"""
Pydantic models — exact required event schema.
Used for validation in POST /events/ingest.
"""

from __future__ import annotations
from typing import Optional, Literal, List
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
import uuid

EventType = Literal[
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
]
Severity = Literal["INFO", "WARN", "CRITICAL"]


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 1


class StoreEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: str
    zone_id: Optional[str] = None
    dwell_ms: int = Field(default=0, ge=0)
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v):
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except Exception:
            raise ValueError(f"Invalid ISO-8601 timestamp: {v}")
        return v

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, v):
        try:
            uuid.UUID(v)
        except Exception:
            raise ValueError(f"event_id must be UUID v4: {v}")
        return v


class IngestRequest(BaseModel):
    events: List[StoreEvent] = Field(..., max_length=500)
