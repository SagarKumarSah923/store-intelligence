"""
emit.py - Event schema validation + JSONL emitter.
Updated to match official sample_events.jsonl schema exactly.

Official schema has TWO event families:
  1. Entry/Exit events  ? id_token, store_code, camera_id, event_timestamp, is_staff,
                          gender_pred, age_pred, age_bucket, is_face_hidden, group_id, group_size
  2. Zone/Queue events  ? track_id, store_id, camera_id, zone_id, zone_name, zone_type,
                          is_revenue_zone, event_time, zone_hotspot_x, zone_hotspot_y,
                          gender, age, age_bucket
  3. Queue events       ? queue_event_id, queue_join_ts, queue_served_ts, queue_exit_ts,
                          wait_seconds, queue_position_at_join, abandoned
"""

import json
import uuid
from pathlib import Path
from datetime import datetime

EVENT_TYPE_MAP = {
    "ENTRY": "entry",
    "EXIT": "exit",
    "REENTRY": "reentry",
    "ZONE_ENTER": "zone_entered",
    "ZONE_EXIT": "zone_exited",
    "ZONE_DWELL": "zone_entered",
    "BILLING_QUEUE_JOIN": "queue_completed",
    "BILLING_QUEUE_ABANDON": "queue_abandoned",
}

VALID_EVENT_TYPES = {
    "entry", "exit", "zone_entered", "zone_exited",
    "queue_completed", "queue_abandoned", "reentry"
}

REQUIRED_ENTRY_EXIT = ["event_type", "camera_id", "is_staff"]
REQUIRED_ZONE = ["event_type", "store_id", "camera_id", "zone_id"]
REQUIRED_QUEUE = ["event_type", "store_id", "camera_id", "zone_id"]


def _canonical_event_type(event_type: str) -> str:
    if not event_type:
        return ""
    event_type = event_type.strip()
    if event_type in EVENT_TYPE_MAP:
        return EVENT_TYPE_MAP[event_type]
    return event_type.lower()


def _is_valid_timestamp(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except Exception:
        return False


def validate_event(event: dict) -> tuple:
    if "event_type" not in event:
        return False, "Missing event_type"
    et = _canonical_event_type(event["event_type"])
    if et not in VALID_EVENT_TYPES:
        return False, f"Invalid event_type: {event['event_type']}"

    if et in ("entry", "exit", "reentry"):
        if "visitor_id" not in event and "id_token" not in event:
            return False, "Missing visitor_id or id_token"
        if "store_id" not in event and "store_code" not in event:
            return False, "Missing store_id or store_code"
        for f in REQUIRED_ENTRY_EXIT:
            if f not in event:
                return False, f"Missing field: {f}"
        ts = event.get("event_timestamp") or event.get("timestamp")
        if not ts or not _is_valid_timestamp(ts):
            return False, "Invalid event timestamp"
    elif et in ("zone_entered", "zone_exited"):
        for f in REQUIRED_ZONE:
            if f not in event:
                return False, f"Missing field: {f}"
        ts = event.get("event_time") or event.get("timestamp")
        if not ts or not _is_valid_timestamp(ts):
            return False, "Invalid event time"
    elif et in ("queue_completed", "queue_abandoned"):
        if "queue_event_id" not in event and "event_id" not in event:
            return False, "Missing queue_event_id or event_id"
        for f in REQUIRED_QUEUE:
            if f not in event:
                return False, f"Missing field: {f}"
        ts = event.get("queue_join_ts") or event.get("timestamp")
        if not ts or not _is_valid_timestamp(ts):
            return False, "Invalid queue_join timestamp"

    confidence = event.get("confidence")
    if confidence is not None:
        try:
            if not (0.0 <= float(confidence) <= 1.0):
                return False, "Invalid confidence value"
        except (ValueError, TypeError):
            return False, "Invalid confidence value"

    if "dwell_ms" in event:
        try:
            if int(event["dwell_ms"]) < 0:
                return False, "Invalid dwell_ms"
        except (ValueError, TypeError):
            return False, "Invalid dwell_ms"

    return True, "ok"


class EventEmitter:
    def __init__(self, output_path: str):
        self.output_path = output_path
        self.count = 0
        self._seen: set = set()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        open(output_path, "w").close()

    def emit(self, event: dict):
        dedup_key = event.get("event_id") or event.get("queue_event_id") or \
                    f"{event.get('event_type')}_{event.get('id_token', event.get('track_id', event.get('visitor_id')))}_{event.get('event_timestamp', event.get('event_time', event.get('timestamp')))}"
        if dedup_key in self._seen:
            return
        self._seen.add(dedup_key)
        valid, reason = validate_event(event)
        if not valid:
            print(f"[emitter] INVALID skipped: {reason} | {event.get('event_type')}")
            return
        with open(self.output_path, "a") as f:
            f.write(json.dumps(event) + "\n")
        self.count += 1

    def emit_batch(self, events: list):
        for ev in events:
            self.emit(ev)


def make_entry_event(id_token: str, store_code: str, camera_id: str,
                     event_timestamp: str, is_staff: bool,
                     gender_pred: str = None, age_pred: int = None,
                     age_bucket: str = None, is_face_hidden: bool = False,
                     group_id: str = None, group_size: int = None,
                     event_type: str = "entry") -> dict:
    ev = {
        "event_id":        str(uuid.uuid4()),
        "event_type":      event_type,
        "id_token":        id_token,
        "store_code":      store_code,
        "camera_id":       camera_id,
        "event_timestamp": event_timestamp,
        "timestamp":       event_timestamp,
        "is_staff":        is_staff,
        "gender_pred":     gender_pred,
        "age_pred":        age_pred,
        "age_bucket":      age_bucket,
        "is_face_hidden":  is_face_hidden,
        "group_id":        group_id,
        "group_size":      group_size,
        "confidence":      0.95,
        "metadata":        {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }
    return ev


def make_zone_event(track_id: int, store_id: str, camera_id: str,
                    zone_id: str, zone_name: str, zone_type: str,
                    is_revenue_zone: str, event_time: str,
                    zone_hotspot_x: float = 0.0, zone_hotspot_y: float = 0.0,
                    gender: str = None, age: int = None,
                    age_bucket: str = None,
                    event_type: str = "zone_entered") -> dict:
    return {
        "event_id":        str(uuid.uuid4()),
        "event_type":      event_type,
        "track_id":        track_id,
        "store_id":        store_id,
        "camera_id":       camera_id,
        "zone_id":         zone_id,
        "zone_name":       zone_name,
        "zone_type":       zone_type,
        "is_revenue_zone": is_revenue_zone,
        "event_time":      event_time,
        "timestamp":       event_time,
        "is_staff":        False,
        "zone_hotspot_x":  round(zone_hotspot_x, 1),
        "zone_hotspot_y":  round(zone_hotspot_y, 1),
        "gender":          gender,
        "age":             age,
        "age_bucket":      age_bucket,
        "confidence":      0.95,
        "metadata":        {"queue_depth": None, "sku_zone": zone_id, "session_seq": 1}
    }


def make_queue_event(track_id: int, store_id: str, camera_id: str,
                     zone_id: str, zone_name: str,
                     queue_join_ts: str, queue_served_ts: str,
                     queue_exit_ts: str, wait_seconds: int,
                     queue_position_at_join: int, abandoned: bool,
                     zone_hotspot_x: float = 0.0,
                     zone_hotspot_y: float = 0.0,
                     gender: str = None, age: int = None,
                     age_bucket: str = None) -> dict:
    return {
        "event_id":               str(uuid.uuid4()),
        "queue_event_id":         str(uuid.uuid4()),
        "event_type":             "queue_abandoned" if abandoned else "queue_completed",
        "track_id":               track_id,
        "store_id":               store_id,
        "camera_id":              camera_id,
        "zone_id":                zone_id,
        "zone_name":              zone_name,
        "zone_type":              "BILLING",
        "is_revenue_zone":        "Yes",
        "event_time":             queue_join_ts,
        "timestamp":              queue_join_ts,
        "is_staff":               False,
        "queue_join_ts":          queue_join_ts,
        "queue_served_ts":        queue_served_ts,
        "queue_exit_ts":          queue_exit_ts,
        "wait_seconds":           wait_seconds,
        "queue_position_at_join": queue_position_at_join,
        "abandoned":              abandoned,
        "zone_hotspot_x":         round(zone_hotspot_x, 1),
        "zone_hotspot_y":         round(zone_hotspot_y, 1),
        "gender":                 gender,
        "age":                    age,
        "age_bucket":             age_bucket,
        "confidence":             0.95,
        "metadata":               {"queue_depth": queue_position_at_join, "sku_zone": zone_id, "session_seq": 1}
    }


def generate_sample_events(store_id: str = "ST1008",
                            store_code: str = "store_1008", n: int = 50) -> list:
    import random
    from datetime import timedelta
    if n == 0:
        return []
    rng = random.Random(42)
    base = datetime(2026, 4, 10, 18, 10, 0)
    events = []
    visitor_counter = 60000
    zones = [
        ("PURPLLE_ST1008_Z01",       "Left Shelf - Skincare",     "SHELF",   "Yes", "CAM_FLOOR_01"),
        ("PURPLLE_ST1008_Z02",       "Right Shelf - Makeup",      "SHELF",   "Yes", "CAM_FLOOR_02"),
        ("PURPLLE_ST1008_Z03",       "Makeup Unit / FOH",         "GONDOLA", "Yes", "CAM_FLOOR_01"),
        ("PURPLLE_ST1008_Z_BILLING_01", "Billing Counter Queue",  "BILLING", "Yes", "CAM_BILLING_01"),
    ]
    genders = ["M", "F"]
    num_visitors = max(1, n // 6)

    for i in range(num_visitors):
        visitor_counter += 1
        vid = f"ID_{visitor_counter}"
        is_staff = (i == 0)
        gender = rng.choice(genders)
        age = rng.randint(18, 50)
        from pipeline.tracker import _age_bucket
        abucket = _age_bucket(age)
        t = base + timedelta(seconds=rng.randint(i * 30, i * 30 + 60))

        events.append(make_entry_event(
            id_token=vid, store_code=store_code,
            camera_id="CAM_ENTRY_01",
            event_timestamp=t.isoformat(),
            is_staff=is_staff,
            gender_pred=gender, age_pred=age, age_bucket=abucket,
            is_face_hidden=False, group_id=None, group_size=None
        ))

        if not is_staff:
            for zone_id, zone_name, zone_type, rev, cam in rng.sample(zones[:3], k=rng.randint(1,2)):
                t += timedelta(seconds=rng.randint(10, 40))
                hx = rng.uniform(200, 700)
                hy = rng.uniform(100, 400)
                events.append(make_zone_event(
                    track_id=visitor_counter, store_id=store_id, camera_id=cam,
                    zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                    is_revenue_zone=rev, event_time=t.isoformat(),
                    zone_hotspot_x=hx, zone_hotspot_y=hy,
                    gender=gender, age=age, age_bucket=abucket,
                    event_type="zone_entered"
                ))
                dwell = rng.randint(20, 90)
                t += timedelta(seconds=dwell)
                events.append(make_zone_event(
                    track_id=visitor_counter, store_id=store_id, camera_id=cam,
                    zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                    is_revenue_zone=rev, event_time=t.isoformat(),
                    zone_hotspot_x=hx, zone_hotspot_y=hy,
                    gender=gender, age=age, age_bucket=abucket,
                    event_type="zone_exited"
                ))

            if rng.random() > 0.4:
                join_t = t + timedelta(seconds=rng.randint(5, 20))
                wait = rng.randint(5, 120)
                abandoned = rng.random() < 0.2
                served_t = None if abandoned else (join_t + timedelta(seconds=wait)).isoformat()
                exit_t = (join_t + timedelta(seconds=wait + rng.randint(30, 120))).isoformat()
                events.append(make_queue_event(
                    track_id=visitor_counter, store_id=store_id,
                    camera_id="CAM_BILLING_01",
                    zone_id="PURPLLE_ST1008_Z_BILLING_01",
                    zone_name="Billing Counter Queue",
                    queue_join_ts=join_t.isoformat(),
                    queue_served_ts=served_t,
                    queue_exit_ts=exit_t,
                    wait_seconds=wait,
                    queue_position_at_join=rng.randint(1, 4),
                    abandoned=abandoned,
                    zone_hotspot_x=rng.uniform(550, 650),
                    zone_hotspot_y=rng.uniform(150, 220),
                    gender=gender, age=age, age_bucket=abucket
                ))
                t = join_t + timedelta(seconds=wait + 60)

        t += timedelta(seconds=rng.randint(10, 30))
        events.append(make_entry_event(
            id_token=vid, store_code=store_code,
            camera_id="CAM_ENTRY_01",
            event_timestamp=t.isoformat(),
            is_staff=is_staff,
            gender_pred=gender, age_pred=age, age_bucket=abucket,
            event_type="exit"
        ))

    return events
