"""
Operator Request — Domain Models.

Defines the typed contracts for the Unified Operator Request Pipeline.

SECURITY:
  - OperatorRequest is the canonical representation of what the application
    currently understands about a user request.
  - OperatorRequest is NOT a permission.
  - OperatorRequest is NOT an execution command.
  - All externally-influenced fields are bounded and validated.
  - Malformed/oversized values are rejected.
"""
from __future__ import annotations

import enum
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.identity.models import ActorType, RequestContext


# ── Bounds ──────────────────────────────────────────────────────────────────────

_MAX_REQUEST_ID_LENGTH = 64
_MAX_CONVERSATION_ID_LENGTH = 64
_MAX_MESSAGE_ID_LENGTH = 64
_MAX_USER_REQUEST_LENGTH = 65_536
_MAX_NORMALIZED_LENGTH = 8_192
_MAX_CORRELATION_ID_LENGTH = 128
_MAX_CAUSATION_ID_LENGTH = 128
_MAX_PROJECT_ID_LENGTH = 128
_MAX_TASK_ID_LENGTH = 74
_MAX_CONSTRAINTS_ENTRIES = 50
_MAX_CONSTRAINTS_KEY_LENGTH = 128
_MAX_CONSTRAINTS_VALUE_LENGTH = 2_048
_MAX_METADATA_ENTRIES = 50
_MAX_METADATA_KEY_LENGTH = 128
_MAX_METADATA_VALUE_LENGTH = 4_096
_MAX_CAPABILITIES = 50
_MAX_WARNINGS = 20
_MAX_WARNING_LENGTH = 2_048
_MAX_REASON_LENGTH = 4_096
_MAX_READINESS_REASON_LENGTH = 4_096
_MAX_CLARIFICATION_ID_LENGTH = 64


# ── Enumerations ────────────────────────────────────────────────────────────────


class OperatorRequestState(str, enum.Enum):
    """Lifecycle state of an Operator Request."""
    RECEIVED = "RECEIVED"
    NORMALIZED = "NORMALIZED"
    CLASSIFIED = "CLASSIFIED"
    READY = "READY"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    TASK_CREATED = "TASK_CREATED"
    PLAN_CREATED = "PLAN_CREATED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OperatorResponseState(str, enum.Enum):
    """Final response state returned to the frontend."""
    ANSWERED = "ANSWERED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    TASK_CREATED = "TASK_CREATED"
    PLAN_CREATED = "PLAN_CREATED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"


class ReadinessState(str, enum.Enum):
    """Readiness evaluation state."""
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


# ── Errors ──────────────────────────────────────────────────────────────────────


class OperatorRequestError(Exception):
    """Base Operator Request error."""
    pass


class InvalidOperatorRequestError(OperatorRequestError):
    """Input validation failed."""
    pass


class OversizedRequestError(InvalidOperatorRequestError):
    """Request exceeds size limits."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Oversized request: {reason}")


class MalformedRequestError(InvalidOperatorRequestError):
    """Request structure is malformed."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Malformed request: {reason}")


class ClassificationFailedError(OperatorRequestError):
    """Operation classification failed."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Classification failed: {reason}")


class ReadinessCheckFailedError(OperatorRequestError):
    """Readiness check failed due to infrastructure error."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Readiness check failed: {reason}")


class NormalizationFailedError(OperatorRequestError):
    """Request normalization failed."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Normalization failed: {reason}")


class OperatorServiceUnavailableError(OperatorRequestError):
    """A required subsystem is unavailable."""
    def __init__(self, reason: str, retryable: bool = False) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"Service unavailable: {reason}")


# ── Core Contract ───────────────────────────────────────────────────────────────


class OperatorRequest(BaseModel):
    """
    Canonical representation of a normalized user request.

    This is NOT a permission.
    This is NOT an execution command.

    It is the authoritative representation of what the application currently
    understands about a user request.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    # ── Identity ────────────────────────────────────────────────────────────────
    request_id: str = Field(min_length=1, max_length=_MAX_REQUEST_ID_LENGTH)
    conversation_id: str | None = Field(default=None, max_length=_MAX_CONVERSATION_ID_LENGTH)
    message_id: str | None = Field(default=None, max_length=_MAX_MESSAGE_ID_LENGTH)

    # ── Authentication (application-controlled, never from client) ───────────────
    request_context: RequestContext | None = None

    # ── Request Data ────────────────────────────────────────────────────────────
    user_request: str = Field(min_length=1, max_length=_MAX_USER_REQUEST_LENGTH)
    normalized_request: str = Field(default="", min_length=0, max_length=_MAX_NORMALIZED_LENGTH)

    # ── Classification ──────────────────────────────────────────────────────────
    operation_type: str = Field(default="OTHER", min_length=1, max_length=64)
    interaction_mode: str = Field(default="conversational", min_length=1, max_length=64)

    # ── Project / Task Context ──────────────────────────────────────────────────
    project_id: str | None = Field(default=None, max_length=_MAX_PROJECT_ID_LENGTH)
    task_id: str | None = Field(default=None, max_length=_MAX_TASK_ID_LENGTH)

    # ── Context Assembly ────────────────────────────────────────────────────────
    assembled_context: Any = Field(default=None)

    # ── Readiness ───────────────────────────────────────────────────────────────
    readiness_state: ReadinessState = Field(default=ReadinessState.UNKNOWN)
    readiness_reason: str | None = Field(default=None, max_length=_MAX_READINESS_REASON_LENGTH)
    clarification_session_id: str | None = Field(default=None, max_length=_MAX_CLARIFICATION_ID_LENGTH)

    # ── Classification Detail ───────────────────────────────────────────────────
    intent: str | None = Field(default=None, max_length=128)
    classification_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    classification_reason: str | None = Field(default=None, max_length=_MAX_REASON_LENGTH)

    # ── Constraints & Assumptions ───────────────────────────────────────────────
    constraints: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_CONSTRAINTS_ENTRIES)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_METADATA_ENTRIES)

    # ── Requested Capabilities ──────────────────────────────────────────────────
    requested_capabilities: list[str] = Field(default_factory=list, max_length=_MAX_CAPABILITIES)

    # ── Correlation ─────────────────────────────────────────────────────────────
    correlation_id: str = Field(min_length=1, max_length=_MAX_CORRELATION_ID_LENGTH)
    causation_id: str | None = Field(default=None, max_length=_MAX_CAUSATION_ID_LENGTH)

    # ── Lifecycle ───────────────────────────────────────────────────────────────
    state: OperatorRequestState = Field(default=OperatorRequestState.RECEIVED)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ── Error ───────────────────────────────────────────────────────────────────
    error: str | None = Field(default=None, max_length=_MAX_WARNING_LENGTH)

    @field_validator("request_id")
    @classmethod
    def _validate_request_id(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", v):
            raise ValueError("request_id must match ^[A-Za-z0-9_-]{1,64}$")
        return v

    @field_validator("correlation_id")
    @classmethod
    def _validate_correlation_id(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z0-9_-]{1,128}$", v):
            raise ValueError("correlation_id must match ^[A-Za-z0-9_-]{1,128}$")
        return v

    @field_validator("causation_id")
    @classmethod
    def _validate_causation_id(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[A-Za-z0-9_-]{1,128}$", v):
            raise ValueError("causation_id must match ^[A-Za-z0-9_-]{1,128}$")
        return v

    @field_validator("user_request")
    @classmethod
    def _validate_user_request(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("user_request must not be empty or whitespace")
        return v  # Preserve original text; normalization is in normalized_request

    @field_validator("operation_type")
    @classmethod
    def _validate_operation_type(cls, v: str) -> str:
        # Allow known operation types; model-generated types must be validated
        # by the application classifier, not accepted raw.
        allowed = {
            "DECISION", "PLANNING", "CODING", "CHAT",
            "INFORMATION_REQUEST", "FILE_READ", "FILE_WRITE", "FILE_DELETE",
            "DEVELOPMENT", "TASK_CREATE", "TASK_UPDATE",
            "PLAN_CREATE", "PLAN_EXECUTE",
            "SCHEDULE_CREATE",
            "MEMORY_QUERY", "MEMORY_CREATE",
            "OTHER", "UNKNOWN",
        }
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(
                f"operation_type must be one of {sorted(allowed)}, got {v!r}"
            )
        return v_upper

    @field_validator("interaction_mode")
    @classmethod
    def _validate_interaction_mode(cls, v: str) -> str:
        allowed = {"conversational", "task", "planning", "execution", "clarification"}
        v_lower = v.lower()
        if v_lower not in allowed:
            raise ValueError(f"interaction_mode must be one of {sorted(allowed)}, got {v!r}")
        return v_lower

    @field_validator("constraints")
    @classmethod
    def _validate_constraints_bounded(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > _MAX_CONSTRAINTS_ENTRIES:
            raise ValueError(f"constraints must have at most {_MAX_CONSTRAINTS_ENTRIES} entries")
        for key, value in v.items():
            if not isinstance(key, str):
                raise ValueError(f"constraint key must be a string, got {type(key).__name__}")
            if len(key) > _MAX_CONSTRAINTS_KEY_LENGTH:
                raise ValueError(f"constraint key exceeds {_MAX_CONSTRAINTS_KEY_LENGTH} chars")
            if isinstance(value, str) and len(value) > _MAX_CONSTRAINTS_VALUE_LENGTH:
                raise ValueError(f"constraint value for '{key}' exceeds {_MAX_CONSTRAINTS_VALUE_LENGTH} chars")
        return v

    @field_validator("requested_capabilities")
    @classmethod
    def _validate_capabilities_bounded(cls, v: list[str]) -> list[str]:
        if len(v) > _MAX_CAPABILITIES:
            raise ValueError(f"requested_capabilities must have at most {_MAX_CAPABILITIES} entries")
        for cap in v:
            if not isinstance(cap, str) or not cap.strip():
                raise ValueError("capability must be a non-empty string")
        return [cap.strip() for cap in v]

    @field_validator("classification_confidence")
    @classmethod
    def _validate_confidence(cls, v: float | None) -> float | None:
        if v is not None and (v < 0.0 or v > 1.0):
            raise ValueError("classification_confidence must be between 0.0 and 1.0")
        return v


# ── Supporting Models ───────────────────────────────────────────────────────────


class OperatorRequestCreate(BaseModel):
    """
    Input to create an Operator Request.

    All fields are application-controlled or bounded user input.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str | None = Field(default=None, max_length=_MAX_CONVERSATION_ID_LENGTH)
    message_id: str | None = Field(default=None, max_length=_MAX_MESSAGE_ID_LENGTH)
    user_request: str = Field(min_length=1, max_length=_MAX_USER_REQUEST_LENGTH)
    project_id: str | None = Field(default=None, max_length=_MAX_PROJECT_ID_LENGTH)
    task_id: str | None = Field(default=None, max_length=_MAX_TASK_ID_LENGTH)
    correlation_id: str | None = Field(default=None, max_length=_MAX_CORRELATION_ID_LENGTH)
    causation_id: str | None = Field(default=None, max_length=_MAX_CAUSATION_ID_LENGTH)
    constraints: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_CONSTRAINTS_ENTRIES)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_METADATA_ENTRIES)
    request_context: RequestContext

    @field_validator("user_request")
    @classmethod
    def _validate_user_request_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("user_request must not be empty or whitespace")
        return v  # Preserve original text; normalization happens later


class OperatorRequestUpdate(BaseModel):
    """Application-controlled update to an Operator Request."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: OperatorRequestState | None = None
    normalized_request: str | None = Field(default=None, max_length=_MAX_NORMALIZED_LENGTH)
    operation_type: str | None = None
    interaction_mode: str | None = None
    readiness_state: ReadinessState | None = None
    readiness_reason: str | None = Field(default=None, max_length=_MAX_READINESS_REASON_LENGTH)
    clarification_session_id: str | None = Field(default=None, max_length=_MAX_CLARIFICATION_ID_LENGTH)
    intent: str | None = Field(default=None, max_length=128)
    classification_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    classification_reason: str | None = Field(default=None, max_length=_MAX_REASON_LENGTH)
    assembled_context: Any = Field(default=None)
    requested_capabilities: list[str] | None = Field(default=None, max_length=_MAX_CAPABILITIES)
    error: str | None = Field(default=None, max_length=_MAX_WARNING_LENGTH)
    assumptions: list[str] | None = Field(default=None, max_length=20)


__all__ = [
    "OperatorRequest",
    "OperatorRequestState",
    "OperatorResponseState",
    "ReadinessState",
    "OperatorRequestError",
    "InvalidOperatorRequestError",
    "OversizedRequestError",
    "MalformedRequestError",
    "ClassificationFailedError",
    "ReadinessCheckFailedError",
    "NormalizationFailedError",
    "OperatorServiceUnavailableError",
    "OperatorRequestCreate",
    "OperatorRequestUpdate",
]
