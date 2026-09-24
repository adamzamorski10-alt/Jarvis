"""
Domain models and enums for the Scheduler subsystem.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScheduleType(str, Enum):
    CRON = "cron"
    INTERVAL = "interval"
    ONCE = "once"


class SchedulerJobStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    TRIGGERED = "TRIGGERED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    DISABLED = "DISABLED"


class ScheduledJob(BaseModel):
    """
    Minimal domain model for a scheduled job.

    Scheduler owns occurrence identity and lifecycle.
    Execution semantics are owned by the execution layer.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    job_id: str
    name: str
    enabled: bool = True
    schedule_type: ScheduleType
    schedule_expr: str
    timezone: str | None = None
    target_type: str = "task"
    target_id: str
    status: SchedulerJobStatus = SchedulerJobStatus.PENDING
    last_run_at: datetime | None = None
    next_run_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    occurrence_id: str | None = None
    claim_token: str | None = None
    claim_sequence: int = 0
    claimed_at: datetime | None = None
    lease_until: datetime | None = None
    execution_id: str | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("job_id", "name", "schedule_expr", "target_id")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Field must not be empty")
        return value.strip()

    @field_validator("target_type")
    @classmethod
    def _validate_target_type(cls, value: str) -> str:
        if value not in ("task", "plan"):
            raise ValueError(f"target_type must be 'task' or 'plan', got {value!r}")
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        if value is not None:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(value.strip())
            except Exception:
                raise ValueError(f"Invalid timezone identifier: {value!r}")
        return value.strip() if value else None

