"""
Repository — Notifications

Data access layer for notification records in SQLite.

Idempotency is enforced by the unique constraint on (source_event_id, notification_type).
The repository uses savepoint-based handling so that IntegrityError does not
roll back the caller's transaction.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.notification import NotificationModel
from app.notifications.exceptions import NotificationIntegrityConflict
from app.notifications.models import Notification, NotificationSeverity, NotificationType


def _serialize_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    return json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _deserialize_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500
_MAX_OFFSET = 100_000


class NotificationRepository:
    """
    Data access layer for Notification records.

    Ordering: created_at DESC, notification_id DESC (recent first).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def create(self, notification: Notification) -> tuple[Notification, bool]:
        """
        Persist a notification.

        Returns (persisted Notification, True) if newly created.
        Returns (existing Notification, False) if a notification with the same
        (source_event_id, notification_type) already exists (idempotent by unique key).

        The unique key is the authoritative idempotency boundary; mutable fields
        are not compared.
        """
        if not isinstance(notification, Notification):
            raise TypeError(f"Expected Notification, got {type(notification).__name__}")

        created_at = _to_naive_utc(notification.created_at) or datetime.now(timezone.utc)
        updated_at = _to_naive_utc(notification.updated_at) or created_at
        read_at = _to_naive_utc(notification.read_at)
        expires_at = _to_naive_utc(notification.expires_at)

        model = NotificationModel(
            notification_id=notification.notification_id,
            created_at=created_at,
            updated_at=updated_at,
            notification_type=notification.notification_type,
            severity=notification.severity,
            title=notification.title,
            body=notification.body,
            source_event_id=notification.source_event_id,
            correlation_id=notification.correlation_id,
            task_id=notification.task_id,
            plan_id=notification.plan_id,
            run_id=notification.run_id,
            execution_id=notification.execution_id,
            read_at=read_at,
            expires_at=expires_at,
            notification_metadata=_serialize_metadata(notification.metadata),
        )

        try:
            async with self._session.begin_nested():
                self._session.add(model)
                await self._session.flush()
                await self._session.refresh(model)
        except IntegrityError as exc:
            existing = await self.get_by_source_event(
                notification.source_event_id, notification.notification_type
            )
            if existing is not None:
                return existing, False
            raise NotificationIntegrityConflict(
                source_event_id=notification.source_event_id,
                notification_type=notification.notification_type,
            ) from exc

        return self._to_domain(model), True

    async def get(self, notification_id: str) -> Notification | None:
        """Return Notification by notification_id, or None if not found."""
        result = await self._session.execute(
            select(NotificationModel).where(NotificationModel.notification_id == notification_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_source_event(
        self, source_event_id: str, notification_type: str
    ) -> Notification | None:
        """Return Notification by (source_event_id, notification_type), or None."""
        result = await self._session.execute(
            select(NotificationModel).where(
                NotificationModel.source_event_id == source_event_id,
                NotificationModel.notification_type == notification_type,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def list(
        self,
        *,
        unread_only: bool = False,
        notification_type: str | None = None,
        severity: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        execution_id: str | None = None,
        correlation_id: str | None = None,
        from_created_at: datetime | None = None,
        to_created_at: datetime | None = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[Notification, ...]:
        """
        Return notifications matching the given filters.

        Ordering: created_at DESC, notification_id DESC (recent first).
        Pagination: limit/offset with hard caps.
        """
        limit = max(1, min(limit, _MAX_LIMIT))
        offset = max(0, min(offset, _MAX_OFFSET))

        query = select(NotificationModel)
        query = self._apply_filters(
            query,
            unread_only=unread_only,
            notification_type=notification_type,
            severity=severity,
            task_id=task_id,
            plan_id=plan_id,
            execution_id=execution_id,
            correlation_id=correlation_id,
            from_created_at=from_created_at,
            to_created_at=to_created_at,
        )
        query = query.order_by(
            NotificationModel.created_at.desc(), NotificationModel.notification_id.desc()
        )
        query = query.limit(limit).offset(offset)

        result = await self._session.execute(query)
        models = list(result.scalars().all())
        return tuple(self._to_domain(m) for m in models)

    async def count(
        self,
        *,
        unread_only: bool = False,
        notification_type: str | None = None,
        severity: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        execution_id: str | None = None,
        correlation_id: str | None = None,
        from_created_at: datetime | None = None,
        to_created_at: datetime | None = None,
    ) -> int:
        """Return count of notifications matching the given filters."""
        query = select(func.count()).select_from(NotificationModel)
        query = self._apply_filters(
            query,
            unread_only=unread_only,
            notification_type=notification_type,
            severity=severity,
            task_id=task_id,
            plan_id=plan_id,
            execution_id=execution_id,
            correlation_id=correlation_id,
            from_created_at=from_created_at,
            to_created_at=to_created_at,
        )
        result = await self._session.execute(query)
        return result.scalar_one() or 0

    async def mark_read(
        self, notification_id: str, read_at: datetime | None = None
    ) -> Notification | None:
        """
        Mark notification as read.

        Returns the updated Notification, or None if not found.
        """
        if read_at is None:
            read_at = datetime.now(timezone.utc)
        naive_read_at = _to_naive_utc(read_at)

        stmt = (
            update(NotificationModel)
            .where(NotificationModel.notification_id == notification_id)
            .values(read_at=naive_read_at, updated_at=naive_read_at)
            .returning(NotificationModel)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def mark_all_read(
        self,
        *,
        task_id: str | None = None,
        plan_id: str | None = None,
        execution_id: str | None = None,
        correlation_id: str | None = None,
        before: datetime | None = None,
    ) -> int:
        """
        Mark all matching notifications as read.

        Returns the count of updated rows.
        """
        read_at = datetime.now(timezone.utc)
        naive_read_at = _to_naive_utc(read_at)

        stmt = (
            update(NotificationModel)
            .where(NotificationModel.read_at.is_(None))
            .values(read_at=naive_read_at, updated_at=naive_read_at)
        )
        if task_id is not None:
            stmt = stmt.where(NotificationModel.task_id == task_id)
        if plan_id is not None:
            stmt = stmt.where(NotificationModel.plan_id == plan_id)
        if execution_id is not None:
            stmt = stmt.where(NotificationModel.execution_id == execution_id)
        if correlation_id is not None:
            stmt = stmt.where(NotificationModel.correlation_id == correlation_id)
        if before is not None:
            naive_before = _to_naive_utc(before)
            stmt = stmt.where(NotificationModel.created_at <= naive_before)

        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def delete_expired(self, before: datetime) -> int:
        """
        Delete expired notifications.

        Returns count of deleted records.
        """
        naive_before = _to_naive_utc(before)
        stmt = delete(NotificationModel).where(
            NotificationModel.expires_at.is_not(None),
            NotificationModel.expires_at <= naive_before,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    # ── Private helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _apply_filters(query, **filters):
        if filters.get("unread_only"):
            query = query.where(NotificationModel.read_at.is_(None))
        if filters.get("notification_type") is not None:
            query = query.where(NotificationModel.notification_type == filters["notification_type"])
        if filters.get("severity") is not None:
            query = query.where(NotificationModel.severity == filters["severity"])
        if filters.get("task_id") is not None:
            query = query.where(NotificationModel.task_id == filters["task_id"])
        if filters.get("plan_id") is not None:
            query = query.where(NotificationModel.plan_id == filters["plan_id"])
        if filters.get("execution_id") is not None:
            query = query.where(NotificationModel.execution_id == filters["execution_id"])
        if filters.get("correlation_id") is not None:
            query = query.where(NotificationModel.correlation_id == filters["correlation_id"])
        if filters.get("from_created_at") is not None:
            from_ts = _to_naive_utc(filters["from_created_at"])
            query = query.where(NotificationModel.created_at >= from_ts)
        if filters.get("to_created_at") is not None:
            to_ts = _to_naive_utc(filters["to_created_at"])
            query = query.where(NotificationModel.created_at <= to_ts)
        return query

    @staticmethod
    def _to_domain(model: NotificationModel | None) -> Notification | None:
        if model is None:
            return None
        return Notification(
            notification_id=model.notification_id,
            created_at=_to_aware_utc(model.created_at),
            updated_at=_to_aware_utc(model.updated_at),
            notification_type=model.notification_type,
            severity=model.severity,
            title=model.title,
            body=model.body,
            source_event_id=model.source_event_id,
            correlation_id=model.correlation_id,
            task_id=model.task_id,
            plan_id=model.plan_id,
            run_id=model.run_id,
            execution_id=model.execution_id,
            read_at=_to_aware_utc(model.read_at),
            expires_at=_to_aware_utc(model.expires_at),
            metadata=_deserialize_metadata(model.notification_metadata),
        )


__all__ = ["NotificationRepository"]
