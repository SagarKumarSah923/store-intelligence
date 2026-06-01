#!/bin/bash
# run.sh — One command to process all video clips and emit events.jsonl
# Usage: bash pipeline/run.sh [clips_dir] [output_file]

set -e

CLIPS_DIR="${1:-clips}"
OUTPUT="${2:-events.jsonl}"
LAYOUT="store_layout.json"

echo "=================================================="
echo "  Purplle Store Intelligence — Detection Pipeline"
echo "=================================================="
echo "Clips dir  : $CLIPS_DIR"
echo "Output     : $OUTPUT"
echo "Layout     : $LAYOUT"
echo ""

# Verify clips dir
if [ ! -d "$CLIPS_DIR" ]; then
  echo "[ERROR] Clips directory '$CLIPS_DIR' not found."
  echo "Usage: bash pipeline/run.sh <clips_directory> [output.jsonl]"
  exit 1
fi

# Run detection pipeline
python -m pipeline.detect \
  --layout   "$LAYOUT" \
  --clips-dir "$CLIPS_DIR" \
  --output   "$OUTPUT" \
  --fps      5

echo ""
echo "Events written to: $OUTPUT"
echo "Event count: $(wc -l < "$OUTPUT")"
echo ""
echo "To ingest into API:"
echo "  curl -X POST http://localhost:8000/events/ingest \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d \"{\\\"events\\\": \$(python -c \"import json; evs=open('$OUTPUT').readlines()[:500]; print(json.dumps([json.loads(l) for l in evs]))\")}\""
