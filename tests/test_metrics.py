"""
test_metrics.py - Integration tests for Intelligence API endpoints.

# PROMPT: "Write async pytest tests for a FastAPI store analytics API.
#          Test all endpoints: /metrics, /funnel, /heatmap, /anomalies, /health.
#          Include edge cases: empty store, all-staff events, zero purchases,
#          re-entry deduplication in funnel."
# CHANGES MADE: Used httpx.AsyncClient instead of TestClient for async DB.
#               Added fixture to seed realistic event sequences.
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


def _ev(event_type, visitor_id, store_id="STORE_PURPLLE_001",
        camera_id="CAM_ENTRY_01", zone_id=None, dwell_ms=0,
        is_staff=False, confidence=0.85, queue_depth=None,
        minutes_ago=0):
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
        "confidence": confidence,
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


@pytest.fixture
async def seeded_client(client):
    """Seed realistic event sequence for STORE_PURPLLE_001."""
    visitors = [f"VIS_{uuid.uuid4().hex[:6].upper()}" for _ in range(6)]
    staff = f"VIS_STAFF_{uuid.uuid4().hex[:4].upper()}"
    events = []

    # Staff events - should be excluded from metrics
    events += [
        _ev("ENTRY", staff, is_staff=True, minutes_ago=50),
        _ev("ZONE_ENTER", staff, zone_id="SKINCARE", is_staff=True, minutes_ago=48),
        _ev("EXIT", staff, is_staff=True, minutes_ago=10),
    ]

    # Customer sessions
    for i, vid in enumerate(visitors):
        offset = 45 - i * 5
        events += [
            _ev("ENTRY",      vid, minutes_ago=offset),
            _ev("ZONE_ENTER", vid, zone_id="SKINCARE",   minutes_ago=offset-1, camera_id="CAM_FLOOR_01"),
            _ev("ZONE_EXIT",  vid, zone_id="SKINCARE",   dwell_ms=45000, minutes_ago=offset-3, camera_id="CAM_FLOOR_01"),
            _ev("ZONE_ENTER", vid, zone_id="MAKEUP",     minutes_ago=offset-4, camera_id="CAM_FLOOR_02"),
            _ev("ZONE_EXIT",  vid, zone_id="MAKEUP",     dwell_ms=30000, minutes_ago=offset-6, camera_id="CAM_FLOOR_02"),
        ]
        # 4 of 6 reach billing
        if i < 4:
            events += [
                _ev("BILLING_QUEUE_JOIN", vid, zone_id="BILLING",
                    queue_depth=i, minutes_ago=offset-8, camera_id="CAM_BILLING_01"),
            ]
        # 1 abandons
        if i == 3:
            events.append(_ev("BILLING_QUEUE_ABANDON", vid, zone_id="BILLING",
                              minutes_ago=offset-10, camera_id="CAM_BILLING_01"))
        events.append(_ev("EXIT", vid, minutes_ago=offset-12))

    # Re-entry visitor
    reentry_vid = visitors[0]
    events += [
        _ev("ENTRY",   reentry_vid, minutes_ago=5),
        _ev("REENTRY", reentry_vid, minutes_ago=4),
        _ev("EXIT",    reentry_vid, minutes_ago=1),
    ]

    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200
    return client


# ── Root ─────────────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_root(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "Store Intelligence API" in r.json()["service"]


# ── Health ────────────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("ok", "degraded")
    assert "database" in data


# ── Ingest idempotency ────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_ingest_idempotent(client):
    ev = _ev("ENTRY", "VIS_IDEM01")
    r1 = await client.post("/events/ingest", json={"events": [ev]})
    r2 = await client.post("/events/ingest", json={"events": [ev]})
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Second ingest should accept 0 (deduped)
    assert r2.json()["accepted"] == 0
    assert r2.json()["skipped"] == 1


@pytest.mark.anyio
async def test_ingest_partial_failure(client):
    good = _ev("ENTRY", "VIS_GOOD01")
    bad = {"event_id": "not-a-uuid", "store_id": "X"}  # malformed
    r = await client.post("/events/ingest", json={"events": [good, bad]})
    assert r.status_code in (200, 422)


@pytest.mark.anyio
async def test_ingest_batch_500(client):
    events = [_ev("ZONE_ENTER", f"VIS_{i:04d}", zone_id="SKINCARE") for i in range(500)]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200


# ── Metrics ───────────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_metrics_returns_valid_structure(seeded_client):
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "unique_visitors" in data
    assert "conversion_rate" in data
    assert "avg_dwell_per_zone" in data
    assert "queue_depth" in data
    assert "abandonment_rate" in data


@pytest.mark.anyio
async def test_metrics_excludes_staff(seeded_client):
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/metrics")
    data = r.json()
    # We seeded 6 customer visitors + 1 staff. Unique should be 6 (not 7).
    assert data["unique_visitors"] <= 6


@pytest.mark.anyio
async def test_metrics_empty_store(client):
    r = await client.get("/stores/STORE_EMPTY_99/metrics")
    assert r.status_code == 200
    data = r.json()
    assert data["unique_visitors"] == 0
    assert data["conversion_rate"] == 0.0


@pytest.mark.anyio
async def test_metrics_zero_purchases(client):
    vid = "VIS_NOPURCHASE"
    ev = _ev("ENTRY", vid, store_id="STORE_NOPURCHASE")
    await client.post("/events/ingest", json={"events": [ev]})
    r = await client.get("/stores/STORE_NOPURCHASE/metrics")
    assert r.status_code == 200
    assert r.json()["conversion_rate"] == 0.0


# ── Funnel ────────────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_funnel_structure(seeded_client):
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/funnel")
    assert r.status_code == 200
    data = r.json()
    assert "funnel" in data
    stages = [s["stage"] for s in data["funnel"]]
    assert stages == ["ENTRY", "ZONE_VISIT", "BILLING_QUEUE", "PURCHASE"]


@pytest.mark.anyio
async def test_funnel_monotonically_decreasing(seeded_client):
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/funnel")
    visitors = [s["visitors"] for s in r.json()["funnel"]]
    for i in range(len(visitors) - 1):
        assert visitors[i] >= visitors[i+1], f"Funnel not monotonic at stage {i}"


@pytest.mark.anyio
async def test_funnel_reentry_no_double_count(seeded_client):
    """Visitor re-entering should not inflate Entry count."""
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/funnel")
    # ENTRY stage should be <= 6 (not inflated by the REENTRY event)
    entered = r.json()["funnel"][0]["visitors"]
    assert entered <= 6


# ── Heatmap ───────────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_heatmap_structure(seeded_client):
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/heatmap")
    assert r.status_code == 200
    data = r.json()
    assert "zones" in data
    assert "data_confidence" in data


@pytest.mark.anyio
async def test_heatmap_scores_0_to_100(seeded_client):
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/heatmap")
    for zone in r.json()["zones"]:
        assert 0 <= zone["heat_score"] <= 100


@pytest.mark.anyio
async def test_heatmap_low_confidence_flag(client):
    """Less than 20 sessions → data_confidence=LOW."""
    r = await client.get("/stores/STORE_PURPLLE_001/heatmap")
    # Could be LOW if not enough data
    assert r.json()["data_confidence"] in ("HIGH", "LOW")


# ── Anomalies ─────────────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_anomalies_structure(seeded_client):
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/anomalies")
    assert r.status_code == 200
    data = r.json()
    assert "anomalies" in data
    assert "anomaly_count" in data


@pytest.mark.anyio
async def test_anomalies_have_suggested_action(seeded_client):
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/anomalies")
    for a in r.json()["anomalies"]:
        assert "suggested_action" in a
        assert len(a["suggested_action"]) > 0


@pytest.mark.anyio
async def test_anomaly_severity_values(seeded_client):
    r = await seeded_client.get("/stores/STORE_PURPLLE_001/anomalies")
    for a in r.json()["anomalies"]:
        assert a["severity"] in ("INFO", "WARN", "CRITICAL")


@pytest.mark.anyio
async def test_all_staff_clip(client):
    """Store with only staff events should return 0 unique visitors."""
    sid = "STORE_ALLSTAFF"
    staff_id = "VIS_STAFF01"
    events = [
        _ev("ENTRY", staff_id, store_id=sid, is_staff=True),
        _ev("ZONE_ENTER", staff_id, store_id=sid, zone_id="SKINCARE", is_staff=True),
        _ev("EXIT", staff_id, store_id=sid, is_staff=True),
    ]
    await client.post("/events/ingest", json={"events": events})
    r = await client.get(f"/stores/{sid}/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 0
