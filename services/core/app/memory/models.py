"""
Memory Foundation — Domain Schemas

Defines the application-owned Memory contract, MemoryCategory vocabulary,
MemoryScope classification, TrustLevel hierarchy, and ProvenanceSource.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Bounds ─────────────────────────────────────────────────────────────────────

_MAX_CONTENT_BYTES = 8 * 1024
_MAX_METADATA_BYTES = 2 * 1024
_MAX_MEMORY_ID_LENGTH = 64
_MAX_SOURCE_ID_LENGTH = 128


# ── Secret Redaction (V1) ──────────────────────────────────────────────────────

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+[a-zA-Z0-9_\-\.]+)"), r"\1[REDACTED_BEARER]"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(basic\s+[a-zA-Z0-9_\-\.=]+)"), r"\1[REDACTED_BASIC]"),
    (re.compile(r'(?i)("api[_-]?key"\s*:\s*")([^"]{20,})(")'), r'\1[REDACTED_API_KEY]\3'),
    (re.compile(r'(?i)("access[_-]?token"\s*:\s*")([^"]+)(")'), r'\1[REDACTED_TOKEN]\3'),
    (re.compile(r"(?i)\b(bearer\s+[a-zA-Z0-9_\-\.=]+)\b"), "[REDACTED_BEARER]"),
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"(?i)(-----BEGIN\s+[A-Z ]+PRIVATE KEY-----.*?-----END\s+[A-Z ]+PRIVATE KEY-----)"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r'(?i)("password"\s*:\s*")([^"]+)(")'), r'\1[REDACTED_PASSWORD]\3'),
    (re.compile(r'(?i)("passwd"\s*:\s*")([^"]+)(")'), r'\1[REDACTED_PASSWORD]\3'),
    (re.compile(r'(?i)("client_secret"\s*:\s*")([^"]+)(")'), r'\1[REDACTED_CLIENT_SECRET]\3'),
    (re.compile(r'(?i)("secret"\s*:\s*")([^"]+)(")'), r'\1[REDACTED_SECRET]\3'),
]


def _redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    redacted = serialized
    for pattern, replacement in _REDACT_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    try:
        result = json.loads(redacted)
        if not isinstance(result, dict):
            result = {}
    except Exception:
        result = {}
    if not result:
        raise ValueError("content cannot be safely redacted: all values match known secret patterns")
    return result


# ── Enumerations ──────────────────────────────────────────────────────────────


class MemoryScope(str, Enum):
    """
    Application-owned memory scope vocabulary.

    V1 scopes only.
    """
    GLOBAL = "global"
    PROJECT = "project"
    SYSTEM = "system"


class MemoryCategory(str, Enum):
    """
    Application-owned memory category vocabulary.

    V1 categories only.
    """
    USER_INSTRUCTION = "user_instruction"
    PROJECT_KNOWLEDGE = "project_knowledge"
    SYSTEM_KNOWLEDGE = "system_knowledge"
    AGENT_OBSERVATION = "agent_observation"
    DECISION_RATIONALE = "decision_rationale"
    CONTEXTUAL = "contextual"


class TrustLevel(str, Enum):
    """
    Application-owned trust classification for memory entries.

    Hierarchy (highest to lowest):
      EXPLICIT_USER > SYSTEM > DERIVED > EXTERNAL > AGENT

    Lower trust can NEVER silently override higher trust.
    """
    EXPLICIT_USER = "EXPLICIT_USER"
    SYSTEM = "SYSTEM"
    DERIVED = "DERIVED"
    EXTERNAL = "EXTERNAL"
    AGENT = "AGENT"


class ProvenanceSource(str, Enum):
    """
    Application-owned provenance source vocabulary.

    V1 sources only.
    """
    USER = "user"
    SYSTEM = "system"
    EVENT = "event"
    TASK = "task"
    AGENT = "agent"
    EXTERNAL = "external"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_memory_id() -> str:
    return f"mem_{uuid.uuid4().hex[:12]}"


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


class MemoryEntry(BaseModel):
    """
    Durable memory entry representing knowledge the AI Operator retains
    across sessions.

    Memory is DATA, not executable authority. It does not grant execution
    rights, modify task/plan state, or bypass any security boundary.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=False)

    memory_id: str = Field(default_factory=_make_memory_id, min_length=1, max_length=_MAX_MEMORY_ID_LENGTH)
    category: MemoryCategory
    scope: MemoryScope
    scope_id: str | None = Field(default=None, max_length=100)
    content: dict[str, Any] = Field(..., min_length=1, max_length=_MAX_CONTENT_BYTES)
    trust_level: TrustLevel
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: ProvenanceSource
    source_id: str | None = Field(default=None, max_length=_MAX_SOURCE_ID_LENGTH)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    archived_at: datetime | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)
    superseded_by: str | None = Field(default=None, max_length=_MAX_MEMORY_ID_LENGTH)
    deleted_at: datetime | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None, validate_default=True)

    @field_validator("memory_id")
    @classmethod
    def _validate_memory_id(cls, value: str) -> str:
        if not value.startswith("mem_"):
            raise ValueError(f"memory_id must start with 'mem_', got {value!r}")
        return value

    @field_validator("category", mode="before")
    @classmethod
    def _validate_category(cls, value: Any) -> Any:
        if isinstance(value, MemoryCategory):
            return value
        return MemoryCategory(str(value).strip().lower())

    @field_validator("scope", mode="before")
    @classmethod
    def _validate_scope(cls, value: Any) -> Any:
        if isinstance(value, MemoryScope):
            return value
        return MemoryScope(str(value).strip().lower())

    @field_validator("trust_level", mode="before")
    @classmethod
    def _validate_trust_level(cls, value: Any) -> Any:
        if isinstance(value, TrustLevel):
            return value
        return TrustLevel(str(value).strip().upper())

    @field_validator("source", mode="before")
    @classmethod
    def _validate_source(cls, value: Any) -> Any:
        if isinstance(value, ProvenanceSource):
            return value
        return ProvenanceSource(str(value).strip().lower())

    @field_validator("scope_id")
    @classmethod
    def _validate_scope_id(cls, value: str | None, info: Any) -> str | None:
        scope = info.data.get("scope") if info.data else None
        if isinstance(scope, str):
            scope = MemoryScope(scope)
        if scope == MemoryScope.PROJECT:
            if value is None or not value.strip():
                raise ValueError("scope_id is required when scope is 'project'")
        return value

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {value}")
        return value

    @field_validator("created_at", "updated_at", "archived_at", "expires_at", "deleted_at", mode="before")
    @classmethod
    def _validate_utc(cls, value: Any) -> Any:
        return _validate_utc_datetime(value)

    @field_validator("content", mode="before")
    @classmethod
    def _validate_content(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("content must not be None")
        if isinstance(value, dict):
            value = _validate_json_size(value, _MAX_CONTENT_BYTES, "content")
            return _redact_secrets(value)
        raise ValueError(f"content must be a dict, got {type(value).__name__}")

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_metadata(cls, value: Any) -> Any:
        return _validate_json_size(value, _MAX_METADATA_BYTES, "metadata")

    @model_validator(mode="after")
    def _validate_lifecycle_consistency(self) -> "MemoryEntry":
        if self.deleted_at is not None:
            if self.superseded_by is not None:
                raise ValueError("deleted_at and superseded_by cannot both be set")
            if self.archived_at is not None:
                raise ValueError("deleted_at and archived_at cannot both be set")
        if self.superseded_by is not None and self.archived_at is not None:
            raise ValueError("superseded_by and archived_at cannot both be set")
        return self


__all__ = [
    "MemoryEntry",
    "MemoryScope",
    "MemoryCategory",
    "TrustLevel",
    "ProvenanceSource",
    "_MAX_CONTENT_BYTES",
    "_MAX_METADATA_BYTES",
]
