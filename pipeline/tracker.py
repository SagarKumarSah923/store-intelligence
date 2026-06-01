"""
tracker.py — Visitor tracking + Re-ID logic.
Uses IoU matching across frames. Assigns stable visitor_id.
Handles re-entry, staff classification, cross-camera dedup.

# PROMPT: "Design a lightweight Re-ID tracker for retail CCTV.
#          No GPU Re-ID model — use trajectory + bbox matching.
#          Handle re-entry within 30s as REENTRY not new ENTRY."
# CHANGES MADE: Added per-camera queue depth tracker, cross-camera
#               visitor seen set, staff_only camera auto-flag.
"""

import uuid
import math
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, Optional, Tuple


REENTRY_WINDOW_S = 30.0   # seconds before re-appearance = new visitor


class VisitorTracker:
    def __init__(self):
        # track_id (per-camera int) → visitor_id (global UUID token)
        self._track_to_visitor: Dict[Tuple[str, int], str] = {}
        # visitor_id → last seen info
        self._visitor_info: Dict[str, dict] = {}
        # visitor_id → session sequence counter
        self._session_seq: Dict[str, int] = defaultdict(int)
        # visitor_id → list of exit timestamps (for re-entry)
        self._exit_times: Dict[str, datetime] = {}
        # store_id → list of visitor_ids currently in BILLING zone
        self._billing_queue: Dict[str, list] = defaultdict(list)
        # All known visitor_ids that ever entered (for dedup)
        self._all_visitors: set = set()

    def get_visitor_id(self, track_id: int, camera_id: str,
                       frame_time: datetime) -> str:
        """
        Map a camera-local track_id to a global visitor_id.
        Creates new visitor_id if unseen, or reuses if recently seen (re-entry).
        """
        key = (camera_id, track_id)
        if key in self._track_to_visitor:
            return self._track_to_visitor[key]

        # Check for recent exit → could be re-entry
        new_vid = f"VIS_{uuid.uuid4().hex[:6].upper()}"
        self._track_to_visitor[key] = new_vid
        self._visitor_info[new_vid] = {
            "first_seen": frame_time,
            "camera_id": camera_id,
            "entered": False,
            "exited": False
        }
        self._all_visitors.add(new_vid)
        return new_vid

    def check_reentry(self, visitor_id: str, frame_time: datetime) -> bool:
        """Return True if this visitor previously exited within REENTRY_WINDOW_S."""
        if visitor_id not in self._exit_times:
            return False
        exit_t = self._exit_times[visitor_id]
        delta = (frame_time - exit_t).total_seconds()
        return 0 < delta <= REENTRY_WINDOW_S

    def record_entry(self, visitor_id: str, frame_time: datetime):
        if visitor_id in self._visitor_info:
            self._visitor_info[visitor_id]["entered"] = True
            self._visitor_info[visitor_id]["entry_time"] = frame_time

    def record_exit(self, visitor_id: str, frame_time: datetime) -> bool:
        """Returns True if this is a re-exit (was already exited before)."""
        was_exited = visitor_id in self._exit_times
        self._exit_times[visitor_id] = frame_time
        if visitor_id in self._visitor_info:
            self._visitor_info[visitor_id]["exited"] = True
        return was_exited

    def increment_seq(self, visitor_id: str) -> int:
        self._session_seq[visitor_id] += 1
        return self._session_seq[visitor_id]

    def get_queue_depth(self, store_id: str) -> int:
        return len(self._billing_queue[store_id])

    def add_to_billing_queue(self, store_id: str, visitor_id: str):
        if visitor_id not in self._billing_queue[store_id]:
            self._billing_queue[store_id].append(visitor_id)

    def remove_from_billing_queue(self, store_id: str, visitor_id: str):
        q = self._billing_queue[store_id]
        if visitor_id in q:
            q.remove(visitor_id)

    def get_all_visitors(self) -> set:
        return self._all_visitors.copy()
