"""
Scheduler API schemas.

Request/response contracts for the Scheduler MVP control plane.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.scheduler.models import ScheduleType, SchedulerJobStatus


class ScheduledJobResponse(BaseModel):
    """Sanitized scheduler job state for API consumers."""

    model_config = ConfigDict(from_attributes=True)

    job_id: str
    name: str
    enabled: bool
    schedule_type: str
    schedule_expr: str
    timezone: str | None = None
    target_type: str
    target_id: str
    status: str
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    claim_sequence: int = 0


class ScheduledJobListResponse(BaseModel):
    """Paginated list of scheduled jobs."""

    model_config = ConfigDict(from_attributes=True)

    jobs: list[ScheduledJobResponse]
    total: int
    limit: int
    offset: int


class CreateScheduledJobRequest(BaseModel):
    """Create a new scheduled job."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    schedule_type: str = Field(..., pattern="^(once|interval|cron)$")
    schedule_expr: str = Field(..., min_length=1, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)
    target_type: str = Field(default="task", pattern="^(task|plan)$")
    target_id: str = Field(..., min_length=1, max_length=64)
    enabled: bool = True

    @field_validator("job_id", "name", "schedule_expr", "target_id")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(value)
        except Exception:
            raise ValueError(f"Invalid timezone identifier: {value!r}")
        return value


class UpdateScheduledJobRequest(BaseModel):
    """Partial update for a scheduled job."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    schedule_type: str | None = Field(default=None, pattern="^(once|interval|cron)$")
    schedule_expr: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)
    target_type: str | None = Field(default=None, pattern="^(task|plan)$")
    target_id: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None

    @field_validator("name", "schedule_expr", "target_id")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(value)
        except Exception:
            raise ValueError(f"Invalid timezone identifier: {value!r}")
        return value
