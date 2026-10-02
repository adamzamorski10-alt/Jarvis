"""
Context Assembly — Domain Contracts.

Defines the typed contracts for Context Assembly.

SECURITY:
  - ContextAssemblyRequest is application-controlled input.
  - AssembledContext is application-owned output.
  - MemoryContextEntry exposes only model-safe memory metadata.
  - Internal-only fields (superseded_by, archived_at, expires_at, deleted_at, source_id)
    are never exposed in MemoryContextEntry.
"""
from __future__ import annotations

import enum
import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.identity.models import ActorType, RequestContext
from app.memory.models import (
    MemoryCategory,
    MemoryEntry,
    MemoryScope,
    ProvenanceSource,
    TrustLevel,
)
from app.tools.models import AutonomyMode


# ── Bounds ──────────────────────────────────────────────────────────────────────

_MAX_USER_REQUEST_LENGTH = 65_536
_MAX_MEMORY_QUERY_LENGTH = 256
_MAX_CONSTRAINTS_ENTRIES = 50
_MAX_METADATA_ENTRIES = 50
_MAX_MEMORIES = 20
_MAX_MEMORY_CHARS = 20_000
_MAX_TASK_CONTEXT_CHARS = 10_000
_MAX_TOOL_CONTEXT_CHARS = 4_000
_MAX_APPLICATION_STATE_CHARS = 2_000
_MAX_TOTAL_CHARS = 40_000
_MAX_WARNINGS = 20
_MAX_WARNING_LENGTH = 2_048
_MAX_SYSTEM_PROMPT_LENGTH = 8_192
_MAX_CONSTRAINTS_VALUE_LENGTH = 2_048

_QUICK_MAX_MEMORIES = 5
_QUICK_MAX_MEMORY_CHARS = 5_000
_QUICK_MAX_TOTAL_CHARS = 15_000


# ── Enumerations ──────────────────────────────────────────────────────────────


class OperationType(str, enum.Enum):
    """Application-owned operation type vocabulary."""
    DECISION = "decision"
    PLANNING = "planning"
    CODING = "coding"
    CHAT = "chat"
    INFORMATION_REQUEST = "information_request"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    DEVELOPMENT = "development"
    TASK_CREATE = "task_create"
    TASK_UPDATE = "task_update"
    PLAN_CREATE = "plan_create"
    PLAN_EXECUTE = "plan_execute"
    SCHEDULE_CREATE = "schedule_create"
    MEMORY_QUERY = "memory_query"
    MEMORY_CREATE = "memory_create"
    OTHER = "other"


class ContextMode(str, enum.Enum):
    """Assembly depth mode."""
    STANDARD = "standard"
    QUICK = "quick"
    DEEP = "deep"


# ── Errors ─────────────────────────────────────────────────────────────────────


class ContextAssemblyError(ValueError):
    """Base Context Assembly error."""
    pass


class AuthenticationRequired(ContextAssemblyError):
    """Request context is missing or not authenticated."""
    pass


class InvalidProject(ContextAssemblyError):
    """Project identifier is invalid or missing when required."""
    pass


class MemoryRetrievalFailed(ContextAssemblyError):
    """Memory retrieval failed."""
    pass


class ScopeAmbiguous(ContextAssemblyError):
    """Project scope is ambiguous."""
    pass


class ContextBudgetExceeded(ContextAssemblyError):
    """Assembled context exceeds budget limits."""
    pass


class InvalidActor(ContextAssemblyError):
    """Request actor_type does not match request_context."""
    pass


class SensitiveDataDetected(ContextAssemblyError):
    """Sensitive data detected in assembled context."""
    pass


# ── Secret screening patterns (defense-in-depth, matches MemoryEntry redaction) ─

_SECRET_SCREENING_PATTERNS: list[tuple[re.Pattern[str], str]] = [
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
    (re.compile(r'(?i)("session"\s*:\s*")([^"]+)(")'), r'\1[REDACTED_SESSION]\3'),
    (re.compile(r'(?i)("session_id"\s*:\s*")([^"]+)(")'), r'\1[REDACTED_SESSION]\3'),
]


def _screen_secrets(data: dict[str, Any]) -> bool:
    """
    Screen a dict for secret patterns.

    Returns True if any secret pattern is found.
    Does NOT modify the input dict.
    """
    try:
        serialized = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return False
    for pattern, _ in _SECRET_SCREENING_PATTERNS:
        if pattern.search(serialized):
            return True
    return False


# ── Request / Response / Entry ─────────────────────────────────────────────────


class ContextAssemblyRequest(BaseModel):
    """Input to Context Assembly."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Execution context
    operation_type: OperationType
    actor_type: ActorType
    user_id: str | None

    # Task context
    task_id: str | None = None
    project_id: str | None = None
    workspace_path: str | None = None
    repository_root: str | None = None

    # Request data
    user_request: str = Field(min_length=1, max_length=_MAX_USER_REQUEST_LENGTH)
    constraints: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_CONSTRAINTS_ENTRIES)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_METADATA_ENTRIES)

    # Optional explicit memory query
    memory_query: str | None = Field(default=None, max_length=_MAX_MEMORY_QUERY_LENGTH)

    # Execution mode
    autonomy_mode: AutonomyMode | None = None

    # Assembly depth
    mode: ContextMode = Field(default=ContextMode.STANDARD)

    # Server-populated request context (never from client)
    request_context: RequestContext | None = None


class MemoryContextEntry(BaseModel):
    """A single memory entry in assembled context."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    category: MemoryCategory
    scope: MemoryScope
    scope_id: str | None
    content: dict[str, Any]
    trust_level: TrustLevel
    confidence: float
    source: ProvenanceSource
    created_at: datetime
    relevance_score: float | None = None


class AssembledContext(BaseModel):
    """Output from Context Assembly."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    operation_type: OperationType
    project_id: str | None

    # Structured sections
    system_instructions: str = Field(default="", min_length=0, max_length=_MAX_SYSTEM_PROMPT_LENGTH)
    user_request: str = Field(default="", min_length=0, max_length=_MAX_USER_REQUEST_LENGTH)
    task_context: str = Field(default="", min_length=0, max_length=_MAX_TASK_CONTEXT_CHARS)
    memories: list[MemoryContextEntry] = Field(default_factory=list, max_length=_MAX_MEMORIES)
    application_state: str = Field(default="", min_length=0, max_length=_MAX_APPLICATION_STATE_CHARS)
    constraints: str = Field(default="", min_length=0, max_length=_MAX_CONSTRAINTS_VALUE_LENGTH)
    warnings: str = Field(default="", min_length=0, max_length=_MAX_WARNING_LENGTH)

    # Metadata (not sent to model)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_METADATA_ENTRIES)
    total_memories: int = 0
    total_memory_chars: int = 0
    truncated: bool = False
    budget_warning: str | None = None

    # Errors
    error: str | None = None


# Reuse bounds from existing modules where compatible
__all__ = [
    "ContextAssemblyRequest",
    "AssembledContext",
    "MemoryContextEntry",
    "ContextAssemblyError",
    "AuthenticationRequired",
    "InvalidProject",
    "MemoryRetrievalFailed",
    "ScopeAmbiguous",
    "ContextBudgetExceeded",
    "InvalidActor",
    "SensitiveDataDetected",
    "OperationType",
    "ContextMode",
    "_MAX_MEMORIES",
    "_MAX_TOTAL_CHARS",
    "_QUICK_MAX_MEMORIES",
    "_QUICK_MAX_MEMORY_CHARS",
    "_QUICK_MAX_TOTAL_CHARS",
    "_screen_secrets",
]
