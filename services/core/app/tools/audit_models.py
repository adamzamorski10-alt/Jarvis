"""
Domain Models for Stage 5.4 — Audit Log
Immutable AuditEvent domain model with sanitized payload handling.
All timestamps are UTC. No secrets, tokens or raw user data are stored.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum byte length of serialized arguments/result JSON stored in the DB.
AUDIT_PAYLOAD_MAX_BYTES = 4096

# Exact key names and underscore-separated components (case-insensitive,
# '-' normalized to '_'). "author" / "authorized_path" are not "auth".
_EXACT_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "auth",
    "credential",
    "private_key",
    "access_key",
    "key",
})

_REDACTED_MARKER = "[REDACTED]"


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
    APPROVAL_APPROVED = "APPROVAL_APPROVED"
    APPROVAL_EXECUTING = "APPROVAL_EXECUTING"
    APPROVAL_EXECUTED = "APPROVAL_EXECUTED"
    APPROVAL_FAILED = "APPROVAL_FAILED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    APPROVAL_TOOL_MISMATCH = "APPROVAL_TOOL_MISMATCH"
    APPROVAL_ACTION_MISMATCH = "APPROVAL_ACTION_MISMATCH"
    APPROVAL_TOOL_CALL_ID_MISMATCH = "APPROVAL_TOOL_CALL_ID_MISMATCH"
    APPROVAL_ARGS_MISMATCH = "APPROVAL_ARGS_MISMATCH"
    APPROVAL_REQUEST_ID_MISMATCH = "APPROVAL_REQUEST_ID_MISMATCH"
    APPROVAL_TOOL_DEFINITION_CHANGED = "APPROVAL_TOOL_DEFINITION_CHANGED"
    APPROVAL_OVERRIDE = "APPROVAL_OVERRIDE"
    TOOL_EXECUTION_STARTED = "TOOL_EXECUTION_STARTED"
    TOOL_EXECUTION_SUCCEEDED = "TOOL_EXECUTION_SUCCEEDED"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_EXECUTION_TIMEOUT = "TOOL_EXECUTION_TIMEOUT"
    TOOL_EXECUTION_CANCELLED = "TOOL_EXECUTION_CANCELLED"


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------

def _normalize_key(key: str) -> str:
    return str(key).strip().lower().replace("-", "_")


# Underscore components that indicate a secret. "key" is exact-name only
# (see _EXACT_SENSITIVE_KEYS) so "normal_key" / "tool_id" stay visible.
_SENSITIVE_KEY_PARTS: frozenset[str] = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "auth",
    "credential",
    "apikey",
})


def _is_sensitive_key(key: str) -> bool:
    """Returns True if a dict key name looks like it may contain sensitive data."""
    normalized = _normalize_key(key)
    if normalized in _EXACT_SENSITIVE_KEYS:
        return True
    for part in normalized.split("_"):
        if part in _SENSITIVE_KEY_PARTS:
            return True
    return False


def _redact_value(value: Any, depth: int = 0) -> Any:
    """Recursively redact dicts and nested lists/tuples. Depth cap is 5."""
    if depth > 5:
        if isinstance(value, dict):
            return {"__redacted__": "max_depth_exceeded"}
        if isinstance(value, (list, tuple)):
            return ["max_depth_exceeded"]
        return value

    if isinstance(value, dict):
        return _redact_dict(value, depth)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, depth + 1) for item in value]
    return value


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
        else:
            result[key] = _redact_value(value, depth + 1)
    return result


def _truncated_wrapper(original_bytes: int, data: str, max_bytes: int) -> str:
    """
    Build a truncation marker JSON whose UTF-8 size is <= max_bytes.
    Shrinks __data__ until the wrapped document fits.
    """
    lo, hi = 0, len(data)
    best = json.dumps(
        {"__truncated__": True, "__original_bytes__": original_bytes, "__data__": ""},
        ensure_ascii=False,
    )
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = json.dumps(
            {
                "__truncated__": True,
                "__original_bytes__": original_bytes,
                "__data__": data[:mid],
            },
            ensure_ascii=False,
        )
        if len(candidate.encode("utf-8")) <= max_bytes:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    if len(best.encode("utf-8")) > max_bytes:
        # Pathological tiny max_bytes — still never exceed the cap.
        fallback = "{}"
        if len(fallback.encode("utf-8")) <= max_bytes:
            return fallback
        return ""
    return best


def sanitize_payload(
    data: Any,
    max_bytes: int = AUDIT_PAYLOAD_MAX_BYTES,
) -> str | None:
    """
    Sanitize and serialize an arbitrary payload for safe audit log storage.

    Steps:
    1. If None → return None.
    2. If dict/list → redact sensitive keys (including nested lists), then serialize.
    3. If not JSON-serializable → return safe string representation.
    4. If serialized result exceeds max_bytes → truncate; wrapped JSON is still <= max_bytes.

    Returns a UTF-8 JSON string or None.
    """
    if data is None:
        return None

    try:
        if isinstance(data, dict):
            sanitized = _redact_dict(data)
        elif isinstance(data, (list, tuple)):
            sanitized = _redact_value(data)
        else:
            sanitized = data

        serialized = json.dumps(sanitized, default=str, ensure_ascii=False)
    except Exception:
        # Last-resort fallback — never fail audit log write
        serialized = json.dumps({"__audit_error__": "payload_not_serializable"})

    encoded = serialized.encode("utf-8")
    if len(encoded) <= max_bytes:
        return serialized

    truncated_text = encoded[:max_bytes].decode("utf-8", errors="replace")
    fitted = _truncated_wrapper(len(encoded), truncated_text, max_bytes)
    return fitted if fitted else "{}"


def ensure_sanitized_payload(
    value: Any,
    max_bytes: int = AUDIT_PAYLOAD_MAX_BYTES,
) -> str | None:
    """
    Guarantee a stored arguments/result field is sanitized JSON.
    Accepts None, dict/list, or a JSON string (including already-sanitized output).
    Non-JSON strings are not stored raw (avoids smuggling secrets past key redaction).
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return sanitize_payload({"__non_json__": True}, max_bytes=max_bytes)
        return sanitize_payload(parsed, max_bytes=max_bytes)
    return sanitize_payload(value, max_bytes=max_bytes)


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
    - Production writes must go through make_audit_event() / AuditLogService.
    """
    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1, description="Unique audit event ID")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the event",
    )
    event_type: AuditEventType = Field(..., description="Type of audit event")
    reason: str = Field(..., min_length=1, description="Reason for the audit event")
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


def make_audit_event(
    event_type: AuditEventType | str,
    *,
    reason: str,
    request_id: str | None = None,
    tool_id: str | None = None,
    action: str | None = None,
    risk_level: str | None = None,
    autonomy_mode: str | None = None,
    decision: str | None = None,
    rule_id: str | None = None,
    grant_id: str | None = None,
    execution_status: str | None = None,
    success: bool | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    execution_time_ms: float | None = None,
    arguments: Any = None,
    result: Any = None,
    event_id: str | None = None,
) -> AuditEvent:
    """
    Factory function: creates a valid AuditEvent with sanitized payloads.
    Production timestamp is always Core UTC now — callers cannot supply it.
    Central place for sanitize_payload() — callers must NOT pre-serialize
    arguments/result themselves.
    """
    et = event_type if isinstance(event_type, AuditEventType) else AuditEventType(event_type)
    eid = event_id or f"audit_{uuid.uuid4().hex}"

    return AuditEvent(
        id=eid,
        timestamp=datetime.now(timezone.utc),
        event_type=et,
        reason=reason,
        request_id=request_id,
        tool_id=tool_id,
        action=action,
        risk_level=risk_level,
        autonomy_mode=autonomy_mode,
        decision=decision,
        rule_id=rule_id,
        grant_id=grant_id,
        execution_status=execution_status,
        success=success,
        error_code=error_code,
        error_message=error_message,
        execution_time_ms=execution_time_ms,
        arguments=sanitize_payload(arguments),
        result=sanitize_payload(result),
    )
