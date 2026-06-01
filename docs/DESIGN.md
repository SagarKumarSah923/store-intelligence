# DESIGN.md — Store Intelligence System

## Architecture Overview

This system converts raw CCTV footage from Purplle retail stores into real-time store analytics. The pipeline has five stages: video ingestion → AI detection → event emission → API ingestion → live dashboard.

```
CCTV Videos (5 cameras)
     ↓
pipeline/detect.py       ← YOLOv8n + VisitorTracker
     ↓
events.jsonl             ← Structured JSONL event stream
     ↓
POST /events/ingest      ← FastAPI + aiosqlite
     ↓
GET /stores/{id}/metrics|funnel|heatmap|anomalies
     ↓
dashboard/live_dashboard.py  ← Rich terminal UI
```

## Component Decisions

### Detection Layer
YOLOv8n was chosen for speed (real-time at 5fps on CPU) over accuracy. Staff are identified using an HSV colour heuristic on the torso region — Purplle staff uniformly wear black, giving a reliable dark-pixel ratio signal. Entry/exit is detected via a virtual crossing line at 55% of frame height on CAM_ENTRY_01, which captures the glass door threshold observed in the footage.

Zone assignment is rule-based using normalised centroid position per camera. This is faster and more robust than polygon IoU for fixed-camera retail environments where zone boundaries are stable.

### Tracking and Re-ID
A lightweight custom tracker (VisitorTracker) maps YOLO's per-frame integer track IDs to stable visitor_id tokens. Re-entry detection uses a 30-second window — if the same visitor_id appears again within 30s of an EXIT event, it emits REENTRY rather than a new ENTRY. This directly addresses the re-entry inflation problem stated in the challenge.

### API Layer
FastAPI with aiosqlite. All endpoints are async. The ingest endpoint is idempotent by event_id using INSERT OR IGNORE. Partial batch failure returns accepted/rejected counts without 500ing the whole request.

### Storage
SQLite via aiosqlite. See CHOICES.md for full rationale.

## AI-Assisted Decisions

### 1. Staff Detection Approach
I asked Claude: "What's the most reliable way to classify retail staff vs customers from CCTV without a trained classifier?" The AI suggested three options: (a) uniform colour heuristic, (b) fine-tuned YOLO classifier, (c) zone entry pattern (staff appear in stockroom). I agreed with option (a) as the primary signal after visually confirming from the footage that Purplle staff wear all-black. I added option (c) as a secondary signal — anything detected in CAM_STOCK_01 is auto-flagged staff.

### 2. Re-entry Window Duration
I asked for guidance on the re-entry window duration. AI suggested 60s based on literature. I reduced it to 30s after observing that the entry camera footage shows very fast re-entries (customers stepping out briefly to take a call). A 60s window would over-count re-entries; 30s is more conservative and accurate for this store format.

### 3. Funnel Deduplication Logic
AI suggested using session_seq to deduplicate funnel stages. I overrode this in favour of DISTINCT visitor_id per event_type query, which is simpler, more robust, and directly answers the business question without requiring correct session_seq assignment from the pipeline.
