"""
Repository — Audit Log
Data access layer for structured tool operation audit events in SQLite.
All timestamps are UTC. Provides immutable record storage with filtering,
pagination, and retention management.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_event import AuditEventModel
from app.tools.audit_models import (
    AuditEvent,
    AuditEventType,
    ensure_sanitized_payload,
    make_audit_event,
)

# Hard limits to guard against pathological queries
_MAX_LIMIT = 500
_DEFAULT_LIMIT = 50
_MAX_OFFSET = 100_000


def _normalize_dt(dt: datetime | None) -> datetime | None:
    """Normalize datetimes to UTC-aware values (SQLite may return naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class AuditLogRepository:
    """
    Repository for AuditEvent persistence.
    Provides structured, immutable audit trail for tool operations.
    There is no update() — records are append-only aside from retention delete_before().
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, event: AuditEvent) -> AuditEvent:
        """
        Persist an AuditEvent to the database.
        Payloads are re-sanitized so callers cannot bypass sanitize_payload
        by constructing AuditEvent(arguments=...) directly.
        Timestamp from the domain object is kept (factory sets UTC now).
        """
        if not isinstance(event, AuditEvent):
            raise TypeError(f"Expected AuditEvent, got {type(event).__name__}")

        model = AuditEventModel(
            id=event.id,
            timestamp=event.timestamp,
            event_type=event.event_type.value,
            reason=event.reason,
            request_id=event.request_id,
            tool_id=event.tool_id,
            action=event.action,
            risk_level=event.risk_level,
            autonomy_mode=event.autonomy_mode,
            decision=event.decision,
            rule_id=event.rule_id,
            grant_id=event.grant_id,
            execution_status=event.execution_status,
            success=event.success,
            error_code=event.error_code,
            error_message=event.error_message,
            execution_time_ms=event.execution_time_ms,
            arguments=ensure_sanitized_payload(event.arguments),
            result=ensure_sanitized_payload(event.result),
        )
        self._session.add(model)
        await self._session.flush()
        return event.model_copy(
            update={
                "arguments": model.arguments,
                "result": model.result,
            }
        )

    async def get(self, event_id: str) -> AuditEvent | None:
        """Return AuditEvent by ID, or None if not found."""
        result = await self._session.execute(
            select(AuditEventModel).where(AuditEventModel.id == event_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def list(
        self,
        *,
        tool_id: str | None = None,
        request_id: str | None = None,
        event_type: AuditEventType | str | None = None,
        decision: str | None = None,
        from_timestamp: datetime | None = None,
        to_timestamp: datetime | None = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[AuditEvent, ...]:
        """
        Return a tuple of AuditEvents matching the given filters.
        Results are ordered by timestamp ascending (oldest first) for
        deterministic audit trail review. Pagination via limit/offset.
        """
        limit = max(1, min(limit, _MAX_LIMIT))
        offset = max(0, min(offset, _MAX_OFFSET))

        query = select(AuditEventModel)
        query = self._apply_filters(
            query, tool_id, request_id, event_type, decision, from_timestamp, to_timestamp
        )
        query = query.order_by(AuditEventModel.timestamp.asc(), AuditEventModel.id.asc())
        query = query.limit(limit).offset(offset)

        result = await self._session.execute(query)
        models = list(result.scalars().all())
        return tuple(self._to_domain(m) for m in models)

    async def count(
        self,
        *,
        tool_id: str | None = None,
        request_id: str | None = None,
        event_type: AuditEventType | str | None = None,
        decision: str | None = None,
        from_timestamp: datetime | None = None,
        to_timestamp: datetime | None = None,
    ) -> int:
        """Return total count of events matching the given filters."""
        query = select(func.count()).select_from(AuditEventModel)
        query = self._apply_filters(
            query, tool_id, request_id, event_type, decision, from_timestamp, to_timestamp
        )
        result = await self._session.execute(query)
        return result.scalar_one()

    async def delete_before(self, timestamp: datetime) -> int:
        """
        Delete all audit events with timestamp strictly before the given datetime.
        Returns count of deleted records. Used for retention management.
        Not exposed as a public HTTP API.
        """
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        stmt = delete(AuditEventModel).where(AuditEventModel.timestamp < timestamp)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_filters(query, tool_id, request_id, event_type, decision, from_ts, to_ts):
        if tool_id is not None:
            query = query.where(AuditEventModel.tool_id == tool_id)
        if request_id is not None:
            query = query.where(AuditEventModel.request_id == request_id)
        if event_type is not None:
            et_str = event_type.value if isinstance(event_type, AuditEventType) else str(event_type)
            query = query.where(AuditEventModel.event_type == et_str)
        if decision is not None:
            query = query.where(AuditEventModel.decision == decision)
        if from_ts is not None:
            if from_ts.tzinfo is None:
                from_ts = from_ts.replace(tzinfo=timezone.utc)
            query = query.where(AuditEventModel.timestamp >= from_ts)
        if to_ts is not None:
            if to_ts.tzinfo is None:
                to_ts = to_ts.replace(tzinfo=timezone.utc)
            query = query.where(AuditEventModel.timestamp <= to_ts)
        return query

    @staticmethod
    def _to_domain(model: AuditEventModel) -> AuditEvent:
        """Reconstruct an immutable AuditEvent from an ORM model."""
        return AuditEvent.model_construct(
            id=model.id,
            timestamp=_normalize_dt(model.timestamp),
            event_type=AuditEventType(model.event_type),
            reason=model.reason or "unknown",
            request_id=model.request_id,
            tool_id=model.tool_id,
            action=model.action,
            risk_level=model.risk_level,
            autonomy_mode=model.autonomy_mode,
            decision=model.decision,
            rule_id=model.rule_id,
            grant_id=model.grant_id,
            execution_status=model.execution_status,
            success=model.success,
            error_code=model.error_code,
            error_message=model.error_message,
            execution_time_ms=model.execution_time_ms,
            arguments=model.arguments,
            result=model.result,
        )


__all__ = ["AuditLogRepository", "make_audit_event"]
