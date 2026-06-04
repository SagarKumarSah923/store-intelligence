"""
models.py - Pydantic models matching official sample_events.jsonl schema exactly.
Two event families: Entry/Exit and Zone/Queue events.
"""

from __future__ import annotations
from typing import Optional, Literal, List, Union
from pydantic import BaseModel, Field
import uuid

EntryExitType = Literal["entry", "exit", "reentry"]
ZoneEventType = Literal["zone_entered", "zone_exited"]
QueueEventType = Literal["queue_completed", "queue_abandoned"]


class EntryExitEvent(BaseModel):
    event_type:      EntryExitType
    id_token:        str
    store_code:      str
    camera_id:       str
    event_timestamp: str
    is_staff:        bool = False
    gender_pred:     Optional[str] = None
    age_pred:        Optional[int] = None
    age_bucket:      Optional[str] = None
    is_face_hidden:  bool = False
    group_id:        Optional[str] = None
    group_size:      Optional[int] = None


class ZoneEvent(BaseModel):
    event_type:      ZoneEventType
    track_id:        int
    store_id:        str
    camera_id:       str
    zone_id:         str
    zone_name:       str
    zone_type:       str
    is_revenue_zone: str = "Yes"
    event_time:      str
    zone_hotspot_x:  Optional[float] = None
    zone_hotspot_y:  Optional[float] = None
    gender:          Optional[str] = None
    age:             Optional[int] = None
    age_bucket:      Optional[str] = None


class QueueEvent(BaseModel):
    queue_event_id:        str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type:            QueueEventType
    track_id:              int
    store_id:              str
    camera_id:             str
    zone_id:               str
    zone_name:             str
    zone_type:             str = "BILLING"
    is_revenue_zone:       str = "Yes"
    queue_join_ts:         str
    queue_served_ts:       Optional[str] = None
    queue_exit_ts:         Optional[str] = None
    wait_seconds:          Optional[int] = None
    queue_position_at_join: Optional[int] = None
    abandoned:             bool = False
    zone_hotspot_x:        Optional[float] = None
    zone_hotspot_y:        Optional[float] = None
    gender:                Optional[str] = None
    age:                   Optional[int] = None
    age_bucket:            Optional[str] = None


AnyEvent = Union[EntryExitEvent, ZoneEvent, QueueEvent]


class IngestRequest(BaseModel):
    events: List[dict] = Field(..., max_length=500)
