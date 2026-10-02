"""
Clarification Loop — Domain Contracts.

Defines the typed contracts for the interactive clarification loop between
AI Operator and the user.

SECURITY:
  - ClarificationSession is application-owned state.
  - Questions come from Information Sufficiency evaluation (application-owned).
  - Answers are user input (untrusted) and are not treated as authority.
  - A clarification answer does not automatically make a task sufficient.
  - This module does NOT grant permissions, approve actions, execute tools,
    or modify memory/task state directly.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.information_sufficiency.models import ClarifyingQuestion


# ── Bounds ──────────────────────────────────────────────────────────────────────

_MAX_CLARIFICATION_ID_LENGTH = 64
_MAX_CONVERSATION_ID_LENGTH = 64
_MAX_TASK_ID_LENGTH = 74
_MAX_PLAN_ID_LENGTH = 64
_MAX_REQUEST_ID_LENGTH = 64
_MAX_CORRELATION_ID_LENGTH = 128
_MAX_ID_LENGTH = 64
_MAX_SESSION_TTL_SECONDS = 86_400  # 24 hours
_MAX_ANSWER_LENGTH = 4_096
_MAX_METADATA_ENTRIES = 50
_MAX_QUESTION_IDS = 20


# ── Enumerations ───────────────────────────────────────────────────────────────


class ClarificationStatus(str, Enum):
    """Lifecycle status of a pending clarification."""
    PENDING = "PENDING"
    RESPONDED = "RESPONDED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    SATISFIED = "SATISFIED"


class ClarificationOutcome(str, Enum):
    """Outcome of processing a clarification answer."""
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    AMBIGUOUS = "AMBIGUOUS"


# ── Errors ─────────────────────────────────────────────────────────────────────


class ClarificationError(Exception):
    """Base Clarification Loop error."""
    pass


class ClarificationNotFoundError(ClarificationError):
    """Clarification session not found."""
    pass


class ClarificationStateError(ClarificationError):
    """Invalid state transition."""
    pass


class ClarificationExpiredError(ClarificationError):
    """Clarification session has expired."""
    pass


class ClarificationValidationError(ClarificationError):
    """Input validation failed."""
    pass


# ── Answer ─────────────────────────────────────────────────────────────────────


class ClarificationAnswer(BaseModel):
    """
    A user's answer to one or more clarifying questions.

    Answers are untrusted input. They are recorded as-is and re-evaluated
    by Information Sufficiency; they do not automatically resolve gaps.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str = Field(min_length=1, max_length=_MAX_CLARIFICATION_ID_LENGTH)
    answer: str = Field(min_length=1, max_length=_MAX_ANSWER_LENGTH)

    @field_validator("answer")
    @classmethod
    def _validate_answer_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("answer must not be empty or whitespace")
        return v.strip()


class ClarificationRespondRequest(BaseModel):
    """
    Input to respond to a clarification session.

    SECURITY:
      - session_id is application-controlled (not user-selected from a list).
      - answers are untrusted user input.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1, max_length=_MAX_CLARIFICATION_ID_LENGTH)
    answers: list[ClarificationAnswer] = Field(min_length=1, max_length=_MAX_QUESTION_IDS)

    @field_validator("answers")
    @classmethod
    def _validate_answer_question_ids(cls, v: list[ClarificationAnswer]) -> list[ClarificationAnswer]:
        seen: set[str] = set()
        for ans in v:
            if ans.question_id in seen:
                raise ValueError(f"duplicate question_id in answers: {ans.question_id}")
            seen.add(ans.question_id)
        return v


class ClarificationCancelRequest(BaseModel):
    """Input to cancel a pending clarification."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1, max_length=_MAX_CLARIFICATION_ID_LENGTH)
    reason: str | None = Field(default=None, max_length=500)


# ── Session ────────────────────────────────────────────────────────────────────


class ClarificationSession(BaseModel):
    """
    Durable state of a pending clarification interaction.

    IMMUTABLE after creation. Status transitions are managed exclusively
    by ClarificationService.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1, max_length=_MAX_CLARIFICATION_ID_LENGTH)
    conversation_id: str = Field(min_length=1, max_length=_MAX_CONVERSATION_ID_LENGTH)
    task_id: str | None = Field(default=None, max_length=_MAX_TASK_ID_LENGTH)
    plan_id: str | None = Field(default=None, max_length=_MAX_PLAN_ID_LENGTH)
    request_id: str | None = Field(default=None, max_length=_MAX_REQUEST_ID_LENGTH)
    correlation_id: str | None = Field(default=None, max_length=_MAX_CORRELATION_ID_LENGTH)
    user_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)
    actor_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)
    session_token_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)
    round_number: int = Field(default=1, ge=1, le=10)
    max_rounds: int = Field(default=3, ge=1, le=10)

    questions: list[ClarifyingQuestion] = Field(min_length=1, max_length=_MAX_QUESTION_IDS)
    status: ClarificationStatus = Field(default=ClarificationStatus.PENDING)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(...)
    responded_at: datetime | None = Field(default=None)
    answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=_MAX_QUESTION_IDS)
    re_evaluation_outcome: ClarificationOutcome | None = Field(default=None)
    re_evaluation_questions: list[ClarifyingQuestion] = Field(default_factory=list, max_length=_MAX_QUESTION_IDS)

    metadata: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_METADATA_ENTRIES)

    @field_validator("session_id", "conversation_id")
    @classmethod
    def _validate_non_empty_ids(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("ID field must not be empty")
        return v.strip()

    @field_validator("expires_at")
    @classmethod
    def _validate_expires_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("responded_at")
    @classmethod
    def _validate_responded_at(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    def is_expired(self, at: datetime | None = None) -> bool:
        """Check if the session has expired."""
        now = at or datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None:
            return now >= self.expires_at.replace(tzinfo=timezone.utc)
        return now >= self.expires_at

    def can_respond(self) -> bool:
        """Check if the session can still accept responses."""
        return self.status == ClarificationStatus.PENDING and not self.is_expired()


# ── Create Request ─────────────────────────────────────────────────────────────


class ClarificationCreateRequest(BaseModel):
    """
    Input to create a new clarification session.

    All identifying fields are application-controlled.
    Questions come from Information Sufficiency evaluation.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=_MAX_CONVERSATION_ID_LENGTH)
    task_id: str | None = Field(default=None, max_length=_MAX_TASK_ID_LENGTH)
    plan_id: str | None = Field(default=None, max_length=_MAX_PLAN_ID_LENGTH)
    request_id: str | None = Field(default=None, max_length=_MAX_REQUEST_ID_LENGTH)
    correlation_id: str | None = Field(default=None, max_length=_MAX_CORRELATION_ID_LENGTH)
    user_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)
    actor_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)
    session_token_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)
    questions: list[ClarifyingQuestion] = Field(min_length=1, max_length=_MAX_QUESTION_IDS)
    ttl_seconds: float | None = Field(default=None, ge=0, le=_MAX_SESSION_TTL_SECONDS)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_METADATA_ENTRIES)

    @field_validator("conversation_id")
    @classmethod
    def _validate_conversation_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("conversation_id must not be empty")
        return v.strip()

    @field_validator("task_id", "plan_id", "request_id", "correlation_id")
    @classmethod
    def _validate_ids_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            return None
        return v.strip() if v else None


# ── Response DTOs ──────────────────────────────────────────────────────────────


class ClarificationResponse(BaseModel):
    """Output from creating or acting on a clarification session."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    session: ClarificationSession
    message: str | None = None


class ClarificationListResponse(BaseModel):
    """Paginated list of clarification sessions."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    sessions: list[ClarificationSession]
    total: int
    pending_count: int


# ── Exports ────────────────────────────────────────────────────────────────────

__all__ = [
    # Enums
    "ClarificationStatus",
    "ClarificationOutcome",
    # Errors
    "ClarificationError",
    "ClarificationNotFoundError",
    "ClarificationStateError",
    "ClarificationExpiredError",
    "ClarificationValidationError",
    # Models
    "ClarificationAnswer",
    "ClarificationSession",
    "ClarificationCreateRequest",
    "ClarificationRespondRequest",
    "ClarificationCancelRequest",
    "ClarificationResponse",
    "ClarificationListResponse",
    # Bounds
    "_MAX_CLARIFICATION_ID_LENGTH",
    "_MAX_CONVERSATION_ID_LENGTH",
    "_MAX_TASK_ID_LENGTH",
    "_MAX_PLAN_ID_LENGTH",
    "_MAX_REQUEST_ID_LENGTH",
    "_MAX_CORRELATION_ID_LENGTH",
    "_MAX_SESSION_TTL_SECONDS",
    "_MAX_ANSWER_LENGTH",
    "_MAX_METADATA_ENTRIES",
    "_MAX_QUESTION_IDS",
]
