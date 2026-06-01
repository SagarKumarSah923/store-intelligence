"""
detect.py — Main detection + tracking script.
YOLOv8n for person detection. Custom VisitorTracker for Re-ID.
Staff detected by dark-uniform HSV heuristic (Purplle black uniform).
Entry/exit via crossing-line direction on CAM_ENTRY_01.

Run: python -m pipeline.detect --layout store_layout.json --clips-dir clips/ --output events.jsonl
"""

import cv2, json, uuid, argparse, numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

from pipeline.tracker import VisitorTracker
from pipeline.emit import EventEmitter


def load_layout(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def is_staff_by_color(frame: np.ndarray, bbox: tuple) -> tuple[bool, float]:
    """
    Purplle staff wear all-black uniforms.
    Check HSV Value channel in torso region (20%-70% of bbox height).
    Returns (is_staff, confidence).
    """
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    ty1 = y1 + int((y2 - y1) * 0.20)
    ty2 = y1 + int((y2 - y1) * 0.70)
    torso = frame[ty1:ty2, x1:x2]
    if torso.size == 0:
        return False, 0.5
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    dark = ((hsv[:, :, 2] < 80) & (hsv[:, :, 1] < 80)).sum()
    ratio = dark / (torso.shape[0] * torso.shape[1] + 1e-9)
    return ratio > 0.45, round(min(0.95, 0.5 + ratio), 3)


def crossing_direction(prev_y: float, curr_y: float, line_y: float) -> str | None:
    """
    Detect entry/exit line crossing.
    CAM_3 (top-down entry view): increasing Y = entering store.
    """
    if prev_y is None:
        return None
    if prev_y < line_y <= curr_y:
        return "ENTRY"
    if prev_y >= line_y > curr_y:
        return "EXIT"
    return None


def assign_zone(cx: float, cy: float, frame_w: int, frame_h: int,
                camera_id: str) -> str | None:
    """
    Rule-based zone assignment from normalised centroid position.
    Based on visual analysis of the 5 video clips.
    CAM_FLOOR_01  → SKINCARE (left), CLEAN_BEAUTY (right)
    CAM_FLOOR_02  → MAKEUP (left/center), FACE (right), LIPS_EYES (bottom)
    CAM_BILLING_01 → BILLING (center), ACCESSORIES (right)
    CAM_STOCK_01  → STOCKROOM (all)
    """
    nx = cx / frame_w
    ny = cy / frame_h

    if camera_id == "CAM_FLOOR_01":
        return "SKINCARE" if nx < 0.5 else "CLEAN_BEAUTY"
    if camera_id == "CAM_FLOOR_02":
        if ny > 0.7:
            return "LIPS_EYES"
        return "MAKEUP" if nx < 0.5 else "FACE"
    if camera_id == "CAM_BILLING_01":
        return "BILLING" if nx < 0.6 else "ACCESSORIES"
    if camera_id == "CAM_STOCK_01":
        return "STOCKROOM"
    return None


class DetectionPipeline:
    def __init__(self, layout_path: str, output_path: str,
                 store_id: str = None, fps_target: int = 5):
        self.layout     = load_layout(layout_path)
        self.store_id   = store_id or self.layout["store_id"]
        self.fps_target = fps_target
        self.emitter    = EventEmitter(output_path)
        self.tracker    = VisitorTracker()

        if YOLO_AVAILABLE:
            print("[pipeline] Loading YOLOv8n model...")
            self.model = YOLO("yolov8n.pt")
        else:
            self.model = None
            print("[pipeline] YOLO not available — using mock detections")

    def process_video(self, video_path: str, camera_id: str,
                      clip_start: datetime, is_entry_cam: bool = False,
                      is_stockroom: bool = False):
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"[pipeline] SKIP: Cannot open {video_path}")
            return

        src_fps    = cap.get(cv2.CAP_PROP_FPS) or 25.0
        skip       = max(1, int(src_fps / self.fps_target))
        entry_line = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) * 0.55)

        track_prev_cy: dict = {}
        track_zone:    dict = {}
        track_zone_ts: dict = {}
        dwell_emitted: dict = defaultdict(set)
        frame_idx = 0

        print(f"[pipeline] {Path(video_path).name} | cam={camera_id} | "
              f"src_fps={src_fps:.0f} | skip={skip}")

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % skip != 0:
                frame_idx += 1
                continue

            h, w = frame.shape[:2]
            frame_time = clip_start + timedelta(seconds=frame_idx / src_fps)
            detections = self._detect(frame)

            for det in detections:
                bbox     = det["bbox"]
                track_id = det["track_id"]
                conf     = det["confidence"]
                cx = (bbox[0] + bbox[2]) / 2
                cy = (bbox[1] + bbox[3]) / 2

                is_staff, _ = is_staff_by_color(frame, bbox)
                if is_stockroom:
                    is_staff = True

                vid = self.tracker.get_visitor_id(track_id, camera_id, frame_time)
                seq = self.tracker.increment_seq(vid)

                def emit(etype, zone_id=None, dwell_ms=0, q_depth=None):
                    self.emitter.emit({
                        "event_id":  str(uuid.uuid4()),
                        "store_id":  self.store_id,
                        "camera_id": camera_id,
                        "visitor_id": vid,
                        "event_type": etype,
                        "timestamp":  frame_time.isoformat(),
                        "zone_id":    zone_id,
                        "dwell_ms":   dwell_ms,
                        "is_staff":   is_staff,
                        "confidence": round(conf, 3),
                        "metadata": {
                            "queue_depth": q_depth,
                            "sku_zone":    zone_id,
                            "session_seq": seq
                        }
                    })

                # ── Entry / Exit camera ──────────────────────────────────────
                if is_entry_cam:
                    direction = crossing_direction(
                        track_prev_cy.get(track_id), cy, entry_line)
                    if direction == "ENTRY":
                        is_re = self.tracker.check_reentry(vid, frame_time)
                        self.tracker.record_entry(vid, frame_time)
                        emit("REENTRY" if is_re else "ENTRY")
                    elif direction == "EXIT":
                        self.tracker.record_exit(vid, frame_time)
                        emit("EXIT")
                    track_prev_cy[track_id] = cy

                # ── Zone cameras ─────────────────────────────────────────────
                else:
                    zone = assign_zone(cx, cy, w, h, camera_id)
                    if not zone:
                        frame_idx += 1
                        continue

                    prev_zone = track_zone.get(track_id)

                    # Zone transition
                    if prev_zone != zone:
                        # Exit previous zone
                        if prev_zone and (track_id, prev_zone) in track_zone_ts:
                            enter_t = track_zone_ts[(track_id, prev_zone)]
                            dwell = int((frame_time - enter_t).total_seconds() * 1000)
                            emit("ZONE_EXIT", prev_zone, dwell)
                            del track_zone_ts[(track_id, prev_zone)]

                        # Enter new zone
                        track_zone_ts[(track_id, zone)] = frame_time
                        track_zone[track_id] = zone

                        if zone == "BILLING":
                            qd = self.tracker.get_queue_depth(self.store_id)
                            self.tracker.add_to_billing_queue(self.store_id, vid)
                            emit("BILLING_QUEUE_JOIN", zone, 0, qd)
                        else:
                            emit("ZONE_ENTER", zone)

                    # ZONE_DWELL every 30s
                    if (track_id, zone) in track_zone_ts:
                        elapsed = (frame_time - track_zone_ts[(track_id, zone)]).total_seconds()
                        dseq = int(elapsed // 30)
                        if dseq > 0 and (zone, dseq) not in dwell_emitted[track_id]:
                            dwell_emitted[track_id].add((zone, dseq))
                            emit("ZONE_DWELL", zone, int(elapsed * 1000))

            frame_idx += 1

        cap.release()
        print(f"[pipeline] Done: {self.emitter.count} total events emitted")

    def _detect(self, frame: np.ndarray) -> list:
        if YOLO_AVAILABLE and self.model:
            results = self.model.track(frame, persist=True, classes=[0],
                                       verbose=False, conf=0.35)
            if results and results[0].boxes is not None:
                boxes = results[0].boxes
                return [
                    {
                        "bbox": tuple(b.xyxy[0].tolist()),
                        "track_id": int(b.id[0]) if b.id is not None else i,
                        "confidence": float(b.conf[0])
                    }
                    for i, b in enumerate(boxes)
                ]
            return []
        return self._mock_detections(frame)

    def _mock_detections(self, frame: np.ndarray) -> list:
        import random
        rng = random.Random(hash(bytes(frame[:5, :5].flatten().tolist())))
        h, w = frame.shape[:2]
        return [
            {
                "bbox": (rng.randint(50, w-200), rng.randint(50, h-300),
                         rng.randint(150, w-50), rng.randint(200, h-50)),
                "track_id": i + 1,
                "confidence": round(rng.uniform(0.55, 0.92), 3)
            }
            for i in range(rng.randint(1, 3))
        ]


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence — Detection Pipeline")
    parser.add_argument("--layout",    default="store_layout.json")
    parser.add_argument("--clips-dir", default="clips")
    parser.add_argument("--output",    default="events.jsonl")
    parser.add_argument("--store-id",  default=None)
    parser.add_argument("--fps",       type=int, default=5)
    args = parser.parse_args()

    layout   = load_layout(args.layout)
    pipeline = DetectionPipeline(args.layout, args.output, args.store_id, args.fps)
    t_start  = datetime(2026, 4, 10, 20, 8, 0, tzinfo=timezone.utc)

    cam_meta = {
        "CAM_ENTRY_01":   {"is_entry": True,  "is_stock": False},
        "CAM_FLOOR_01":   {"is_entry": False, "is_stock": False},
        "CAM_FLOOR_02":   {"is_entry": False, "is_stock": False},
        "CAM_BILLING_01": {"is_entry": False, "is_stock": False},
        "CAM_STOCK_01":   {"is_entry": False, "is_stock": True},
    }

    for i, cam in enumerate(layout["cameras"]):
        video = Path(args.clips_dir) / cam["file"]
        if not video.exists():
            matches = sorted(Path(args.clips_dir).glob("*.mp4"))
            video = matches[i] if i < len(matches) else None
        if not video or not video.exists():
            print(f"[pipeline] SKIP {cam['camera_id']} — file not found")
            continue
        meta = cam_meta.get(cam["camera_id"], {})
        pipeline.process_video(
            str(video), cam["camera_id"],
            t_start + timedelta(minutes=i * 2),
            is_entry_cam=meta.get("is_entry", False),
            is_stockroom=meta.get("is_stock", False)
        )

    print(f"\n✅ Pipeline complete. Events → {args.output}")
    print(f"   Total events: {pipeline.emitter.count}")


if __name__ == "__main__":
    main()
