"""
Domain Models for Stage 5.4 — Audit Log
Immutable AuditEvent domain model with sanitized payload handling.
All timestamps are UTC. No secrets, tokens or raw user data are stored.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum byte length of serialized arguments/result JSON stored in the DB.
AUDIT_PAYLOAD_MAX_BYTES = 4096

# Patterns used to detect and redact potentially sensitive keys in payloads.
_SENSITIVE_KEY_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"passwd", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"api.?key", re.IGNORECASE),
    re.compile(r"auth", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"private.?key", re.IGNORECASE),
    re.compile(r"access.?key", re.IGNORECASE),
)

_REDACTED_MARKER = "[REDACTED]"
_TRUNCATED_MARKER = "[TRUNCATED]"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AuditEventType(str, Enum):
    """All structured audit event types in the AI Operator system."""
    TOOL_CALL_REQUESTED = "TOOL_CALL_REQUESTED"
    PERMISSION_EVALUATED = "PERMISSION_EVALUATED"
    PERMISSION_GRANTED = "PERMISSION_GRANTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    TOOL_EXECUTION_STARTED = "TOOL_EXECUTION_STARTED"
    TOOL_EXECUTION_SUCCEEDED = "TOOL_EXECUTION_SUCCEEDED"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_EXECUTION_TIMEOUT = "TOOL_EXECUTION_TIMEOUT"
    TOOL_EXECUTION_CANCELLED = "TOOL_EXECUTION_CANCELLED"


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------

def _is_sensitive_key(key: str) -> bool:
    """Returns True if a dict key name looks like it may contain sensitive data."""
    return any(pattern.search(key) for pattern in _SENSITIVE_KEY_PATTERNS)


def _redact_dict(data: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """
    Recursively redacts sensitive keys from a dict.
    Limits recursion depth to 5 to prevent pathological inputs.
    """
    if depth > 5:
        return {"__redacted__": "max_depth_exceeded"}
    result: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sensitive_key(str(key)):
            result[key] = _REDACTED_MARKER
        elif isinstance(value, dict):
            result[key] = _redact_dict(value, depth + 1)
        elif isinstance(value, (list, tuple)):
            result[key] = [
                _redact_dict(item, depth + 1) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def sanitize_payload(
    data: Any,
    max_bytes: int = AUDIT_PAYLOAD_MAX_BYTES,
) -> str | None:
    """
    Sanitize and serialize an arbitrary payload for safe audit log storage.

    Steps:
    1. If None → return None.
    2. If dict → redact sensitive keys, then serialize to JSON.
    3. If not JSON-serializable → return safe string representation.
    4. If serialized result exceeds max_bytes → truncate and mark.

    Returns a UTF-8 JSON string or None.
    """
    if data is None:
        return None

    try:
        if isinstance(data, dict):
            sanitized = _redact_dict(data)
        else:
            sanitized = data

        serialized = json.dumps(sanitized, default=str, ensure_ascii=False)
    except Exception:
        # Last-resort fallback — never fail audit log write
        serialized = json.dumps({"__audit_error__": "payload_not_serializable"})

    encoded = serialized.encode("utf-8")
    if len(encoded) > max_bytes:
        # Truncate at byte boundary and mark clearly
        truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
        serialized = json.dumps({
            "__truncated__": True,
            "__original_bytes__": len(encoded),
            "__data__": truncated,
        })

    return serialized


# ---------------------------------------------------------------------------
# Domain Model
# ---------------------------------------------------------------------------

class AuditEvent(BaseModel):
    """
    Immutable domain model representing a structured audit event.

    Design principles:
    - Frozen (immutable) after creation.
    - No raw secrets, tokens, or passwords.
    - arguments/result stored as pre-sanitized JSON strings via sanitize_payload().
    - Timestamps are always UTC-aware.
    """
    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1, description="Unique audit event ID")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the event",
    )
    event_type: AuditEventType = Field(..., description="Type of audit event")
    request_id: str | None = Field(None, description="Correlation request ID")
    tool_id: str | None = Field(None, description="Target tool identifier")
    action: str | None = Field(None, description="Requested action")
    risk_level: str | None = Field(None, description="Risk level string at evaluation time")
    autonomy_mode: str | None = Field(None, description="Autonomy mode at evaluation time")
    decision: str | None = Field(None, description="Permission decision (ALLOW/ASK_USER/DENY)")
    rule_id: str | None = Field(None, description="Rule ID that produced the decision")
    grant_id: str | None = Field(None, description="Grant ID used if applicable")
    execution_status: str | None = Field(None, description="Tool execution status")
    success: bool | None = Field(None, description="Whether execution succeeded")
    error_code: str | None = Field(None, description="Error code if failed")
    error_message: str | None = Field(None, description="Error description if failed")
    execution_time_ms: float | None = Field(None, ge=0.0, description="Execution time in ms")
    # Pre-sanitized JSON strings - set via sanitize_payload(), never raw user data.
    arguments: str | None = Field(None, description="Sanitized arguments JSON")
    result: str | None = Field(None, description="Sanitized result JSON")

    @field_validator("timestamp")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        """Guarantees timestamp is timezone-aware UTC."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("AuditEvent id cannot be empty.")
        return v_clean
