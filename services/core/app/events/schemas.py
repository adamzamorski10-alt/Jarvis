"""
Event Foundation — Domain Schemas.

Defines the application-owned Event contract, EventType vocabulary,
and Sensitivity classification.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Bounds ────────────────────────────────────────────────────────────────────

_MAX_PAYLOAD_BYTES = 4 * 1024
_MAX_METADATA_BYTES = 1 * 1024
_MAX_EVENT_TYPE_LENGTH = 128
_MAX_SOURCE_LENGTH = 128
_MAX_ACTOR_LENGTH = 128
_MAX_CORRELATION_ID_LENGTH = 128
_MAX_CAUSATION_ID_LENGTH = 128
_MAX_TASK_ID_LENGTH = 74
_MAX_PLAN_ID_LENGTH = 64
_MAX_RUN_ID_LENGTH = 64
_MAX_EXECUTION_ID_LENGTH = 64
_MAX_EVENT_VERSION_LENGTH = 32


# ── Enumerations ──────────────────────────────────────────────────────────────


class Sensitivity(str, Enum):
    """Data sensitivity classification for event payloads."""

    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"


class EventType(str, Enum):
    """
    Application-owned lifecycle event vocabulary.

    Tier 1 events only.  Add new types only through explicit Stage approval.
    """

    # Task lifecycle
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"

    # Plan lifecycle
    PLAN_STARTED = "plan.started"
    PLAN_COMPLETED = "plan.completed"
    PLAN_FAILED = "plan.failed"

    # Verification lifecycle
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_PASSED = "verification.passed"
    VERIFICATION_FAILED = "verification.failed"

    # Workspace / Git
    CHECKPOINT_CREATED = "checkpoint.created"

    # Scheduler lifecycle
    SCHEDULER_OCCURRENCE_CLAIMED = "scheduler.occurrence.claimed"
    SCHEDULER_OCCURRENCE_TRIGGERED = "scheduler.occurrence.triggered"
    SCHEDULER_OCCURRENCE_FAILED = "scheduler.occurrence.failed"
    SCHEDULER_OCCURRENCE_RECOVERED = "scheduler.occurrence.recovered"
    SCHEDULER_OCCURRENCE_SKIPPED = "scheduler.occurrence.skipped"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def _validate_event_type(value: str) -> str:
    try:
        EventType(value)
    except ValueError:
        raise ValueError(
            f"Invalid event_type {value!r}. "
            f"Allowed: {[t.value for t in EventType]}"
        )
    return value


def _validate_utc_datetime(value: datetime) -> datetime:
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
        raise ValueError(f"{label} must not be None")
    import json
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > max_bytes:
        raise ValueError(
            f"{label} exceeds max size {max_bytes} bytes: {len(serialized.encode('utf-8'))}"
        )
    return value


from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Domain Model ──────────────────────────────────────────────────────────────


class Event(BaseModel):
    """
    Immutable domain event record.

    All fields are application-owned.  Payload and metadata are untrusted
    input and MUST be sanitized before persistence.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=False)

    event_id: str = Field(default_factory=lambda: _make_event_id())
    event_type: EventType
    occurred_at: datetime = Field(default_factory=_utc_now)
    recorded_at: datetime = Field(default_factory=_utc_now)
    source: str = Field(min_length=1, max_length=_MAX_SOURCE_LENGTH)
    event_version: str = Field(default="1.0", min_length=1, max_length=_MAX_EVENT_VERSION_LENGTH)
    correlation_id: str | None = Field(default=None, max_length=_MAX_CORRELATION_ID_LENGTH)
    causation_id: str | None = Field(default=None, max_length=_MAX_CAUSATION_ID_LENGTH)
    task_id: str | None = Field(default=None, max_length=_MAX_TASK_ID_LENGTH)
    plan_id: str | None = Field(default=None, max_length=_MAX_PLAN_ID_LENGTH)
    run_id: str | None = Field(default=None, max_length=_MAX_RUN_ID_LENGTH)
    execution_id: str | None = Field(default=None, max_length=_MAX_EXECUTION_ID_LENGTH)
    actor: str | None = Field(default=None, max_length=_MAX_ACTOR_LENGTH)
    payload: dict[str, Any] | None = Field(default=None, validate_default=True)
    metadata: dict[str, Any] | None = Field(default=None, validate_default=True)
    sensitivity: Sensitivity = Field(default=Sensitivity.INTERNAL)

    @field_validator("event_type", mode="before")
    @classmethod
    def _validate_event_type(cls, value: Any) -> Any:
        if isinstance(value, EventType):
            return value
        return _validate_event_type(str(value))

    @field_validator("occurred_at", "recorded_at", mode="before")
    @classmethod
    def _validate_utc(cls, value: Any) -> Any:
        return _validate_utc_datetime(value)

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        return _validate_string_length(value, _MAX_SOURCE_LENGTH, "source") or value

    @field_validator("event_version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _validate_string_length(value, _MAX_EVENT_VERSION_LENGTH, "event_version") or value

    @field_validator("correlation_id")
    @classmethod
    def _validate_correlation(cls, value: str | None) -> str | None:
        return _validate_string_length(value, _MAX_CORRELATION_ID_LENGTH, "correlation_id")

    @field_validator("causation_id")
    @classmethod
    def _validate_causation(cls, value: str | None) -> str | None:
        return _validate_string_length(value, _MAX_CAUSATION_ID_LENGTH, "causation_id")

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

    @field_validator("actor")
    @classmethod
    def _validate_actor(cls, value: str | None) -> str | None:
        return _validate_string_length(value, _MAX_ACTOR_LENGTH, "actor")

    @field_validator("payload", mode="before")
    @classmethod
    def _validate_payload(cls, value: Any) -> Any:
        if value is None:
            return None
        return _validate_json_size(value, _MAX_PAYLOAD_BYTES, "payload")

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_metadata(cls, value: Any) -> Any:
        if value is None:
            return None
        return _validate_json_size(value, _MAX_METADATA_BYTES, "metadata")
