"""
Event Sanitization — Secret Redaction.

Provides deterministic redaction of sensitive values from event payloads
and metadata before persistence.

LIMITATIONS:
  Regex-based redaction is NOT exhaustive.  It targets common credential
  formats and well-known secret patterns.  It does NOT catch:
    - credentials embedded in arbitrary base64 blobs
    - custom secret formats
    - secrets split across multiple fields
    - secrets encoded with non-standard schemes

  If an event contains content that cannot safely be persisted after
  redaction, the emitter MUST reject the event rather than relying on
  incomplete redaction.
"""
from __future__ import annotations

import json
import re
from typing import Any


# ── Patterns ──────────────────────────────────────────────────────────────────

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization headers
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+[a-zA-Z0-9_\-\.]+)"),
        r"\1[REDACTED_BEARER]",
    ),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)(basic\s+[a-zA-Z0-9_\-\.=]+)"),
        r"\1[REDACTED_BASIC]",
    ),
    # API key patterns (JSON: "api_key":"...")
    (
        re.compile(r'(?i)("api[_-]?key"\s*:\s*")([^"]{20,})(")'),
        r'\1[REDACTED_API_KEY]\3',
    ),
    # Access token patterns (JSON: "access_token":"...")
    (
        re.compile(r'(?i)("access[_-]?token"\s*:\s*")([^"]+)(")'),
        r'\1[REDACTED_TOKEN]\3',
    ),
    # Bearer tokens standalone
    (
        re.compile(r"(?i)\b(bearer\s+[a-zA-Z0-9_\-\.=]+)\b"),
        "[REDACTED_BEARER]",
    ),
    # AWS-style keys
    (
        re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        "[REDACTED_AWS_KEY]",
    ),
    # Private key blocks
    (
        re.compile(r"(?i)(-----BEGIN\s+[A-Z ]+PRIVATE KEY-----.*?-----END\s+[A-Z ]+PRIVATE KEY-----)"),
        "[REDACTED_PRIVATE_KEY]",
    ),
    # Password patterns (JSON: "password":"...")
    (
        re.compile(r'(?i)("password"\s*:\s*")([^"]+)(")'),
        r'\1[REDACTED_PASSWORD]\3',
    ),
    (
        re.compile(r'(?i)("passwd"\s*:\s*")([^"]+)(")'),
        r'\1[REDACTED_PASSWORD]\3',
    ),
    # Client secret (JSON: "client_secret":"...")
    (
        re.compile(r'(?i)("client_secret"\s*:\s*")([^"]+)(")'),
        r'\1[REDACTED_CLIENT_SECRET]\3',
    ),
    # Generic secret (JSON: "secret":"...")
    (
        re.compile(r'(?i)("secret"\s*:\s*")([^"]+)(")'),
        r'\1[REDACTED_SECRET]\3',
    ),
]


def _redact_string(value: str) -> str:
    result = value
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def _redact_json_string(json_str: str) -> str:
    result = json_str
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Return a sanitized copy of the payload dict.

    Serializes to JSON first so that key-value context is available for
    pattern matching, then redacts known secret patterns, then parses back.
    """
    if payload is None:
        return None
    try:
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        redacted = _redact_json_string(serialized)
        return json.loads(redacted)
    except Exception:
        return _redact_value(payload)


def redact_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Return a sanitized copy of the metadata dict.

    Same redaction rules as redact_payload.  Metadata is NOT a security
    boundary — it is still sanitized to prevent accidental leakage.
    """
    if metadata is None:
        return None
    try:
        serialized = json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        redacted = _redact_json_string(serialized)
        return json.loads(redacted)
    except Exception:
        return _redact_value(metadata)
