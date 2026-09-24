"""
Notification Foundation — Domain Schemas

Defines the application-owned Notification contract, NotificationType vocabulary,
and NotificationSeverity classification.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Bounds ────────────────────────────────────────────────────────────────────

_MAX_TITLE_BYTES = 200
_MAX_BODY_BYTES = 2000
_MAX_METADATA_BYTES = 1024
_MAX_NOTIFICATION_TYPE_LENGTH = 64
_MAX_SOURCE_EVENT_ID_LENGTH = 64
_MAX_CORRELATION_ID_LENGTH = 128
_MAX_TASK_ID_LENGTH = 74
_MAX_PLAN_ID_LENGTH = 64
_MAX_RUN_ID_LENGTH = 64
_MAX_EXECUTION_ID_LENGTH = 64


# ── Enumerations ──────────────────────────────────────────────────────────────


class NotificationSeverity(str, Enum):
    """Severity classification for notifications."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationType(str, Enum):
    """
    Application-owned notification vocabulary.

    Tier 1 types only. Add new types only through explicit Stage approval.
    """
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    PLAN_COMPLETED = "plan.completed"
    PLAN_FAILED = "plan.failed"
    VERIFICATION_FAILED = "verification.failed"
    SCHEDULER_OCCURRENCE_FAILED = "scheduler.occurrence.failed"
    SCHEDULER_OCCURRENCE_RECOVERED = "scheduler.occurrence.recovered"
    APPROVAL_REQUESTED = "approval.requested"
    RECOVERY_COMPLETED = "recovery.completed"
    SYSTEM_ERROR = "system.error"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_notification_id() -> str:
    return f"notif_{uuid.uuid4().hex[:12]}"


def _validate_notification_type(value: str) -> str:
    try:
        NotificationType(value)
    except ValueError:
        raise ValueError(
            f"Invalid notification_type {value!r}. "
            f"Allowed: {[t.value for t in NotificationType]}"
        )
    return value


def _validate_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_string_length(value: str | None, max_length: int, label: str) -> str | None:
    if value is None:
        return None
    if len(value) > max_length:
        raise ValueError(f"{label} exceeds max length {max_length}: {len(value)}")
    return value


def _validate_json_size(value: dict[str, Any] | None, max_bytes: int, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    import json
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > max_bytes:
        raise ValueError(
            f"{label} exceeds max size {max_bytes} bytes: {len(serialized.encode('utf-8'))}"
        )
    return value


# ── Domain Model ──────────────────────────────────────────────────────────────


class Notification(BaseModel):
    """
    Mutable notification record.

    A Notification is a durable user-facing record derived from events.
    It is NOT an Event, NOT a History entry, NOT a Delivery attempt,
    and NOT an execution command.

    Immutable fields: notification_id, source_event_id, notification_type,
                      created_at.
    Mutable fields: updated_at, read_at, severity, title, body, metadata.
    """

    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=False)

    notification_id: str = Field(default_factory=_make_notification_id)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    notification_type: str = Field(..., min_length=1, max_length=_MAX_NOTIFICATION_TYPE_LENGTH)
    severity: str = Field(default=NotificationSeverity.INFO.value, min_length=1, max_length=20)
    title: str = Field(..., min_length=1, max_length=_MAX_TITLE_BYTES)
    body: str | None = Field(default=None, max_length=_MAX_BODY_BYTES)
    source_event_id: str = Field(..., min_length=1, max_length=_MAX_SOURCE_EVENT_ID_LENGTH)
    correlation_id: str | None = Field(default=None, max_length=_MAX_CORRELATION_ID_LENGTH)
    task_id: str | None = Field(default=None, max_length=_MAX_TASK_ID_LENGTH)
    plan_id: str | None = Field(default=None, max_length=_MAX_PLAN_ID_LENGTH)
    run_id: str | None = Field(default=None, max_length=_MAX_RUN_ID_LENGTH)
    execution_id: str | None = Field(default=None, max_length=_MAX_EXECUTION_ID_LENGTH)
    read_at: datetime | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None, validate_default=True)

    @field_validator("notification_type", mode="before")
    @classmethod
    def _validate_notification_type(cls, value: Any) -> Any:
        if isinstance(value, NotificationType):
            return value.value
        return _validate_notification_type(str(value))

    @field_validator("severity", mode="before")
    @classmethod
    def _validate_severity(cls, value: Any) -> Any:
        if isinstance(value, NotificationSeverity):
            return value.value
        if value is None:
            return NotificationSeverity.INFO.value
        s = str(value).strip().lower()
        allowed = {sv.value for sv in NotificationSeverity}
        if s not in allowed:
            raise ValueError(
                f"Invalid severity {value!r}. Allowed: {sorted(allowed)}"
            )
        return s

    @field_validator("created_at", "updated_at", "read_at", "expires_at", mode="before")
    @classmethod
    def _validate_utc(cls, value: Any) -> Any:
        return _validate_utc_datetime(value)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _validate_string_length(value, _MAX_TITLE_BYTES, "title") or value

    @field_validator("body")
    @classmethod
    def _validate_body(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_string_length(value, _MAX_BODY_BYTES, "body")

    @field_validator("source_event_id")
    @classmethod
    def _validate_source_event_id(cls, value: str) -> str:
        return _validate_string_length(value, _MAX_SOURCE_EVENT_ID_LENGTH, "source_event_id") or value

    @field_validator("correlation_id")
    @classmethod
    def _validate_correlation_id(cls, value: str | None) -> str | None:
        return _validate_string_length(value, _MAX_CORRELATION_ID_LENGTH, "correlation_id")

    @field_validator("task_id")
    @classmethod
    def _validate_task_id(cls, value: str | None) -> str | None:
        return _validate_string_length(value, _MAX_TASK_ID_LENGTH, "task_id")

    @field_validator("plan_id")
    @classmethod
    def _validate_plan_id(cls, value: str | None) -> str | None:
        return _validate_string_length(value, _MAX_PLAN_ID_LENGTH, "plan_id")

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str | None) -> str | None:
        return _validate_string_length(value, _MAX_RUN_ID_LENGTH, "run_id")

    @field_validator("execution_id")
    @classmethod
    def _validate_execution_id(cls, value: str | None) -> str | None:
        return _validate_string_length(value, _MAX_EXECUTION_ID_LENGTH, "execution_id")

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_metadata(cls, value: Any) -> Any:
        if value is None:
            return None
        return _validate_json_size(value, _MAX_METADATA_BYTES, "metadata")


__all__ = [
    "Notification",
    "NotificationSeverity",
    "NotificationType",
]
