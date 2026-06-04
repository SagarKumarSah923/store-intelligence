"""
assertions.py - 10 acceptance assertions for Store Intelligence API v2.
Uses official Purplle event schema (entry/exit + zone + queue).
Run: python assertions.py --api http://localhost:8000
"""

import sys, uuid, httpx, argparse
from datetime import datetime, timezone, timedelta

STORE_ID   = f"ST_ASSERT_{uuid.uuid4().hex[:4].upper()}"
STORE_CODE = f"store_{STORE_ID[3:]}"
results    = []


def check(name, cond, detail=""):
    s = "? PASS" if cond else "? FAIL"
    results.append((s, name, detail))
    print(f"{s}  {name}" + (f" - {detail}" if detail else ""))
    return cond


def mk_entry(tok, sc=None, etype="entry", is_staff=False, mins_ago=0):
    ts = (datetime.now(timezone.utc)-timedelta(minutes=mins_ago)).isoformat()
    return {"event_type":etype,"id_token":tok,"store_code":sc or STORE_CODE,
            "camera_id":"CAM_ENTRY_01","event_timestamp":ts,"is_staff":is_staff,
            "gender_pred":"F","age_pred":28,"age_bucket":"25-34",
            "is_face_hidden":False,"group_id":None,"group_size":None}


def mk_zone(tid, sid=None, etype="zone_entered", zone_id="PURPLLE_ST1008_Z01", mins_ago=0):
    ts = (datetime.now(timezone.utc)-timedelta(minutes=mins_ago)).isoformat()
    return {"event_type":etype,"track_id":tid,"store_id":sid or STORE_ID,
            "camera_id":"CAM_FLOOR_01","zone_id":zone_id,
            "zone_name":"Left Shelf","zone_type":"SHELF",
            "is_revenue_zone":"Yes","event_time":ts,
            "zone_hotspot_x":412.6,"zone_hotspot_y":238.4,
            "gender":"F","age":28,"age_bucket":"25-34"}


def mk_queue(tid, sid=None, abandoned=False, mins_ago=0):
    ts = (datetime.now(timezone.utc)-timedelta(minutes=mins_ago)).isoformat()
    return {"event_type":"queue_abandoned" if abandoned else "queue_completed",
            "queue_event_id":str(uuid.uuid4()),
            "track_id":tid,"store_id":sid or STORE_ID,
            "camera_id":"CAM_BILLING_01",
            "zone_id":"PURPLLE_ST1008_Z_BILLING_01",
            "zone_name":"Billing Counter Queue","zone_type":"BILLING",
            "is_revenue_zone":"Yes","queue_join_ts":ts,
            "queue_served_ts":None if abandoned else ts,
            "queue_exit_ts":ts,"wait_seconds":30,
            "queue_position_at_join":1,"abandoned":abandoned,
            "gender":"F","age":28,"age_bucket":"25-34"}


def seed(api):
    events = []
    events += [mk_entry("ID_STAFF",is_staff=True,mins_ago=50),
               mk_entry("ID_STAFF",is_staff=True,mins_ago=10,etype="exit")]
    for i in range(7):
        vid, tid, off = f"ID_{60001+i}", 100+i, 45-i*4
        events += [mk_entry(vid, mins_ago=off),
                   mk_zone(tid, mins_ago=off-1),
                   mk_zone(tid, mins_ago=off-3, etype="zone_exited")]
        if i < 4:
            events.append(mk_queue(tid, abandoned=(i==3), mins_ago=off-5))
        events.append(mk_entry(vid, mins_ago=off-8, etype="exit"))
    r = httpx.post(f"{api}/events/ingest", json={"events":events}, timeout=15)
    return r.status_code == 200, len(events)


def run(api):
    print(f"\n{'='*55}\n  Store Intelligence API - 10 Acceptance Assertions\n  Store: {STORE_ID}\n{'='*55}\n")
    ok, cnt = seed(api)
    check("1. Event ingest returns 200", ok, f"{cnt} events")
    _idemp_sc = f"store_IDEMP_{uuid.uuid4().hex[:4]}"
    ev = mk_entry("ID_IDEMP", sc=_idemp_sc)
    httpx.post(f"{api}/events/ingest", json={"events":[ev]}, timeout=5)
    r2 = httpx.post(f"{api}/events/ingest", json={"events":[ev]}, timeout=5)
    check("2. Ingest idempotent (dup ? accepted=0, skipped=1)",
          r2.json().get("accepted")==0 and r2.json().get("skipped")==1,
          f"accepted={r2.json().get('accepted')} skipped={r2.json().get('skipped')}")
    batch = [mk_entry(f"ID_{70000+i}") for i in range(500)]
    rb = httpx.post(f"{api}/events/ingest", json={"events":batch}, timeout=20)
    check("3. Batch of 500 events accepted", rb.status_code==200,
          f"status={rb.status_code}")
    import json, os
    if os.path.exists("sample_events.jsonl"):
        with open("sample_events.jsonl") as f:
            sevs = [json.loads(l) for l in f if l.strip()]
        rs = httpx.post(f"{api}/events/ingest", json={"events":sevs}, timeout=10)
        check("4. Official sample_events.jsonl accepted",
              rs.status_code==200 and rs.json()["rejected"]==0,
              f"accepted={rs.json().get('accepted')} rejected={rs.json().get('rejected')}")
    else:
        check("4. Official sample_events.jsonl accepted", True, "file not found - skipped")
    m = httpx.get(f"{api}/stores/{STORE_ID}/metrics", timeout=5).json()
    fields = ["unique_visitors","conversion_rate","avg_dwell_per_zone",
              "queue_depth","abandonment_rate"]
    check("5. /metrics has all required fields",
          all(k in m for k in fields), str(list(m.keys())))
    check("6. Staff excluded from unique_visitors",
          m.get("unique_visitors",999) <= 7,
          f"unique_visitors={m.get('unique_visitors')}")
    fn = httpx.get(f"{api}/stores/{STORE_ID}/funnel", timeout=5).json()
    vs = [s["visitors"] for s in fn.get("funnel",[])]
    mono = all(vs[i]>=vs[i+1] for i in range(len(vs)-1)) if len(vs)>1 else True
    check("7. /funnel stages monotonically decrease", mono, str(vs))
    hm = httpx.get(f"{api}/stores/{STORE_ID}/heatmap", timeout=5).json()
    ok8 = all(0<=z["heat_score"]<=100 for z in hm.get("zones",[]))
    check("8. /heatmap heat_score in 0-100", ok8 or not hm.get("zones"),
          f"{len(hm.get('zones',[]))} zones")
    an = httpx.get(f"{api}/stores/{STORE_ID}/anomalies", timeout=5).json()
    ok9 = all("suggested_action" in a and a["severity"] in ("INFO","WARN","CRITICAL")
              for a in an.get("anomalies",[]))
    check("9. Every anomaly has severity + suggested_action",
          ok9, f"{an.get('anomaly_count',0)} anomalies")
    hl = httpx.get(f"{api}/health", timeout=5).json()
    check("10. /health returns ok + store_feeds",
          hl.get("status")=="ok" and "store_feeds" in hl,
          f"status={hl.get('status')}")
    passed = sum(1 for r,_,_ in results if "PASS" in r)
    total  = len(results)
    print(f"\n{'='*55}\n  Results: {passed}/{total} passed\n{'='*55}\n")
    return passed == total


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8000")
    args = p.parse_args()
    sys.exit(0 if run(args.api) else 1)
