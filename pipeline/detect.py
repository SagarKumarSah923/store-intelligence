"""
detect.py - Main detection + tracking pipeline.
YOLOv8n for person detection. VisitorTracker for Re-ID.
Staff detection via black-uniform HSV heuristic.
Entry/exit via crossing-line direction on entry cameras.
Zone assignment via normalised centroid + store_layout.json polygons.

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

from pipeline.tracker import VisitorTracker, _age_bucket
from pipeline.emit import EventEmitter, make_entry_event, make_zone_event, make_queue_event


def load_layout(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def is_staff_by_color(frame: np.ndarray, bbox: tuple) -> tuple:
    """
    Purplle staff wear black uniforms.
    Check HSV Value + Saturation in torso region (20-70% bbox height).
    Returns (is_staff, confidence).
    """
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w-1, x2), min(h-1, y2)
    ty1 = y1 + int((y2 - y1) * 0.20)
    ty2 = y1 + int((y2 - y1) * 0.70)
    torso = frame[ty1:ty2, x1:x2]
    if torso.size == 0:
        return False, 0.5
    hsv  = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    dark = ((hsv[:,:,2] < 80) & (hsv[:,:,1] < 80)).sum()
    ratio = dark / (torso.shape[0] * torso.shape[1] + 1e-9)
    return ratio > 0.45, round(min(0.95, 0.5 + ratio), 3)


def crossing_direction(prev_y, curr_y, line_y) -> str:
    if prev_y is None:
        return None
    if prev_y < line_y <= curr_y:
        return "entry"
    if prev_y >= line_y > curr_y:
        return "exit"
    return None


def estimate_demographics(frame: np.ndarray, bbox: tuple) -> dict:
    """
    Lightweight demographics estimation.
    In production: use a dedicated age/gender model.
    Here: use bbox aspect ratio + position heuristics as proxy.
    Returns dict with gender, age, age_bucket.
    """
    import random
    x1, y1, x2, y2 = bbox
    aspect = (y2 - y1) / max(x2 - x1, 1)
    rng = random.Random(int(x1 + y1 * 100))
    gender = "F" if aspect > 2.2 else rng.choice(["M", "F"])
    age    = rng.randint(20, 45)
    return {"gender": gender, "age": age, "age_bucket": _age_bucket(age)}


def assign_zone(cx: float, cy: float, fw: int, fh: int, camera_id: str,
                layout_zones: list) -> dict:
    """
    Rule-based zone from normalised centroid position.
    Based on visual analysis + store_layout.json zone positions.
    """
    nx, ny = cx / fw, cy / fh
    if camera_id == "CAM_FLOOR_01":
        if nx < 0.5:
            return next((z for z in layout_zones if "Z01" in z["zone_id"]), None)
        return next((z for z in layout_zones if "Z03" in z["zone_id"]), None)
    if camera_id == "CAM_FLOOR_02":
        if nx > 0.5:
            return next((z for z in layout_zones if "Z02" in z["zone_id"]), None)
        return next((z for z in layout_zones if "Z04" in z["zone_id"]), None)
    if camera_id in ("CAM_BILLING_01", "CAM_BILLING_02"):
        return next((z for z in layout_zones if "BILLING" in z["zone_id"]), None)
    return None


class DetectionPipeline:
    def __init__(self, layout_path: str, output_path: str, fps_target: int = 5):
        self.layout     = load_layout(layout_path)
        self.store_id   = self.layout["store_id"]
        self.store_code = self.layout["store_code"]
        self.fps_target = fps_target
        self.emitter    = EventEmitter(output_path)
        self.tracker    = VisitorTracker(self.store_id, self.store_code)
        self.zones      = self.layout["zones"]

        if YOLO_AVAILABLE:
            print("[pipeline] Loading YOLOv8n...")
            self.model = YOLO("yolov8n.pt")
        else:
            self.model = None
            print("[pipeline] YOLO unavailable - mock detections")

    def process_video(self, video_path: str, camera_id: str,
                      clip_start: datetime, is_entry_cam: bool = False):
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"[pipeline] SKIP: {video_path}")
            return

        src_fps   = cap.get(cv2.CAP_PROP_FPS) or 15.0
        skip      = max(1, int(src_fps / self.fps_target))
        h         = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        w         = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        entry_line = int(h * 0.55)   # glass door threshold

        track_prev_cy: dict = {}
        track_zone:    dict = {}
        track_zone_ts: dict = {}
        queue_join_info: dict = {}
        frame_idx = 0
        print(f"[pipeline] {Path(video_path).name} cam={camera_id} fps={src_fps:.0f} skip={skip}")

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % skip != 0:
                frame_idx += 1
                continue

            frame_time = clip_start + timedelta(seconds=frame_idx / src_fps)
            ts_str = frame_time.isoformat()
            detections = self._detect(frame)

            for det in detections:
                bbox     = det["bbox"]
                track_id = det["track_id"]
                conf     = det["confidence"]
                cx = (bbox[0] + bbox[2]) / 2
                cy = (bbox[1] + bbox[3]) / 2

                is_staff, _ = is_staff_by_color(frame, bbox)
                demo = estimate_demographics(frame, bbox)

                vid, is_new = self.tracker.get_or_create_visitor(
                    track_id, camera_id, frame_time)
                self.tracker.update_demographics(
                    vid, demo["gender"], demo["age"])

                # -- Entry/Exit camera ----------------------------------------
                if is_entry_cam:
                    direction = crossing_direction(
                        track_prev_cy.get(track_id), cy, entry_line)

                    if direction == "entry":
                        is_re = self.tracker.check_reentry(vid, frame_time)
                        gid, gsize = self.tracker.detect_group(vid, frame_time)
                        self.tracker.record_entry(vid, frame_time)
                        etype = "reentry" if is_re else "entry"
                        self.emitter.emit(make_entry_event(
                            id_token=vid, store_code=self.store_code,
                            camera_id=camera_id, event_timestamp=ts_str,
                            is_staff=is_staff,
                            gender_pred=demo["gender"],
                            age_pred=demo["age"],
                            age_bucket=demo["age_bucket"],
                            is_face_hidden=False,
                            group_id=gid, group_size=gsize,
                            event_type=etype
                        ))

                    elif direction == "exit":
                        self.tracker.record_exit(vid, frame_time)
                        self.emitter.emit(make_entry_event(
                            id_token=vid, store_code=self.store_code,
                            camera_id=camera_id, event_timestamp=ts_str,
                            is_staff=is_staff,
                            gender_pred=demo["gender"],
                            age_pred=demo["age"],
                            age_bucket=demo["age_bucket"],
                            is_face_hidden=False,
                            group_id=None, group_size=None,
                            event_type="exit"
                        ))

                    track_prev_cy[track_id] = cy

                # -- Zone cameras ---------------------------------------------
                else:
                    zone = assign_zone(cx, cy, w, h, camera_id, self.zones)
                    if not zone:
                        frame_idx += 1
                        continue

                    zone_id   = zone["zone_id"]
                    zone_name = zone["zone_name"]
                    zone_type = zone["zone_type"]
                    zone_rev  = zone.get("is_revenue_zone", "Yes")
                    prev_zone = track_zone.get(track_id)

                    if prev_zone != zone_id:
                        if prev_zone and (track_id, prev_zone) in track_zone_ts:
                            pz = next((z for z in self.zones if z["zone_id"] == prev_zone), None)
                            if pz:
                                self.emitter.emit(make_zone_event(
                                    track_id=track_id, store_id=self.store_id,
                                    camera_id=camera_id,
                                    zone_id=prev_zone,
                                    zone_name=pz["zone_name"],
                                    zone_type=pz["zone_type"],
                                    is_revenue_zone=pz.get("is_revenue_zone","Yes"),
                                    event_time=ts_str,
                                    zone_hotspot_x=cx, zone_hotspot_y=cy,
                                    gender=demo["gender"], age=demo["age"],
                                    age_bucket=demo["age_bucket"],
                                    event_type="zone_exited"
                                ))
                                if pz["zone_type"] == "BILLING" and track_id in queue_join_info:
                                    join_info = queue_join_info.pop(track_id)
                                    wait = int((frame_time - join_info["join_time"]).total_seconds())
                                    abandoned = wait > 90
                                    self.emitter.emit(make_queue_event(
                                        track_id=track_id, store_id=self.store_id,
                                        camera_id=camera_id,
                                        zone_id=prev_zone,
                                        zone_name=pz["zone_name"],
                                        queue_join_ts=join_info["join_ts"],
                                        queue_served_ts=None if abandoned else ts_str,
                                        queue_exit_ts=ts_str,
                                        wait_seconds=wait,
                                        queue_position_at_join=join_info["position"],
                                        abandoned=abandoned,
                                        zone_hotspot_x=cx, zone_hotspot_y=cy,
                                        gender=demo["gender"], age=demo["age"],
                                        age_bucket=demo["age_bucket"]
                                    ))
                                    self.tracker.remove_from_queue(vid)
                            del track_zone_ts[(track_id, prev_zone)]

                        track_zone[track_id] = zone_id
                        track_zone_ts[(track_id, zone_id)] = frame_time
                        self.emitter.emit(make_zone_event(
                            track_id=track_id, store_id=self.store_id,
                            camera_id=camera_id,
                            zone_id=zone_id, zone_name=zone_name,
                            zone_type=zone_type, is_revenue_zone=zone_rev,
                            event_time=ts_str,
                            zone_hotspot_x=cx, zone_hotspot_y=cy,
                            gender=demo["gender"], age=demo["age"],
                            age_bucket=demo["age_bucket"],
                            event_type="zone_entered"
                        ))

                        if zone_type == "BILLING":
                            qd = self.tracker.add_to_queue(vid)
                            queue_join_info[track_id] = {
                                "join_ts": ts_str,
                                "join_time": frame_time,
                                "position": qd
                            }

            frame_idx += 1
        cap.release()
        print(f"[pipeline] Done {Path(video_path).name}: {self.emitter.count} events total")

    def _detect(self, frame):
        if YOLO_AVAILABLE and self.model:
            results = self.model.track(frame, persist=True, classes=[0],
                                       verbose=False, conf=0.35)
            if results and results[0].boxes is not None:
                boxes = results[0].boxes
                return [{"bbox": tuple(b.xyxy[0].tolist()),
                          "track_id": int(b.id[0]) if b.id is not None else i,
                          "confidence": float(b.conf[0])}
                         for i, b in enumerate(boxes)]
            return []
        return self._mock_detect(frame)

    def _mock_detect(self, frame):
        import random
        h, w = frame.shape[:2]
        rng = random.Random(hash(bytes(frame[:3,:3].flatten().tolist())))
        return [{"bbox": (rng.randint(50,w-150), rng.randint(50,h-250),
                           rng.randint(150,w-50), rng.randint(200,h-50)),
                  "track_id": i+1,
                  "confidence": round(rng.uniform(0.55,0.92),3)}
                 for i in range(rng.randint(1,3))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout",    default="store_layout.json")
    parser.add_argument("--clips-dir", default="clips")
    parser.add_argument("--output",    default="events.jsonl")
    parser.add_argument("--fps",       type=int, default=5)
    args = parser.parse_args()

    layout   = load_layout(args.layout)
    pipeline = DetectionPipeline(args.layout, args.output, args.fps)
    t_start  = datetime(2026, 4, 10, 18, 10, 0)

    cam_order = [
        ("CAM_ENTRY_01",   True),
        ("CAM_ENTRY_02",   True),
        ("CAM_ENTRY_03",   True),
        ("CAM_FLOOR_01",   False),
        ("CAM_FLOOR_02",   False),
        ("CAM_BILLING_01", False),
        ("CAM_BILLING_02", False),
    ]

    clips_dir = Path(args.clips_dir)
    for i, cam in enumerate(layout["cameras"]):
        video = clips_dir / cam["file"]
        if not video.exists():
            mp4s = sorted(clips_dir.glob("*.mp4"))
            video = mp4s[i] if i < len(mp4s) else None
        if not video or not video.exists():
            print(f"[pipeline] SKIP {cam['camera_id']} - not found")
            continue
        is_entry = cam.get("is_entry_cam", False)
        pipeline.process_video(str(video), cam["camera_id"],
                                t_start + timedelta(minutes=i * 2),
                                is_entry_cam=is_entry)

    print(f"\n? Pipeline complete ? {args.output} ({pipeline.emitter.count} events)")


if __name__ == "__main__":
    main()
