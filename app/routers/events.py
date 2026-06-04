"""
POST /events/ingest - idempotent batch ingestion of schema variants.
Supports official Purplle schema and legacy uppercase event fixtures.
"""

from fastapi import APIRouter
from app.database import get_db
from app.logger import get_logger
import hashlib
import uuid

router = APIRouter()
logger = get_logger(__name__)

EVENT_TYPE_MAP = {
    "ENTRY": "entry",
    "EXIT": "exit",
    "REENTRY": "reentry",
    "ZONE_ENTER": "zone_entered",
    "ZONE_EXIT": "zone_exited",
    "BILLING_QUEUE_JOIN": "queue_completed",
    "BILLING_QUEUE_ABANDON": "queue_abandoned",
}


def _canonical_event_type(event_type: str) -> str:
    if not event_type:
        return ""
    et = event_type.strip()
    if et in EVENT_TYPE_MAP:
        return EVENT_TYPE_MAP[et]
    return et.lower()


def _store_pairs(store_id: str, store_code: str) -> tuple[str, str]:
    sc = store_code or ""
    sid = store_id or ""
    if not sc and sid:
        if sid.startswith("STORE_"):
            sc = sid
            sid = sid.replace("STORE_", "ST", 1)
        elif sid.startswith("ST"):
            sc = sid.replace("ST", "STORE_", 1)
    elif not sid and sc:
        if sc.startswith("STORE_"):
            sid = sc.replace("STORE_", "ST", 1)
        elif sc.startswith("ST"):
            sid = sc
            sc = sc.replace("ST", "STORE_", 1)
    return sid, sc


def _normalize_track_id(visitor_id: str, track_id):
    if track_id is not None:
        try:
            return int(track_id)
        except (ValueError, TypeError):
            return 0
    if not visitor_id:
        return 0
    digest = hashlib.md5(visitor_id.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _dedup_key(ev: dict) -> str:
    et = _canonical_event_type(ev.get("event_type", ""))
    ts = ev.get("event_timestamp") or ev.get("event_time") or ev.get("timestamp", "")
    token = ev.get("queue_event_id") or ev.get("id_token") or ev.get("visitor_id") or str(ev.get("track_id", ""))
    return ev.get("queue_event_id") or f"{et}_{token}_{ts}"


@router.post("/ingest")
async def ingest_events(payload: dict):
    events = payload.get("events", [])
    if len(events) > 500:
        events = events[:500]

    accepted, skipped, rejected = 0, 0, []

    async with await get_db() as db:
        all_keys = [_dedup_key(e) for e in events]
        placeholders = ",".join("?" * len(all_keys)) if all_keys else "''"
        existing = set()
        for tbl in ("entry_exit_events", "zone_events", "queue_events"):
            try:
                async with db.execute(
                    f"SELECT dedup_key FROM {tbl} WHERE dedup_key IN ({placeholders})",
                    all_keys
                ) as cur:
                    existing.update(r[0] for r in await cur.fetchall())
            except Exception:
                pass

        for ev in events:
            dk = _dedup_key(ev)
            if dk in existing:
                skipped += 1
                continue
            et = _canonical_event_type(ev.get("event_type", ""))
            store_id, store_code = _store_pairs(ev.get("store_id"), ev.get("store_code"))
            camera_id = ev.get("camera_id", "")
            timestamp = ev.get("event_timestamp") or ev.get("event_time") or ev.get("timestamp", "")

            try:
                if et in ("entry", "exit", "reentry"):
                    await db.execute("""
                        INSERT OR IGNORE INTO entry_exit_events
                        (dedup_key,event_type,id_token,store_code,store_id,camera_id,
                         event_timestamp,is_staff,gender_pred,age_pred,age_bucket,
                         is_face_hidden,group_id,group_size)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (dk, et,
                          ev.get("id_token") or ev.get("visitor_id", ""),
                          store_code, store_id,
                          camera_id, timestamp,
                          int(ev.get("is_staff", False)),
                          ev.get("gender_pred"), ev.get("age_pred"),
                          ev.get("age_bucket"),
                          int(ev.get("is_face_hidden", False)),
                          ev.get("group_id"), ev.get("group_size")))
                    accepted += 1
                elif et in ("zone_entered", "zone_exited"):
                    await db.execute("""
                        INSERT OR IGNORE INTO zone_events
                        (dedup_key,event_type,track_id,store_id,camera_id,zone_id,
                         zone_name,zone_type,is_revenue_zone,event_time,
                         zone_hotspot_x,zone_hotspot_y,gender,age,age_bucket)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (dk, et,
                          _normalize_track_id(ev.get("visitor_id", ""), ev.get("track_id")),
                          store_id,
                          camera_id, ev.get("zone_id", ""),
                          ev.get("zone_name") or ev.get("zone_id", ""),
                          ev.get("zone_type", "SHELF"),
                          ev.get("is_revenue_zone", "Yes"),
                          timestamp,
                          ev.get("zone_hotspot_x"), ev.get("zone_hotspot_y"),
                          ev.get("gender"), ev.get("age"), ev.get("age_bucket")))
                    accepted += 1
                elif et in ("queue_completed", "queue_abandoned"):
                    qeid = ev.get("queue_event_id") or str(uuid.uuid4())
                    queue_join_ts = ev.get("queue_join_ts") or timestamp
                    queue_served_ts = ev.get("queue_served_ts") or (timestamp if et == "queue_completed" else None)
                    queue_exit_ts = ev.get("queue_exit_ts") or timestamp
                    await db.execute("""
                        INSERT OR IGNORE INTO queue_events
                        (queue_event_id,event_type,track_id,store_id,camera_id,
                         zone_id,zone_name,queue_join_ts,queue_served_ts,
                         queue_exit_ts,wait_seconds,queue_position_at_join,
                         abandoned,zone_hotspot_x,zone_hotspot_y,gender,age,age_bucket)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (qeid, et,
                          _normalize_track_id(ev.get("visitor_id", ""), ev.get("track_id")),
                          store_id, camera_id,
                          ev.get("zone_id", ""),
                          ev.get("zone_name") or ev.get("zone_id", ""),
                          queue_join_ts,
                          queue_served_ts,
                          queue_exit_ts,
                          ev.get("wait_seconds"),
                          int(ev.get("queue_position_at_join") or ev.get("queue_depth") or 0),
                          int(bool(ev.get("abandoned", False) or et == "queue_abandoned")),
                          ev.get("zone_hotspot_x"), ev.get("zone_hotspot_y"),
                          ev.get("gender"), ev.get("age"), ev.get("age_bucket")))
                    accepted += 1
                else:
                    rejected.append({"event": ev.get("event_type"), "reason": "Unknown event_type"})
            except Exception as e:
                rejected.append({"event": ev.get("event_type", ""), "reason": str(e)})

        await db.commit()

    logger.info(f"event=ingest accepted={accepted} skipped={skipped} rejected={len(rejected)}")
    return {"accepted": accepted, "skipped": skipped,
            "rejected": len(rejected), "rejected_details": rejected}
