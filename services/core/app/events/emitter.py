"""
Event Emitter — Application-facing write boundary for lifecycle events.

EventEmitter validates, bounds, sanitizes, timestamps, and persists events
through EventRepository.

SECURITY:
  - Does NOT modify permissions, approvals, task state, or Git authority.
  - Does NOT execute tools or make AI decisions.
  - Payload and metadata are sanitized before persistence.
  - Oversized payloads/metadata are rejected deterministically.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.events.repository import EventRepository, EventIntegrityConflict
from app.events.sanitizer import redact_payload, redact_metadata
from app.events.schemas import (
    Event,
    EventType,
    Sensitivity,
    _utc_now,
    _validate_json_size,
)
from app.notifications.models import Notification, NotificationSeverity, NotificationType
from app.notifications.policy import NotificationDraft, NotificationPolicy

_MAX_PAYLOAD_BYTES = 4 * 1024
_MAX_METADATA_BYTES = 1 * 1024

logger = logging.getLogger(__name__)


class EventEmissionError(Exception):
    """Base exception for event emission failures."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Event emission error: {reason}")


class EventPayloadOversizedError(EventEmissionError):
    """Raised when event payload exceeds the maximum allowed size."""

    def __init__(self, size_bytes: int, max_bytes: int) -> None:
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"Event payload size {size_bytes} bytes exceeds limit {max_bytes} bytes"
        )


class EventMetadataOversizedError(EventEmissionError):
    """Raised when event metadata exceeds the maximum allowed size."""

    def __init__(self, size_bytes: int, max_bytes: int) -> None:
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"Event metadata size {size_bytes} bytes exceeds limit {max_bytes} bytes"
        )


class EventEmitter:
    """
    Application-facing service for emitting lifecycle events.

    All events flow through this emitter before persistence.
    """

    def __init__(
        self,
        repository: EventRepository,
        notification_policy: NotificationPolicy | None = None,
        notification_repository: Any | None = None,
    ) -> None:
        self._repo = repository
        self._notification_policy = notification_policy
        self._notification_repo = notification_repository

    async def emit(
        self,
        event_type: EventType | str,
        source: str,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        run_id: str | None = None,
        execution_id: str | None = None,
        actor: str | None = None,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
        occurred_at: datetime | None = None,
        event_id: str | None = None,
        event_version: str = "1.0",
        **kwargs: Any,
    ) -> Event:
        """
        Emit a lifecycle event.

        Args:
            event_type: The event type (EventType enum or string).
            source: Component emitting the event (e.g., 'autonomous_task_orchestrator').
            correlation_id: Optional correlation ID grouping related events.
            causation_id: Optional event_id of the event that caused this one.
            task_id: Optional task identifier.
            plan_id: Optional plan identifier.
            run_id: Optional run identifier.
            execution_id: Optional execution identifier.
            actor: Optional actor identifier.
            payload: Optional structured event data (bounded 4KB, sanitized).
            metadata: Optional auxiliary data (bounded 1KB, sanitized).
            sensitivity: Data sensitivity classification.
            occurred_at: When the action occurred (defaults to now).
            event_id: Optional pre-generated event_id.
            event_version: Schema version (default '1.0').

        Returns:
            The persisted Event.

        Raises:
            EventPayloadOversizedError: If payload exceeds 4KB.
            EventMetadataOversizedError: If metadata exceeds 1KB.
            EventIntegrityConflict: If event_id conflicts with existing different content.
        """
        if occurred_at is None:
            occurred_at = _utc_now()
        else:
            if occurred_at.tzinfo is None:
                raise ValueError("occurred_at must be timezone-aware (UTC)")
            occurred_at = occurred_at.astimezone(timezone.utc)

        recorded_at = _utc_now()

        if isinstance(event_type, str):
            try:
                event_type = EventType(event_type)
            except ValueError:
                raise ValueError(
                    f"Invalid event_type {event_type!r}. "
                    f"Allowed: {[t.value for t in EventType]}"
                )

        # Merge extra kwargs into payload
        sanitized_payload = redact_payload(payload)
        merged_payload: dict[str, Any] = {}
        if sanitized_payload is not None:
            merged_payload.update(sanitized_payload)
        if kwargs:
            merged_payload.update(kwargs)

        # Validate bounds before constructing the event
        if merged_payload:
            try:
                _validate_json_size(merged_payload, _MAX_PAYLOAD_BYTES, "payload")
            except ValueError as exc:
                raise EventPayloadOversizedError(
                    size_bytes=len(json.dumps(merged_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                    max_bytes=_MAX_PAYLOAD_BYTES,
                ) from exc

        sanitized_metadata = redact_metadata(metadata)
        if sanitized_metadata is not None:
            try:
                _validate_json_size(sanitized_metadata, _MAX_METADATA_BYTES, "metadata")
            except ValueError as exc:
                raise EventMetadataOversizedError(
                    size_bytes=len(json.dumps(sanitized_metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
                    max_bytes=_MAX_METADATA_BYTES,
                ) from exc

        event = Event(
            event_id=event_id or f"evt_{__import__('uuid').uuid4().hex}",
            event_type=event_type,
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            source=source,
            event_version=event_version,
            correlation_id=correlation_id,
            causation_id=causation_id,
            task_id=task_id,
            plan_id=plan_id,
            run_id=run_id,
            execution_id=execution_id,
            actor=actor,
            payload=merged_payload or None,
            metadata=sanitized_metadata,
            sensitivity=sensitivity,
        )

        try:
            persisted_event = await self._repo.record(event)
        except EventIntegrityConflict:
            raise
        except Exception as exc:
            raise EventEmissionError(
                f"Failed to persist event {event.event_id!r}: {exc}"
            ) from exc

        if self._notification_policy is not None and self._notification_repo is not None:
            try:
                draft = self._notification_policy.process(persisted_event)
                if draft is not None:
                    notification = Notification(
                        notification_type=draft.notification_type,
                        severity=draft.severity,
                        title=draft.title,
                        body=draft.body,
                        source_event_id=draft.source_event_id,
                        correlation_id=draft.correlation_id,
                        task_id=draft.task_id,
                        plan_id=draft.plan_id,
                        run_id=draft.run_id,
                        execution_id=draft.execution_id,
                        metadata=draft.metadata,
                        expires_at=draft.expires_at,
                    )
                    await self._notification_repo.create(notification)
            except Exception as exc:
                raise EventEmissionError(
                    f"Notification creation failed for event {persisted_event.event_id!r}: {exc}"
                ) from exc

        return persisted_event


__all__ = ["EventEmitter", "EventEmissionError", "EventPayloadOversizedError", "EventMetadataOversizedError"]
