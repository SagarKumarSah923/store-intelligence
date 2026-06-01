"""
POST /events/ingest — idempotent batch ingestion.
Accepts up to 500 events, validates schema, deduplicates by event_id.
Returns partial success on malformed events.

# PROMPT: "Write idempotent FastAPI ingest endpoint. Same event_id posted
#          twice must count as accepted=0 on second call, not accepted=1."
# CHANGES MADE: Check existing event_ids via SELECT before INSERT so
#               duplicates are reported as skipped, not accepted.
"""

from fastapi import APIRouter
from app.models import IngestRequest
from app.database import get_db
from app.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.post("/ingest")
async def ingest_events(payload: IngestRequest):
    accepted, skipped, rejected = [], [], []

    incoming_ids = [ev.event_id for ev in payload.events]

    async with await get_db() as db:
        # Fetch already-stored event_ids in one query
        placeholders = ",".join("?" * len(incoming_ids))
        async with db.execute(
            f"SELECT event_id FROM events WHERE event_id IN ({placeholders})",
            incoming_ids
        ) as cur:
            existing = {row[0] for row in await cur.fetchall()}

        for ev in payload.events:
            # Already exists → idempotent skip
            if ev.event_id in existing:
                skipped.append(ev.event_id)
                continue
            try:
                await db.execute("""
                    INSERT INTO events
                    (event_id, store_id, camera_id, visitor_id, event_type,
                     timestamp, zone_id, dwell_ms, is_staff, confidence,
                     queue_depth, sku_zone, session_seq)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    ev.event_id, ev.store_id, ev.camera_id, ev.visitor_id,
                    ev.event_type, ev.timestamp, ev.zone_id, ev.dwell_ms,
                    int(ev.is_staff), ev.confidence,
                    ev.metadata.queue_depth, ev.metadata.sku_zone,
                    ev.metadata.session_seq
                ))
                accepted.append(ev.event_id)
            except Exception as e:
                rejected.append({"event_id": ev.event_id, "reason": str(e)})

        await db.commit()

    logger.info(
        f"event=ingest accepted={len(accepted)} "
        f"skipped={len(skipped)} rejected={len(rejected)}"
    )
    return {
        "accepted":        len(accepted),
        "skipped":         len(skipped),
        "rejected":        len(rejected),
        "rejected_details": rejected
    }
