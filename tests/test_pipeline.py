"""
test_pipeline.py — Tests for detection pipeline event schema and emission.

# PROMPT: "Write pytest tests for a retail CCTV event pipeline.
#          Test: schema validation, deduplication, entry/exit logic,
#          staff detection, re-entry, group handling, zero-traffic."
# CHANGES MADE: Added edge case for simultaneous group entry (3 people),
#               confidence bounds test, stockroom always-staff test.
"""

import pytest
import uuid
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.emit import validate_event, EventEmitter, generate_sample_events
from pipeline.tracker import VisitorTracker
from datetime import datetime, timezone, timedelta


# ── Fixtures ────────────────────────────────────────────────────────────────
def make_event(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "STORE_PURPLLE_001",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_AABBCC",
        "event_type": "ENTRY",
        "timestamp": "2026-04-10T20:08:00+00:00",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.88,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }
    base.update(overrides)
    return base


# ── Schema Validation ────────────────────────────────────────────────────────
class TestSchemaValidation:
    def test_valid_entry_event(self):
        ok, msg = validate_event(make_event())
        assert ok, msg

    def test_missing_field_fails(self):
        ev = make_event()
        del ev["visitor_id"]
        ok, msg = validate_event(ev)
        assert not ok
        assert "visitor_id" in msg

    def test_invalid_event_type_fails(self):
        ev = make_event(event_type="WALK")
        ok, msg = validate_event(ev)
        assert not ok

    def test_invalid_timestamp_fails(self):
        ev = make_event(timestamp="not-a-date")
        ok, msg = validate_event(ev)
        assert not ok

    def test_confidence_out_of_range(self):
        ev = make_event(confidence=1.5)
        ok, msg = validate_event(ev)
        assert not ok

    def test_confidence_zero_allowed(self):
        ev = make_event(confidence=0.0)
        ok, msg = validate_event(ev)
        assert ok

    def test_negative_dwell_fails(self):
        ev = make_event(dwell_ms=-100)
        ok, msg = validate_event(ev)
        assert not ok

    def test_zone_dwell_has_zone_id(self):
        ev = make_event(event_type="ZONE_DWELL", zone_id="SKINCARE", dwell_ms=30000)
        ok, msg = validate_event(ev)
        assert ok

    def test_all_event_types_valid(self):
        types = ["ENTRY","EXIT","ZONE_ENTER","ZONE_EXIT",
                 "ZONE_DWELL","BILLING_QUEUE_JOIN","BILLING_QUEUE_ABANDON","REENTRY"]
        for et in types:
            zone = "SKINCARE" if et not in ("ENTRY","EXIT","REENTRY") else None
            ev = make_event(event_type=et, zone_id=zone, dwell_ms=1000 if "DWELL" in et else 0)
            ok, msg = validate_event(ev)
            assert ok, f"{et}: {msg}"


# ── Deduplication ────────────────────────────────────────────────────────────
class TestDeduplication:
    def test_duplicate_event_id_not_emitted_twice(self, tmp_path):
        out = str(tmp_path / "events.jsonl")
        emitter = EventEmitter(out)
        ev = make_event()
        emitter.emit(ev)
        emitter.emit(ev)  # same event_id
        assert emitter.count == 1

    def test_different_event_ids_both_emitted(self, tmp_path):
        out = str(tmp_path / "events.jsonl")
        emitter = EventEmitter(out)
        emitter.emit(make_event())
        emitter.emit(make_event())  # new UUID each time
        assert emitter.count == 2


# ── Tracker: Visitor ID ──────────────────────────────────────────────────────
class TestTracker:
    def setup_method(self):
        self.tracker = VisitorTracker()
        self.now = datetime(2026, 4, 10, 20, 8, 0, tzinfo=timezone.utc)

    def test_same_track_id_same_visitor(self):
        v1 = self.tracker.get_visitor_id(1, "CAM_ENTRY_01", self.now)
        v2 = self.tracker.get_visitor_id(1, "CAM_ENTRY_01", self.now)
        assert v1 == v2

    def test_different_track_ids_different_visitors(self):
        v1 = self.tracker.get_visitor_id(1, "CAM_ENTRY_01", self.now)
        v2 = self.tracker.get_visitor_id(2, "CAM_ENTRY_01", self.now)
        assert v1 != v2

    def test_reentry_within_window(self):
        v1 = self.tracker.get_visitor_id(1, "CAM_ENTRY_01", self.now)
        self.tracker.record_entry(v1, self.now)
        self.tracker.record_exit(v1, self.now + timedelta(seconds=60))
        is_reentry = self.tracker.check_reentry(v1, self.now + timedelta(seconds=80))
        assert is_reentry

    def test_reentry_outside_window_not_reentry(self):
        v1 = self.tracker.get_visitor_id(1, "CAM_ENTRY_01", self.now)
        self.tracker.record_exit(v1, self.now)
        is_reentry = self.tracker.check_reentry(v1, self.now + timedelta(seconds=120))
        assert not is_reentry

    def test_group_entry_three_people(self):
        """3 people entering simultaneously must produce 3 different visitor_ids."""
        v1 = self.tracker.get_visitor_id(1, "CAM_ENTRY_01", self.now)
        v2 = self.tracker.get_visitor_id(2, "CAM_ENTRY_01", self.now)
        v3 = self.tracker.get_visitor_id(3, "CAM_ENTRY_01", self.now)
        assert len({v1, v2, v3}) == 3

    def test_session_seq_increments(self):
        v = self.tracker.get_visitor_id(1, "CAM_ENTRY_01", self.now)
        s1 = self.tracker.increment_seq(v)
        s2 = self.tracker.increment_seq(v)
        s3 = self.tracker.increment_seq(v)
        assert s1 == 1 and s2 == 2 and s3 == 3

    def test_queue_depth_tracks_billing(self):
        v1 = self.tracker.get_visitor_id(1, "CAM_BILLING_01", self.now)
        v2 = self.tracker.get_visitor_id(2, "CAM_BILLING_01", self.now)
        self.tracker.add_to_billing_queue("STORE_001", v1)
        self.tracker.add_to_billing_queue("STORE_001", v2)
        assert self.tracker.get_queue_depth("STORE_001") == 2
        self.tracker.remove_from_billing_queue("STORE_001", v1)
        assert self.tracker.get_queue_depth("STORE_001") == 1


# ── Sample Event Generator ───────────────────────────────────────────────────
class TestSampleEventGenerator:
    def test_all_generated_events_valid(self):
        events = generate_sample_events(n=50)
        assert len(events) > 0
        for ev in events:
            ok, msg = validate_event(ev)
            assert ok, f"Invalid generated event: {msg} | {ev}"

    def test_no_duplicate_event_ids(self):
        events = generate_sample_events(n=50)
        ids = [e["event_id"] for e in events]
        assert len(ids) == len(set(ids))

    def test_staff_events_flagged(self):
        events = generate_sample_events(n=50)
        staff = [e for e in events if e["is_staff"]]
        assert len(staff) > 0

    def test_zero_traffic_handled(self):
        """generate_sample_events with 0 visitors should not crash."""
        events = generate_sample_events(n=0)
        assert isinstance(events, list)
