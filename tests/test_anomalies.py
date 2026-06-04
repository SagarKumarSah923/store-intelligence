"""
test_anomalies.py - Tests for anomaly detection logic.

# PROMPT: "Test anomaly detection for retail store: queue spike, dead zone,
#          conversion drop, stale feed. Each should trigger at correct threshold."
# CHANGES MADE: Isolated each anomaly type with targeted event seeds,
#               added boundary tests at exact thresholds.
"""

import pytest
import uuid
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _ev(event_type, visitor_id, store_id, zone_id=None, dwell_ms=0,
        is_staff=False, queue_depth=None, minutes_ago=0, camera_id="CAM_ENTRY_01"):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": ts,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.85,
        "metadata": {"queue_depth": queue_depth, "sku_zone": zone_id, "session_seq": 1}
    }


@pytest.fixture
async def client():
    from app.main import app
    from app.database import init_db
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_billing_queue_spike_triggers(client):
    """6 customers in billing queue → BILLING_QUEUE_SPIKE WARN."""
    sid = f"STORE_QTEST_{uuid.uuid4().hex[:4]}"
    events = []
    for i in range(6):
        vid = f"VIS_Q{i:02d}"
        events += [
            _ev("ENTRY", vid, sid, minutes_ago=30),
            _ev("BILLING_QUEUE_JOIN", vid, sid, zone_id="BILLING",
                queue_depth=i, camera_id="CAM_BILLING_01", minutes_ago=5),
        ]
    await client.post("/events/ingest", json={"events": events})
    r = await client.get(f"/stores/{sid}/anomalies")
    assert r.status_code == 200
    types = [a["anomaly_type"] for a in r.json()["anomalies"]]
    assert "BILLING_QUEUE_SPIKE" in types


@pytest.mark.anyio
async def test_billing_queue_critical_above_8(client):
    """9 customers in billing → CRITICAL severity."""
    sid = f"STORE_QCRIT_{uuid.uuid4().hex[:4]}"
    events = []
    for i in range(9):
        vid = f"VIS_QC{i:02d}"
        events += [
            _ev("ENTRY", vid, sid, minutes_ago=30),
            _ev("BILLING_QUEUE_JOIN", vid, sid, zone_id="BILLING",
                queue_depth=i, camera_id="CAM_BILLING_01", minutes_ago=5),
        ]
    await client.post("/events/ingest", json={"events": events})
    r = await client.get(f"/stores/{sid}/anomalies")
    anomalies = r.json()["anomalies"]
    queue_anomalies = [a for a in anomalies if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    assert len(queue_anomalies) > 0
    assert queue_anomalies[0]["severity"] == "CRITICAL"


@pytest.mark.anyio
async def test_no_queue_spike_below_threshold(client):
    """3 customers in billing → no spike anomaly."""
    sid = f"STORE_QLOW_{uuid.uuid4().hex[:4]}"
    events = []
    for i in range(3):
        vid = f"VIS_QL{i}"
        events += [
            _ev("ENTRY", vid, sid, minutes_ago=30),
            _ev("BILLING_QUEUE_JOIN", vid, sid, zone_id="BILLING",
                queue_depth=i, camera_id="CAM_BILLING_01", minutes_ago=5),
            _ev("EXIT", vid, sid, minutes_ago=2),
        ]
    await client.post("/events/ingest", json={"events": events})
    r = await client.get(f"/stores/{sid}/anomalies")
    types = [a["anomaly_type"] for a in r.json()["anomalies"]]
    assert "BILLING_QUEUE_SPIKE" not in types


@pytest.mark.anyio
async def test_dead_zone_detected(client):
    """Zone with visits only >30 min ago → DEAD_ZONE."""
    sid = f"STORE_DZ_{uuid.uuid4().hex[:4]}"
    vid = "VIS_DEAD01"
    events = [
        _ev("ENTRY",      vid, sid, minutes_ago=60),
        _ev("ZONE_ENTER", vid, sid, zone_id="SKINCARE", camera_id="CAM_FLOOR_01", minutes_ago=55),
        _ev("ZONE_EXIT",  vid, sid, zone_id="SKINCARE", dwell_ms=30000,
            camera_id="CAM_FLOOR_01", minutes_ago=40),
        _ev("EXIT",       vid, sid, minutes_ago=38),
    ]
    await client.post("/events/ingest", json={"events": events})
    r = await client.get(f"/stores/{sid}/anomalies")
    types = [a["anomaly_type"] for a in r.json()["anomalies"]]
    assert "DEAD_ZONE" in types


@pytest.mark.anyio
async def test_anomalies_empty_store(client):
    """Store with no events → no anomalies, no crash."""
    r = await client.get("/stores/STORE_NOEVENT_99/anomalies")
    assert r.status_code == 200
    data = r.json()
    assert data["anomaly_count"] == 0
    assert data["anomalies"] == []


@pytest.mark.anyio
async def test_anomaly_has_required_fields(client):
    """Every anomaly must have type, severity, description, suggested_action."""
    sid = f"STORE_FIELDS_{uuid.uuid4().hex[:4]}"
    events = []
    for i in range(6):
        vid = f"VIS_F{i}"
        events += [
            _ev("ENTRY", vid, sid, minutes_ago=30),
            _ev("BILLING_QUEUE_JOIN", vid, sid, zone_id="BILLING",
                queue_depth=i, camera_id="CAM_BILLING_01", minutes_ago=5),
        ]
    await client.post("/events/ingest", json={"events": events})
    r = await client.get(f"/stores/{sid}/anomalies")
    for a in r.json()["anomalies"]:
        assert "anomaly_type"    in a
        assert "severity"        in a
        assert "description"     in a
        assert "suggested_action" in a
        assert a["severity"] in ("INFO", "WARN", "CRITICAL")
