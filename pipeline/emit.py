"""
emit.py — Event schema validation + JSONL file emitter.
Ensures all events conform to required schema before writing.
"""

import json, uuid
from datetime import datetime, timezone
from pathlib import Path

VALID_EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
}


def validate_event(event: dict) -> tuple[bool, str]:
    required = ["event_id","store_id","camera_id","visitor_id",
                "event_type","timestamp","zone_id","dwell_ms",
                "is_staff","confidence","metadata"]
    for f in required:
        if f not in event:
            return False, f"Missing field: {f}"
    if event["event_type"] not in VALID_EVENT_TYPES:
        return False, f"Invalid event_type: {event['event_type']}"
    try:
        datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
    except Exception:
        return False, f"Invalid timestamp: {event['timestamp']}"
    if not (0.0 <= event["confidence"] <= 1.0):
        return False, f"Confidence out of range: {event['confidence']}"
    if not isinstance(event["dwell_ms"], int) or event["dwell_ms"] < 0:
        return False, f"Invalid dwell_ms: {event['dwell_ms']}"
    return True, "ok"


class EventEmitter:
    def __init__(self, output_path: str):
        self.output_path = output_path
        self.count = 0
        self._seen: set = set()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        open(output_path, "w").close()

    def emit(self, event: dict):
        eid = event.get("event_id")
        if eid in self._seen:
            return
        self._seen.add(eid)
        valid, reason = validate_event(event)
        if not valid:
            print(f"[emitter] INVALID skipped: {reason}")
            return
        with open(self.output_path, "a") as f:
            f.write(json.dumps(event) + "\n")
        self.count += 1

    def emit_batch(self, events: list):
        for ev in events:
            self.emit(ev)


def load_events(path: str) -> list:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


def generate_sample_events(store_id: str = "STORE_PURPLLE_001", n: int = 50) -> list:
    """Generate realistic sample events matching real footage patterns."""
    import random
    from datetime import timedelta
    if n == 0:
        return []
    rng = random.Random(42)
    base = datetime(2026, 4, 10, 20, 8, 0, tzinfo=timezone.utc)
    events, t, seq = [], base, 1
    visitors = [f"VIS_{uuid.uuid4().hex[:6].upper()}" for _ in range(max(1, n // 8))]
    zones = ["SKINCARE","CLEAN_BEAUTY","MAKEUP","LIPS_EYES","BILLING","ACCESSORIES"]
    cam_map = {"SKINCARE":"CAM_FLOOR_01","CLEAN_BEAUTY":"CAM_FLOOR_01",
               "MAKEUP":"CAM_FLOOR_02","LIPS_EYES":"CAM_FLOOR_02",
               "BILLING":"CAM_BILLING_01","ACCESSORIES":"CAM_BILLING_01"}

    for vid in visitors:
        is_staff = (vid == visitors[0])
        t += timedelta(seconds=rng.randint(10, 30))
        events.append({"event_id":str(uuid.uuid4()),"store_id":store_id,
            "camera_id":"CAM_ENTRY_01","visitor_id":vid,"event_type":"ENTRY",
            "timestamp":t.isoformat(),"zone_id":None,"dwell_ms":0,
            "is_staff":is_staff,"confidence":round(rng.uniform(0.7,0.95),3),
            "metadata":{"queue_depth":None,"sku_zone":None,"session_seq":seq}})
        seq += 1
        for zone in rng.sample(zones[:4], k=rng.randint(1, 3)):
            t += timedelta(seconds=rng.randint(15, 60))
            cam = cam_map[zone]
            dwell = rng.randint(20000, 90000)
            events.append({"event_id":str(uuid.uuid4()),"store_id":store_id,
                "camera_id":cam,"visitor_id":vid,"event_type":"ZONE_ENTER",
                "timestamp":t.isoformat(),"zone_id":zone,"dwell_ms":0,
                "is_staff":is_staff,"confidence":round(rng.uniform(0.6,0.95),3),
                "metadata":{"queue_depth":None,"sku_zone":zone,"session_seq":seq}})
            seq += 1
            t += timedelta(milliseconds=dwell)
            events.append({"event_id":str(uuid.uuid4()),"store_id":store_id,
                "camera_id":cam,"visitor_id":vid,"event_type":"ZONE_EXIT",
                "timestamp":t.isoformat(),"zone_id":zone,"dwell_ms":dwell,
                "is_staff":is_staff,"confidence":round(rng.uniform(0.6,0.95),3),
                "metadata":{"queue_depth":None,"sku_zone":zone,"session_seq":seq}})
            seq += 1
        if not is_staff and rng.random() > 0.4:
            t += timedelta(seconds=20)
            qd = rng.randint(0, 3)
            events.append({"event_id":str(uuid.uuid4()),"store_id":store_id,
                "camera_id":"CAM_BILLING_01","visitor_id":vid,
                "event_type":"BILLING_QUEUE_JOIN","timestamp":t.isoformat(),
                "zone_id":"BILLING","dwell_ms":0,"is_staff":False,
                "confidence":round(rng.uniform(0.7,0.95),3),
                "metadata":{"queue_depth":qd,"sku_zone":None,"session_seq":seq}})
            seq += 1
        t += timedelta(seconds=rng.randint(20, 60))
        events.append({"event_id":str(uuid.uuid4()),"store_id":store_id,
            "camera_id":"CAM_ENTRY_01","visitor_id":vid,"event_type":"EXIT",
            "timestamp":t.isoformat(),"zone_id":None,"dwell_ms":0,
            "is_staff":is_staff,"confidence":round(rng.uniform(0.7,0.95),3),
            "metadata":{"queue_depth":None,"sku_zone":None,"session_seq":seq}})
        seq += 1
    return events
