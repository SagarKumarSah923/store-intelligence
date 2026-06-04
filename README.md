# Store Intelligence System
**Purplle Tech Challenge 2026 - Round 2**

AI-powered Store Intelligence System - CCTV-based real-time visitor tracking, anomaly detection & analytics API.

---

## Setup (5 Commands)

```bash
git clone https://github.com/YOUR_USERNAME/store-intelligence.git
cd store-intelligence
cp -r /path/to/your/clips ./clips          # paste your video files here
docker compose up --build                  # starts API on :8000
# In another terminal:
python dashboard/live_dashboard.py --store STORE_PURPLLE_001 --api http://localhost:8000
```

API docs: http://localhost:8000/docs

---

## Running the Detection Pipeline

```bash
# Install dependencies
pip install -r requirements.txt

# Run pipeline against your clips
bash pipeline/run.sh clips/ events.jsonl

# Feed events into the API
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d "{\"events\": $(python -c "
import json
lines = open('events.jsonl').readlines()[:500]
print(json.dumps([json.loads(l) for l in lines]))
")}"
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/events/ingest` | Ingest batch of events (max 500, idempotent) |
| GET | `/stores/{id}/metrics` | Visitors, conversion rate, dwell, queue depth |
| GET | `/stores/{id}/funnel` | Entry → Browse → Billing → Purchase funnel |
| GET | `/stores/{id}/heatmap` | Zone heat scores (0-100) |
| GET | `/stores/{id}/anomalies` | Active anomalies with severity + action |
| GET | `/health` | Service health + STALE_FEED warnings |

---

## Running Tests

```bash
pip install pytest pytest-asyncio anyio httpx
pytest tests/ -v --tb=short
```

---

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py      # YOLOv8n detection + tracking
│   ├── tracker.py     # Re-ID + visitor session management
│   ├── emit.py        # Event schema validation + JSONL writer
│   └── run.sh         # One-command pipeline runner
├── app/
│   ├── main.py        # FastAPI app + middleware
│   ├── models.py      # Pydantic event schema
│   ├── database.py    # SQLite async setup
│   └── routers/       # metrics, funnel, heatmap, anomalies, health, events
├── tests/             # pytest suite (>70% coverage)
├── dashboard/
│   └── live_dashboard.py  # Rich terminal live dashboard
├── docs/
│   ├── DESIGN.md      # Architecture + AI-assisted decisions
│   └── CHOICES.md     # 3 key technical decisions with full reasoning
├── store_layout.json  # Zone + camera definitions
├── docker-compose.yml
├── Dockerfile
└── README.md
```

---

## Architecture

```
CCTV Videos → YOLOv8n Detection → VisitorTracker (Re-ID)
                                        ↓
                              Structured Events (JSONL)
                                        ↓
                          FastAPI  POST /events/ingest
                                        ↓
                              SQLite (aiosqlite)
                                        ↓
            ┌───────────────┬───────────────┬────────────────┐
         /metrics        /funnel        /heatmap        /anomalies
            └───────────────┴───────────────┴────────────────┘
                                        ↓
                          Rich Terminal Live Dashboard
```

**North Star Metric:** `Conversion Rate = billing_visitors / unique_visitors`

---

## Notes

- Video files are **not** included in this repo (challenge rules)
- Detection works with or without GPU (falls back to mock detections for testing)
- `docker compose up` requires no manual steps beyond `git clone`
