"""History API response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel


class HistoryEntryResponse(BaseModel):
    event_id: str
    event_type: str
    occurred_at: datetime
    recorded_at: datetime
    source: str
    event_version: str
    correlation_id: str | None
    causation_id: str | None
    task_id: str | None
    plan_id: str | None
    run_id: str | None
    execution_id: str | None
    actor: str | None
    payload: dict[str, Any] | None
    metadata: dict[str, Any] | None
    sensitivity: str

    model_config = {"from_attributes": True}


class HistoryPageResponse(BaseModel):
    entries: list[HistoryEntryResponse]
    total: int
    limit: int
    offset: int
    has_more: bool

    model_config = {"from_attributes": True}
