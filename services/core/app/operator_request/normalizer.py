"""
Operator Request — Request Normalizer.

Transforms raw chat input into a stable request representation.

The normalizer is deterministic and application-owned.
It does NOT use an LLM to normalize trivial fields that can be derived deterministically.
"""
from __future__ import annotations

import re
import unicodedata
import hashlib
from typing import Any

from app.operator_request.errors import NormalizationFailedError
from app.operator_request.models import (
    OperatorRequest,
    OperatorRequestCreate,
    OperatorRequestState,
    ReadinessState,
)


# ── Bounds ──────────────────────────────────────────────────────────────────────

_MAX_WHITESPACE_COLLAPSE = 100  # max consecutive whitespace chars after collapse
_MAX_NORMALIZED_LENGTH = 8_192
_MAX_USER_REQUEST_LENGTH = 65_536
_MAX_CONSTRAINTS_ENTRIES = 50


# ── Normalizer ─────────────────────────────────────────────────────────────────


class RequestNormalizer:
    """
    Deterministic application-owned normalizer.

    Transforms raw user input into a stable, bounded representation.
    """

    def normalize(self, create: OperatorRequestCreate) -> OperatorRequest:
        """
        Normalize a raw request creation input into an OperatorRequest.

        Args:
            create: Application-controlled creation input.

        Returns:
            Normalized OperatorRequest.

        Raises:
            NormalizationFailedError: If normalization fails.
        """
        try:
            request_id = self._generate_request_id(create)
            correlation_id = self._resolve_correlation_id(create)
            causation_id = create.causation_id

            normalized_request = self._normalize_text(create.user_request)
            bounded_constraints = self._bound_constraints(create.constraints)
            bounded_metadata = self._bound_metadata(create.metadata)

            request = OperatorRequest(
                request_id=request_id,
                conversation_id=create.conversation_id,
                message_id=create.message_id,
                request_context=create.request_context,
                user_request=create.user_request,
                normalized_request=normalized_request,
                constraints=bounded_constraints,
                metadata=bounded_metadata,
                project_id=create.project_id,
                task_id=create.task_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                state=OperatorRequestState.NORMALIZED,
            )
            return request
        except Exception as exc:
            if isinstance(exc, (NormalizationFailedError,)):
                raise
            raise NormalizationFailedError(str(exc)) from exc

    def _generate_request_id(self, create: OperatorRequestCreate) -> str:
        """Generate a stable request ID when the client supplies an idempotency key."""
        if create.message_id:
            scope = f"{create.conversation_id or 'no-conversation'}:{create.message_id}"
            digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:24]
            return f"req_{digest}"

        import uuid
        return f"req_{uuid.uuid4().hex[:16]}"

    def _resolve_correlation_id(self, create: OperatorRequestCreate) -> str:
        """Resolve correlation ID from creation input or generate new one."""
        if create.correlation_id:
            return create.correlation_id
        import uuid
        raw = uuid.uuid4().hex
        return f"corr_{raw[:16]}"

    def _normalize_text(self, text: str) -> str:
        """
        Normalize user text deterministically.

        - Unicode normalize to NFC
        - Strip leading/trailing whitespace
        - Collapse internal whitespace (max 1 space between words)
        - Bound total length
        - Do NOT change meaning
        - Do NOT use LLM
        """
        if not text:
            return ""

        # Unicode normalize
        normalized = unicodedata.normalize("NFC", text)

        # Strip leading/trailing whitespace
        normalized = normalized.strip()

        # Collapse internal whitespace
        normalized = re.sub(r"[ \t]+", " ", normalized)  # collapse horizontal whitespace
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)  # max 2 consecutive newlines

        # Remove control characters except newline and tab
        normalized = "".join(
            ch for ch in normalized
            if unicodedata.category(ch)[0] != "C" or ch in ("\n", "\t")
        )

        # Bound length
        if len(normalized) > _MAX_NORMALIZED_LENGTH:
            normalized = normalized[:_MAX_NORMALIZED_LENGTH]

        return normalized

    def _bound_constraints(self, constraints: dict[str, Any]) -> dict[str, Any]:
        """Bound constraints to safe limits."""
        if len(constraints) > _MAX_CONSTRAINTS_ENTRIES:
            constraints = dict(list(constraints.items())[:_MAX_CONSTRAINTS_ENTRIES])
        return constraints

    def _bound_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Bound metadata to safe limits."""
        if len(metadata) > 50:
            metadata = dict(list(metadata.items())[:50])
        return metadata


__all__ = ["RequestNormalizer"]
