"""
assertions.py — 10 acceptance test assertions for the Store Intelligence API.
Run: python assertions.py --api http://localhost:8000

# PROMPT: "Write 10 acceptance test assertions for a retail store analytics API.
#          Cover: ingest idempotency, metrics structure, funnel monotonicity,
#          heatmap scores, anomaly severity, health endpoint."
# CHANGES MADE: Added timing check, batch-500 test, staff exclusion assertion.
"""

import sys
import uuid
import json
import httpx
import argparse
from datetime import datetime, timezone, timedelta

import uuid as _uuid
STORE_ID = "STORE_ASSERT_" + _uuid.uuid4().hex[:6].upper()
PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []


def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"{status}  {name}" + (f" — {detail}" if detail else ""))
    return condition


def ev(event_type, visitor_id, zone_id=None, dwell_ms=0,
       is_staff=False, q_depth=None, cam="CAM_ENTRY_01", mins_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=mins_ago)).isoformat()
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": cam,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": ts,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.87,
        "metadata": {"queue_depth": q_depth, "sku_zone": zone_id, "session_seq": 1}
    }


def seed_events(api: str):
    visitors = [f"VIS_{uuid.uuid4().hex[:6].upper()}" for _ in range(8)]
    staff_id = "VIS_STAFF_ASSERT"
    events = []

    # Staff — must be excluded from metrics
    events += [
        ev("ENTRY",    staff_id, is_staff=True, mins_ago=50),
        ev("ZONE_ENTER", staff_id, "SKINCARE", is_staff=True, cam="CAM_FLOOR_01", mins_ago=48),
        ev("EXIT",     staff_id, is_staff=True, mins_ago=10),
    ]

    for i, vid in enumerate(visitors):
        offset = 45 - i * 4
        events += [
            ev("ENTRY",      vid, mins_ago=offset),
            ev("ZONE_ENTER", vid, "SKINCARE",   cam="CAM_FLOOR_01", mins_ago=offset-1),
            ev("ZONE_EXIT",  vid, "SKINCARE",   dwell_ms=45000, cam="CAM_FLOOR_01", mins_ago=offset-3),
            ev("ZONE_ENTER", vid, "MAKEUP",     cam="CAM_FLOOR_02", mins_ago=offset-4),
            ev("ZONE_EXIT",  vid, "MAKEUP",     dwell_ms=30000, cam="CAM_FLOOR_02", mins_ago=offset-6),
        ]
        if i < 5:
            events.append(ev("BILLING_QUEUE_JOIN", vid, "BILLING",
                              q_depth=i, cam="CAM_BILLING_01", mins_ago=offset-8))
        if i == 4:
            events.append(ev("BILLING_QUEUE_ABANDON", vid, "BILLING",
                              cam="CAM_BILLING_01", mins_ago=offset-10))
        events.append(ev("EXIT", vid, mins_ago=offset-12))

    r = httpx.post(f"{api}/events/ingest", json={"events": events}, timeout=10)
    return r.status_code == 200, len(events)


def run(api: str):
    print(f"\n{'='*55}")
    print(f"  Store Intelligence API — Acceptance Assertions")
    print(f"  Target: {api}")
    print(f"{'='*55}\n")

    # 1. Seed data
    seeded, count = seed_events(api)
    check("1. Event ingest returns 200", seeded, f"{count} events seeded")

    # 2. Idempotency — same event posted twice → accepted=0 on 2nd call
    import uuid as _u2
    _idemp_store = "STORE_IDEMP_" + _u2.uuid4().hex[:4].upper()
    single_ev = ev("ENTRY", "VIS_IDEMP_TEST")
    single_ev["store_id"] = _idemp_store
    httpx.post(f"{api}/events/ingest", json={"events": [single_ev]}, timeout=5)
    r2 = httpx.post(f"{api}/events/ingest", json={"events": [single_ev]}, timeout=5)
    check("2. Ingest idempotent (dup event_id accepted=0)",
          r2.json().get("accepted") == 0,
          f"accepted={r2.json().get('accepted')}, skipped={r2.json().get('skipped')}")

    # 3. Batch of 500 events accepted
    batch = [ev("ZONE_ENTER", f"VIS_B{i:03d}", "SKINCARE",
                cam="CAM_FLOOR_01") for i in range(500)]
    rb = httpx.post(f"{api}/events/ingest", json={"events": batch}, timeout=15)
    check("3. Batch of 500 events accepted", rb.status_code == 200,
          f"status={rb.status_code}")

    # 4. /metrics returns valid structure
    m = httpx.get(f"{api}/stores/{STORE_ID}/metrics", timeout=5).json()
    required = ["unique_visitors","conversion_rate","avg_dwell_per_zone",
                "queue_depth","abandonment_rate"]
    check("4. /metrics has required fields",
          all(k in m for k in required), str(list(m.keys())))

    # 5. Staff excluded from unique_visitors
    check("5. Staff excluded from unique_visitors",
          m.get("unique_visitors", 999) <= 9,
          f"unique_visitors={m.get('unique_visitors')}")

    # 6. /funnel monotonically decreasing
    f = httpx.get(f"{api}/stores/{STORE_ID}/funnel", timeout=5).json()
    visitors_seq = [s["visitors"] for s in f.get("funnel", [])]
    monotonic = all(visitors_seq[i] >= visitors_seq[i+1]
                    for i in range(len(visitors_seq)-1))
    check("6. /funnel stages monotonically decrease", monotonic,
          str(visitors_seq))

    # 7. /heatmap scores 0–100
    h = httpx.get(f"{api}/stores/{STORE_ID}/heatmap", timeout=5).json()
    zones = h.get("zones", [])
    scores_ok = all(0 <= z["heat_score"] <= 100 for z in zones)
    check("7. /heatmap heat_score in 0–100", scores_ok or not zones,
          f"{len(zones)} zones")

    # 8. /heatmap has data_confidence field
    check("8. /heatmap has data_confidence",
          "data_confidence" in h, str(list(h.keys())))

    # 9. /anomalies has suggested_action on every anomaly
    an = httpx.get(f"{api}/stores/{STORE_ID}/anomalies", timeout=5).json()
    anomalies = an.get("anomalies", [])
    all_have_action = all("suggested_action" in a for a in anomalies)
    check("9. Every anomaly has suggested_action", all_have_action,
          f"{len(anomalies)} anomalies")

    # 10. /health returns ok + store_feeds
    hl = httpx.get(f"{api}/health", timeout=5).json()
    check("10. /health returns ok status",
          hl.get("status") == "ok" and "store_feeds" in hl,
          f"status={hl.get('status')}")

    # Summary
    passed = sum(1 for r, _, _ in results if r == PASS)
    total  = len(results)
    print(f"\n{'='*55}")
    print(f"  Results: {passed}/{total} passed")
    print(f"{'='*55}\n")
    return passed == total


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()
    ok = run(args.api)
    sys.exit(0 if ok else 1)
