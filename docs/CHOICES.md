# CHOICES.md - Technical Decision Rationale

## Decision 1: Detection Model - YOLOv8n

**Options Considered:**
- YOLOv8n (nano) - fastest, CPU-friendly, good enough at 5fps
- YOLOv8m (medium) - better accuracy, needs GPU for real-time
- RT-DETR - transformer-based, more accurate but 3x slower
- MediaPipe - good for mobile, limited tracking support
- GPT-4V / Claude Vision (VLM) - used for zone classification evaluation (see below)

**What AI Suggested:**
Claude suggested YOLOv8s (small) as a balance. It noted that nano loses accuracy on partial occlusion cases.

**What I Chose and Why:**
YOLOv8n - because the challenge requires docker compose up to work without GPU. Nano runs at real-time on CPU at 5fps. The accuracy tradeoff is acceptable because: (1) confidence scores are always emitted, never suppressed; (2) the tracker compensates for single-frame misses via IoU matching.

**VLM Experiment:**
I tested using Claude Vision for zone classification by sending frame crops and asking "which product zone is this person in?" Results were accurate (~85%) but latency was 800ms/frame - unsuitable for real-time. Rule-based centroid assignment runs in <1ms. I chose rule-based with the VLM approach documented as a future improvement for ambiguous zone boundaries.

---

## Decision 2: Event Schema Design

**Options Considered:**
- Flat schema (all fields top-level) - simple but no extensibility
- Nested metadata object - allows future fields without schema breaking
- Separate event tables per event type - rigid, joins expensive

**What AI Suggested:**
AI recommended the nested metadata approach matching the challenge's sample schema exactly. It also suggested adding a `schema_version` field for future migrations.

**What I Chose and Why:**
Nested metadata matching the challenge spec exactly. I did not add schema_version because the challenge spec doesn't include it and adding unrequested fields risks schema validation failures in the scoring harness. The metadata object holds queue_depth, sku_zone, and session_seq - extensible without breaking existing consumers.

**Key Design Principles Applied:**
- event_id is UUID v4 - globally unique, client-generated, idempotent
- timestamp is ISO-8601 UTC - derived from clip start time + frame offset
- confidence is never suppressed - low-confidence events are emitted with their real confidence value
- is_staff is always explicit - downstream systems should never infer it

---

## Decision 3: Storage Engine - SQLite (aiosqlite)

**Options Considered:**
- PostgreSQL + asyncpg - production-grade, but requires extra container, more setup
- SQLite + aiosqlite - zero-dependency, single-file, docker compose up works instantly
- Redis - fast for counters, not queryable for complex analytics
- TimescaleDB - excellent for time-series, heavyweight for this challenge scope

**What AI Suggested:**
Claude suggested PostgreSQL + TimescaleDB for production scalability. It correctly noted SQLite has write concurrency limits.

**What I Chose and Why:**
SQLite for the challenge submission. Reasons:
1. `docker compose up` works with zero external dependencies
2. The challenge scoring harness tests correctness, not throughput
3. Single-writer pattern (one pipeline feeding one API) means no write contention
4. aiosqlite gives async I/O without blocking the FastAPI event loop

**At 40 live stores in production**, I would switch to TimescaleDB with a Kafka consumer per store shard. The API code is already structured to make this a database.py swap - all queries use standard SQL with no SQLite-specific syntax.

**Where AI Was Right:**
The AI correctly identified that the funnel query with multiple subqueries would be slow at scale. I added composite indexes on (store_id, event_type) and (store_id, timestamp) to mitigate this at challenge scale.
