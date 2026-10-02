"""
Information Sufficiency — Domain Contracts.

Defines the typed contracts for Information Sufficiency evaluation.

SECURITY:
  - InformationSufficiencyRequest is application-controlled input.
  - SufficiencyResult is application-owned output.
  - User request and memory content are treated as untrusted data.
  - This module answers only "is information sufficient?" — it does NOT
    decide permissions, approvals, execution, or business decisions.
"""
from __future__ import annotations

import enum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.context_assembly.models import AssembledContext, ContextMode, OperationType
from app.identity.models import ActorType, RequestContext


# ── Bounds ──────────────────────────────────────────────────────────────────────

_MAX_USER_REQUEST_LENGTH = 65_536
_MAX_METADATA_ENTRIES = 50
_MAX_CONSTRAINTS_ENTRIES = 50
_MAX_CAPABILITIES = 50
_MAX_QUESTIONS = 20
_MAX_QUESTION_LENGTH = 2_048
_MAX_REASON_LENGTH = 2_048
_MAX_ASSUMPTIONS = 20
_MAX_ASSUMPTION_DESC_LENGTH = 1_024
_MAX_SOURCE_LENGTH = 256
_MAX_IMPACT_LENGTH = 1_024
_MAX_OPTIONS = 20
_MAX_RELATED = 20
_MAX_MISSING_INFO = 50
_MAX_METADATA = 50
_MAX_ID_LENGTH = 64
_MAX_EXPECTED_ANSWER_TYPES = 20


# ── Enumerations ──────────────────────────────────────────────────────────────


class SufficiencyOutcome(str, enum.Enum):
    """Sufficiency evaluation outcome."""
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    AMBIGUOUS = "AMBIGUOUS"


class MissingInformationCategory(str, enum.Enum):
    """Classification of missing or problematic information."""
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    ASSUMABLE = "ASSUMABLE"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"


_VALID_ANSWER_TYPES = {"text", "number", "boolean", "date", "option", "multi_option"}


# ── Errors ─────────────────────────────────────────────────────────────────────


class InformationSufficiencyError(Exception):
    """Base Information Sufficiency error."""
    pass


class InsufficiencyEvaluationError(InformationSufficiencyError):
    """Evaluation failed due to infrastructure error."""
    pass


class InvalidInputError(InformationSufficiencyError):
    """Input validation failed."""
    pass


class MissingRequiredFieldError(InvalidInputError):
    """A required field is missing."""
    pass


# ── Clarifying Question ────────────────────────────────────────────────────────


class ClarifyingQuestion(BaseModel):
    """
    A structured question to clarify missing or ambiguous information.

    The application decides whether to present this to the user.
    Information Sufficiency only identifies the gap; it does not
    communicate with the user directly.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str = Field(min_length=1, max_length=_MAX_ID_LENGTH)
    question: str = Field(min_length=1, max_length=_MAX_QUESTION_LENGTH)
    reason: str = Field(min_length=1, max_length=_MAX_REASON_LENGTH)
    category: MissingInformationCategory
    blocking: bool = True
    priority: int = Field(default=0, ge=0, le=100)
    expected_answer_type: str = Field(min_length=1, max_length=_MAX_EXPECTED_ANSWER_TYPES)
    options: list[str] | None = Field(default=None, max_length=_MAX_OPTIONS)
    related_missing_information: list[str] = Field(default_factory=list, max_length=_MAX_RELATED)
    safe_assumptions: list[str] | None = Field(default=None, max_length=10)

    @field_validator("expected_answer_type")
    @classmethod
    def _validate_answer_type(cls, v: str) -> str:
        if v not in _VALID_ANSWER_TYPES:
            raise ValueError(
                f"expected_answer_type must be one of {sorted(_VALID_ANSWER_TYPES)}"
            )
        return v

    @field_validator("options")
    @classmethod
    def _validate_options_consistent(cls, v: list[str] | None, info: Any) -> list[str] | None:
        if v is not None:
            data = info.data
            answer_type = data.get("expected_answer_type") if isinstance(data, dict) else getattr(data, "expected_answer_type", None)
            if answer_type not in ("option", "multi_option"):
                raise ValueError("options may only be provided when expected_answer_type is 'option' or 'multi_option'")
            if len(v) == 0:
                raise ValueError("options must not be empty when provided")
            seen: set[str] = set()
            for opt in v:
                if not opt or not opt.strip():
                    raise ValueError("option must not be empty")
                opt_stripped = opt.strip()
                if opt_stripped in seen:
                    raise ValueError(f"duplicate option: {opt_stripped}")
                seen.add(opt_stripped)
        return v

    @field_validator("safe_assumptions")
    @classmethod
    def _validate_safe_assumptions_consistent(cls, v: list[str] | None, info: Any) -> list[str] | None:
        if v is not None:
            data = info.data
            category = data.get("category") if isinstance(data, dict) else getattr(data, "category", None)
            if category != MissingInformationCategory.ASSUMABLE:
                raise ValueError("safe_assumptions may only be provided when category is ASSUMABLE")
        return v


# ── Assumption ────────────────────────────────────────────────────────────────


class Assumption(BaseModel):
    """
    A documented assumption made when information is missing or ambiguous.

    Assumptions are visible to the user and can be confirmed or rejected.
    They do NOT grant permissions or override security policy.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    assumption_id: str = Field(min_length=1, max_length=_MAX_ID_LENGTH)
    description: str = Field(min_length=1, max_length=_MAX_ASSUMPTION_DESC_LENGTH)
    source: str = Field(min_length=1, max_length=_MAX_SOURCE_LENGTH)
    confidence: float = Field(ge=0.0, le=1.0)
    impact: str = Field(min_length=1, max_length=_MAX_IMPACT_LENGTH)
    user_confirmation_required: bool = Field(default=True)
    related_question_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)

    @field_validator("description", "source", "impact")
    @classmethod
    def _validate_non_empty_strings(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must not be empty")
        return v.strip()


# ── Missing Information ───────────────────────────────────────────────────────


class MissingInformation(BaseModel):
    """
    A record of missing, ambiguous, or conflicting information.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    information_id: str = Field(min_length=1, max_length=_MAX_ID_LENGTH)
    description: str = Field(min_length=1, max_length=_MAX_QUESTION_LENGTH)
    category: MissingInformationCategory
    related_question_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description must not be empty")
        return v.strip()


# ── Request ───────────────────────────────────────────────────────────────────


class InformationSufficiencyRequest(BaseModel):
    """
    Input to Information Sufficiency evaluation.

    All fields are application-controlled. None are accepted from untrusted
    model output or user input without validation.

    SECURITY:
      - request_context is server-populated and authoritative for identity.
      - project_id, task_id, workspace_path, repository_root come from
        application state, never from user text.
      - user_request is untrusted content.
      - assembled_context may contain untrusted memory data.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_type: OperationType
    request_context: RequestContext | None = Field(default=None)
    user_request: str = Field(min_length=1, max_length=_MAX_USER_REQUEST_LENGTH)

    task_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)
    project_id: str | None = Field(default=None, max_length=_MAX_ID_LENGTH)
    workspace_path: str | None = Field(default=None, max_length=4_096)
    repository_root: str | None = Field(default=None, max_length=4_096)
    task_metadata: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_METADATA_ENTRIES)

    assembled_context: AssembledContext | None = Field(default=None)

    available_capabilities: list[str] = Field(default_factory=list, max_length=_MAX_CAPABILITIES)
    mode: ContextMode = Field(default=ContextMode.STANDARD)
    constraints: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_CONSTRAINTS_ENTRIES)

    @field_validator("request_context")
    @classmethod
    def _validate_request_context(cls, v: RequestContext) -> RequestContext:
        if v is None:
            raise ValueError("request_context is required")
        return v

    @field_validator("task_id", "project_id")
    @classmethod
    def _validate_id_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            return None
        return v.strip() if v else None

    @field_validator("available_capabilities")
    @classmethod
    def _validate_capabilities(cls, v: list[str]) -> list[str]:
        if len(v) > _MAX_CAPABILITIES:
            raise ValueError(f"available_capabilities must have at most {_MAX_CAPABILITIES} entries")
        for cap in v:
            if not isinstance(cap, str) or not cap.strip():
                raise ValueError("each capability must be a non-empty string")
        return [cap.strip() for cap in v if cap.strip()]

    @field_validator("constraints")
    @classmethod
    def _validate_constraints_bounded(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > _MAX_CONSTRAINTS_ENTRIES:
            raise ValueError(f"constraints must have at most {_MAX_CONSTRAINTS_ENTRIES} entries")
        for key, value in v.items():
            if not isinstance(key, str):
                raise ValueError(f"constraint key must be a string, got {type(key).__name__}")
            if len(key) > 128:
                raise ValueError(f"constraint key exceeds 128 chars")
            if isinstance(value, str) and len(value) > 2_048:
                raise ValueError(f"constraint value for '{key}' exceeds 2048 chars")
        return v

    @field_validator("task_metadata")
    @classmethod
    def _validate_metadata_bounded(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > _MAX_METADATA_ENTRIES:
            raise ValueError(f"task_metadata must have at most {_MAX_METADATA_ENTRIES} entries")
        return v


# ── Result ────────────────────────────────────────────────────────────────────


class SufficiencyResult(BaseModel):
    """
    Typed result of Information Sufficiency evaluation.

    The application decides how to handle each outcome.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    outcome: SufficiencyOutcome | None = None
    missing_information: list[MissingInformation] = Field(default_factory=list, max_length=_MAX_MISSING_INFO)
    ambiguities: list[MissingInformation] = Field(default_factory=list, max_length=_MAX_MISSING_INFO)
    conflicts: list[MissingInformation] = Field(default_factory=list, max_length=_MAX_MISSING_INFO)
    questions: list[ClarifyingQuestion] = Field(default_factory=list, max_length=_MAX_QUESTIONS)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=_MAX_ASSUMPTIONS)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_METADATA)

    @model_validator(mode="after")
    def _validate_consistency(self) -> "SufficiencyResult":
        if self.success:
            if self.outcome is None:
                raise ValueError("success=True requires a non-None outcome")
            if self.error is not None:
                raise ValueError("success=True must not include an error")
        else:
            if self.outcome is not None:
                raise ValueError("success=False must have outcome=None")
            if self.error is None:
                raise ValueError("success=False must include an error")
        return self


# ── Exports ────────────────────────────────────────────────────────────────────

__all__ = [
    # Enums
    "SufficiencyOutcome",
    "MissingInformationCategory",
    # Models
    "InformationSufficiencyRequest",
    "SufficiencyResult",
    "MissingInformation",
    "ClarifyingQuestion",
    "Assumption",
    # Errors
    "InformationSufficiencyError",
    "InsufficiencyEvaluationError",
    "InvalidInputError",
    "MissingRequiredFieldError",
]
