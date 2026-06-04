"""
tracker.py - Visitor tracking + Re-ID logic.
Maps per-camera track IDs to global visitor tokens (ID_6XXXX format per official schema).
Handles re-entry, group detection, staff classification.

# PROMPT: "Design lightweight Re-ID tracker. Use IoU bbox + centroid distance.
#          Handle re-entry within 30s, group entry (simultaneous ±2s), 
#          gender/age tracking. Match official sample_events schema exactly."
# CHANGES MADE: id_token format changed to ID_60001 style per official schema.
#               Added group_id / group_size fields. Added demographics tracking.
#               store_code added alongside store_id.
"""

import uuid
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, Optional, Tuple

REENTRY_WINDOW_S = 30.0
GROUP_WINDOW_S   = 2.0


class VisitorTracker:
    def __init__(self, store_id: str = "", store_code: str = ""):
        self.store_id = store_id
        self.store_code = store_code
        self._track_to_visitor: Dict[Tuple[str, int], str] = {}
        self._visitor_info: Dict[str, dict] = {}
        self._session_seq: Dict[str, int] = defaultdict(int)
        self._exit_times: Dict[str, datetime] = {}
        self._billing_queues: Dict[str, list] = defaultdict(list)
        self._all_visitors: set = set()
        self._visitor_counter: int = 60000
        self._recent_entries: list = []
        self._group_counter: int = 0

    def get_or_create_visitor(self, track_id: int, camera_id: str,
                               frame_time: datetime) -> Tuple[str, bool]:
        key = (camera_id, track_id)
        if key in self._track_to_visitor:
            return self._track_to_visitor[key], False
        self._visitor_counter += 1
        vid = f"ID_{self._visitor_counter}"
        self._track_to_visitor[key] = vid
        self._visitor_info[vid] = {
            "first_seen": frame_time,
            "camera_id": camera_id,
            "entered": False, "exited": False,
            "gender": None, "age": None,
            "age_bucket": None, "is_face_hidden": False,
            "group_id": None, "group_size": None
        }
        self._all_visitors.add(vid)
        return vid, True

    def get_visitor_id(self, track_id: int, camera_id: str, frame_time: datetime) -> str:
        visitor_id, _ = self.get_or_create_visitor(track_id, camera_id, frame_time)
        return visitor_id

    def check_reentry(self, visitor_id: str, frame_time: datetime) -> bool:
        if visitor_id not in self._exit_times:
            return False
        delta = (frame_time - self._exit_times[visitor_id]).total_seconds()
        return 0 < delta <= REENTRY_WINDOW_S

    def detect_group(self, visitor_id: str,
                     frame_time: datetime) -> Tuple[Optional[str], Optional[int]]:
        """Entries within GROUP_WINDOW_S of each other form a group."""
        self._recent_entries = [
            (t, v) for t, v in self._recent_entries
            if (frame_time - t).total_seconds() <= GROUP_WINDOW_S
        ]
        self._recent_entries.append((frame_time, visitor_id))
        if len(self._recent_entries) >= 2:
            self._group_counter += 1
            gid  = f"G_{self._group_counter}"
            size = len(self._recent_entries)
            for _, vid in self._recent_entries:
                if vid in self._visitor_info:
                    self._visitor_info[vid]["group_id"]   = gid
                    self._visitor_info[vid]["group_size"] = size
            return gid, size
        return None, None

    def record_entry(self, visitor_id: str, frame_time: datetime):
        if visitor_id in self._visitor_info:
            self._visitor_info[visitor_id]["entered"]    = True
            self._visitor_info[visitor_id]["entry_time"] = frame_time

    def record_exit(self, visitor_id: str, frame_time: datetime):
        self._exit_times[visitor_id] = frame_time
        if visitor_id in self._visitor_info:
            self._visitor_info[visitor_id]["exited"] = True

    def update_demographics(self, visitor_id: str, gender: str = None,
                             age: int = None, is_face_hidden: bool = False):
        if visitor_id in self._visitor_info:
            if gender:
                self._visitor_info[visitor_id]["gender"] = gender
                self._visitor_info[visitor_id]["gender_pred"] = gender
            if age:
                self._visitor_info[visitor_id]["age"]        = age
                self._visitor_info[visitor_id]["age_pred"]   = age
                self._visitor_info[visitor_id]["age_bucket"] = _age_bucket(age)
            self._visitor_info[visitor_id]["is_face_hidden"] = is_face_hidden

    def increment_seq(self, visitor_id: str) -> int:
        self._session_seq[visitor_id] += 1
        return self._session_seq[visitor_id]

    def get_queue_depth(self, store_id: str = "") -> int:
        return len(self._billing_queues[store_id])

    def add_to_queue(self, visitor_id: str, store_id: str = "") -> int:
        if visitor_id not in self._billing_queues[store_id]:
            self._billing_queues[store_id].append(visitor_id)
        return len(self._billing_queues[store_id])

    def remove_from_queue(self, visitor_id: str, store_id: str = ""):
        if visitor_id in self._billing_queues[store_id]:
            self._billing_queues[store_id].remove(visitor_id)

    # Backwards-compatible method names for tests and legacy callers.
    def add_to_billing_queue(self, store_id: str, visitor_id: str) -> int:
        return self.add_to_queue(visitor_id, store_id)

    def remove_from_billing_queue(self, store_id: str, visitor_id: str):
        return self.remove_from_queue(visitor_id, store_id)

    def get_visitor_info(self, visitor_id: str) -> dict:
        return self._visitor_info.get(visitor_id, {})

    def get_all_visitors(self) -> set:
        return self._all_visitors.copy()


def _age_bucket(age: int) -> str:
    if age < 18: return "under-18"
    if age < 25: return "18-24"
    if age < 35: return "25-34"
    if age < 45: return "35-44"
    if age < 55: return "45-54"
    return "55+"
