"""
feed_api.py - Feed events.jsonl into the Store Intelligence API in batches.
Run: python pipeline/feed_api.py --events events.jsonl --api http://localhost:8000
"""

import json, httpx, argparse, time, sys


def feed(events_file: str, api: str, batch_size: int = 200):
    with open(events_file) as f:
        events = [json.loads(l) for l in f if l.strip()]

    print(f"[feed] {len(events)} events ? {api} (batch={batch_size})")
    total_accepted = 0

    for i in range(0, len(events), batch_size):
        batch = events[i:i+batch_size]
        try:
            r = httpx.post(f"{api}/events/ingest",
                           json={"events": batch}, timeout=30)
            d = r.json()
            total_accepted += d.get("accepted", 0)
            print(f"  Batch {i//batch_size+1}: accepted={d.get('accepted')} "
                  f"skipped={d.get('skipped')} rejected={d.get('rejected')}")
        except Exception as e:
            print(f"  Batch {i//batch_size+1}: ERROR {e}")
        time.sleep(0.1)

    print(f"\n? Done - {total_accepted}/{len(events)} events accepted")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--events",     default="events.jsonl")
    p.add_argument("--api",        default="http://localhost:8000")
    p.add_argument("--batch-size", type=int, default=200)
    args = p.parse_args()
    feed(args.events, args.api, args.batch_size)
