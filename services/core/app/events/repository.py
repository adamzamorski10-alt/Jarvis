"""
Repository — Events

Data access layer for immutable lifecycle events.

All timestamps are UTC. Records are append-only aside from retention
delete_before() operations. Idempotency is enforced by event_id primary key.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.event import EventModel
from app.events.sanitizer import redact_payload, redact_metadata
from app.events.schemas import (
    Event,
    EventType,
    Sensitivity,
    _validate_event_type,
    _validate_json_size,
    _validate_utc_datetime,
    _validate_string_length,
)

_MAX_PAYLOAD_BYTES = 4 * 1024
_MAX_METADATA_BYTES = 1 * 1024
_MAX_LIMIT = 500
_DEFAULT_LIMIT = 50
_MAX_OFFSET = 100_000


def _events_equivalent(a: Event, b: Event) -> bool:
    """Return True if two events represent the same logical event."""
    return (
        a.event_type == b.event_type
        and a.source == b.source
        and a.task_id == b.task_id
        and a.plan_id == b.plan_id
        and a.run_id == b.run_id
        and a.execution_id == b.execution_id
        and a.correlation_id == b.correlation_id
        and a.causation_id == b.causation_id
        and a.actor == b.actor
        and a.payload == b.payload
        and a.metadata == b.metadata
        and a.sensitivity == b.sensitivity
    )


def _normalize_dt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class EventRepository:
    """
    Append-only event store.

    Idempotency: inserting an event with an event_id that already exists
    returns the existing record and does NOT create a duplicate.

    Integrity conflict: inserting an event_id that exists with materially
    different content raises EventIntegrityConflict.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def record(self, event: Event) -> Event:
        """
        Persist an event to the database.

        Returns the persisted Event. If an event with the same event_id
        already exists and matches, returns the existing record (idempotent).
        If it exists with different content, raises EventIntegrityConflict.
        """
        if not isinstance(event, Event):
            raise TypeError(f"Expected Event, got {type(event).__name__}")

        occurred_at = _validate_utc_datetime(event.occurred_at)
        recorded_at = _validate_utc_datetime(event.recorded_at)

        event_type_str = _validate_event_type(event.event_type.value)
        source = _validate_string_length(
            event.source, 128, "source"
        ) or event.source
        correlation_id = _validate_string_length(
            event.correlation_id, 128, "correlation_id"
        )
        causation_id = _validate_string_length(
            event.causation_id, 64, "causation_id"
        )
        task_id = _validate_string_length(event.task_id, 74, "task_id")
        plan_id = _validate_string_length(event.plan_id, 64, "plan_id")
        run_id = _validate_string_length(event.run_id, 64, "run_id")
        execution_id = _validate_string_length(
            event.execution_id, 64, "execution_id"
        )
        actor = _validate_string_length(event.actor, 128, "actor")
        sensitivity = event.sensitivity or Sensitivity.INTERNAL

        payload = redact_payload(event.payload)
        if payload is not None:
            _validate_json_size(payload, _MAX_PAYLOAD_BYTES, "payload")

        metadata = redact_metadata(event.metadata)
        if metadata is not None:
            _validate_json_size(metadata, _MAX_METADATA_BYTES, "metadata")

        payload_text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")) if payload else None
        metadata_text = json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":")) if metadata else None

        model = EventModel(
            event_id=event.event_id,
            event_type=event_type_str,
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            source=source,
            event_version=event.event_version,
            correlation_id=correlation_id,
            causation_id=causation_id,
            task_id=task_id,
            plan_id=plan_id,
            run_id=run_id,
            execution_id=execution_id,
            actor=actor,
            payload=payload_text,
            event_metadata=metadata_text,
            sensitivity=sensitivity.value,
        )

        try:
            async with self._session.begin_nested():
                self._session.add(model)
                await self._session.flush()
                await self._session.refresh(model)
        except IntegrityError as exc:
            existing = await self.get(event.event_id)
            if existing is not None and _events_equivalent(existing, event):
                return existing
            raise EventIntegrityConflict(
                event_id=event.event_id,
                reason="Event already exists with different content",
            ) from exc

        return self._to_domain(model)

    async def get(self, event_id: str) -> Event | None:
        """Return Event by event_id, or None if not found."""
        result = await self._session.execute(
            select(EventModel).where(EventModel.event_id == event_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def list(
        self,
        *,
        correlation_id: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        run_id: str | None = None,
        execution_id: str | None = None,
        event_type: EventType | str | None = None,
        source: str | None = None,
        from_occurred_at: datetime | None = None,
        to_occurred_at: datetime | None = None,
        from_recorded_at: datetime | None = None,
        to_recorded_at: datetime | None = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
        recent_first: bool = False,
    ) -> tuple[Event, ...]:
        """
        Return events matching the given filters.

        Ordering: occurred_at ASC, event_id ASC (deterministic, not causal).
        Set recent_first=True for recorded_at DESC, event_id DESC.
        Pagination: limit/offset with hard caps.
        """
        limit = max(1, min(limit, _MAX_LIMIT))
        offset = max(0, min(offset, _MAX_OFFSET))

        query = select(EventModel)
        query = self._apply_filters(
            query,
            correlation_id=correlation_id,
            task_id=task_id,
            plan_id=plan_id,
            run_id=run_id,
            execution_id=execution_id,
            event_type=event_type,
            source=source,
            from_occurred_at=from_occurred_at,
            to_occurred_at=to_occurred_at,
            from_recorded_at=from_recorded_at,
            to_recorded_at=to_recorded_at,
        )
        if recent_first:
            query = query.order_by(EventModel.recorded_at.desc(), EventModel.event_id.desc())
        else:
            query = query.order_by(EventModel.occurred_at.asc(), EventModel.event_id.asc())
        query = query.limit(limit).offset(offset)

        result = await self._session.execute(query)
        models = list(result.scalars().all())
        return tuple(self._to_domain(m) for m in models)

    async def count(
        self,
        *,
        correlation_id: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        run_id: str | None = None,
        execution_id: str | None = None,
        event_type: EventType | str | None = None,
        source: str | None = None,
        from_occurred_at: datetime | None = None,
        to_occurred_at: datetime | None = None,
        from_recorded_at: datetime | None = None,
        to_recorded_at: datetime | None = None,
    ) -> int:
        """Return count of events matching the given filters."""
        query = select(func.count()).select_from(EventModel)
        query = self._apply_filters(
            query,
            correlation_id=correlation_id,
            task_id=task_id,
            plan_id=plan_id,
            run_id=run_id,
            execution_id=execution_id,
            event_type=event_type,
            source=source,
            from_occurred_at=from_occurred_at,
            to_occurred_at=to_occurred_at,
            from_recorded_at=from_recorded_at,
            to_recorded_at=to_recorded_at,
        )
        result = await self._session.execute(query)
        return result.scalar_one()

    async def list_recent(
        self,
        *,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[Event, ...]:
        """
        Return the most recently recorded events.

        Ordering: recorded_at DESC, event_id DESC
        """
        limit = max(1, min(limit, _MAX_LIMIT))

        query = select(EventModel)
        query = query.order_by(EventModel.recorded_at.desc(), EventModel.event_id.desc())
        query = query.limit(limit)

        result = await self._session.execute(query)
        models = list(result.scalars().all())
        return tuple(self._to_domain(m) for m in models)

    async def delete_before(self, timestamp: datetime) -> int:
        """
        Delete all events with recorded_at strictly before the given datetime.
        Returns count of deleted records. Used for retention management.
        Not exposed as a public API.
        """
        ts = _normalize_dt(timestamp)
        if ts is None:
            raise ValueError("timestamp must not be None")

        stmt = delete(EventModel).where(EventModel.recorded_at < ts)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_filters(query, **filters):
        if filters.get("correlation_id") is not None:
            query = query.where(EventModel.correlation_id == filters["correlation_id"])
        if filters.get("task_id") is not None:
            query = query.where(EventModel.task_id == filters["task_id"])
        if filters.get("plan_id") is not None:
            query = query.where(EventModel.plan_id == filters["plan_id"])
        if filters.get("run_id") is not None:
            query = query.where(EventModel.run_id == filters["run_id"])
        if filters.get("execution_id") is not None:
            query = query.where(EventModel.execution_id == filters["execution_id"])
        if filters.get("event_type") is not None:
            et = filters["event_type"]
            et_str = et.value if isinstance(et, EventType) else str(et)
            query = query.where(EventModel.event_type == et_str)
        if filters.get("source") is not None:
            query = query.where(EventModel.source == filters["source"])
        if filters.get("from_occurred_at") is not None:
            from_ts = _normalize_dt(filters["from_occurred_at"])
            query = query.where(EventModel.occurred_at >= from_ts)
        if filters.get("to_occurred_at") is not None:
            to_ts = _normalize_dt(filters["to_occurred_at"])
            query = query.where(EventModel.occurred_at <= to_ts)
        if filters.get("from_recorded_at") is not None:
            from_ts = _normalize_dt(filters["from_recorded_at"])
            query = query.where(EventModel.recorded_at >= from_ts)
        if filters.get("to_recorded_at") is not None:
            to_ts = _normalize_dt(filters["to_recorded_at"])
            query = query.where(EventModel.recorded_at <= to_ts)
        return query

    @staticmethod
    def _to_domain(model: EventModel) -> Event:
        """Reconstruct an immutable Event from an ORM model."""
        payload = None
        if model.payload:
            try:
                payload = json.loads(model.payload)
            except Exception:
                payload = None

        metadata = None
        if model.event_metadata:
            try:
                metadata = json.loads(model.event_metadata)
            except Exception:
                metadata = None

        return Event(
            event_id=model.event_id,
            event_type=EventType(model.event_type),
            occurred_at=model.occurred_at,
            recorded_at=model.recorded_at,
            source=model.source,
            event_version=model.event_version,
            correlation_id=model.correlation_id,
            causation_id=model.causation_id,
            task_id=model.task_id,
            plan_id=model.plan_id,
            run_id=model.run_id,
            execution_id=model.execution_id,
            actor=model.actor,
            payload=payload,
            metadata=metadata,
            sensitivity=Sensitivity(model.sensitivity),
        )


class EventIntegrityConflict(Exception):
    """Raised when an event_id already exists with different content."""

    def __init__(self, event_id: str, reason: str) -> None:
        self.event_id = event_id
        self.reason = reason
        super().__init__(f"Event integrity conflict for {event_id!r}: {reason}")


__all__ = ["EventRepository", "EventIntegrityConflict"]
