"""
History Service — Read-side query layer over the durable Event Store.

History is a query layer, not a cache or materialized view.
It does not modify, interpret, or authorize — it retrieves.

AUTHORIZATION BOUNDARY:
  HistoryService does NOT invent a new authorization framework.
  It does NOT grant access or bypass PermissionEngine.
  API-level authorization is OUT OF SCOPE.
  Task-scoped methods verify entity existence via TaskRepository only.
  Plan history queries events directly by plan_id without deriving
  history from tasks; a plan may have events even when it currently
  has no associated tasks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.events.repository import EventRepository
from app.events.schemas import Event, EventType, Sensitivity
from app.repositories.task_repository import TaskRepository


class HistoryEntry(BaseModel):
    """
    Typed read representation of a durable Event.

    HistoryEntry is a read model over Event. It preserves all important
    durable Event information. The Event Store remains the sole source of truth.
    HistoryEntry must not become a second source of truth.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=False)

    event_id: str
    event_type: EventType
    occurred_at: datetime
    recorded_at: datetime
    source: str
    event_version: str
    correlation_id: str | None
    causation_id: str | None
    task_id: str | None
    plan_id: str | None
    run_id: str | None
    execution_id: str | None
    actor: str | None
    payload: dict[str, Any] | None
    metadata: dict[str, Any] | None
    sensitivity: Sensitivity

    @classmethod
    def from_event(cls, event: Event) -> HistoryEntry:
        return cls(
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            recorded_at=event.recorded_at,
            source=event.source,
            event_version=event.event_version,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            task_id=event.task_id,
            plan_id=event.plan_id,
            run_id=event.run_id,
            execution_id=event.execution_id,
            actor=event.actor,
            payload=event.payload,
            metadata=event.metadata,
            sensitivity=event.sensitivity,
        )


class HistoryPage(BaseModel):
    """
    Paginated history result.
    """
    model_config = ConfigDict(frozen=True)

    entries: tuple[HistoryEntry, ...]
    total: int
    limit: int
    offset: int
    has_more: bool

    @field_validator("limit")
    @classmethod
    def _validate_limit(cls, value: int) -> int:
        if value < 1 or value > 500:
            raise ValueError(f"limit must be between 1 and 500, got {value}")
        return value

    @field_validator("offset")
    @classmethod
    def _validate_offset(cls, value: int) -> int:
        if value < 0 or value > 100_000:
            raise ValueError(f"offset must be between 0 and 100000, got {value}")
        return value


class HistoryService:
    """
    Read-side query layer over the durable Event Store.

    HistoryService does not modify, interpret, or authorize — it retrieves.
    It relies on EventRepository for all data access.
    """

    def __init__(
        self,
        event_repository: EventRepository,
        task_repository: TaskRepository | None = None,
    ) -> None:
        self._event_repo = event_repository
        self._task_repo = task_repository

    async def get_task_history(
        self,
        task_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> HistoryPage:
        """
        Return history events for a specific task.

        Verifies task existence via TaskRepository before returning events.
        If the task does not exist, returns an empty page.
        """
        HistoryPage._validate_limit(limit)
        HistoryPage._validate_offset(offset)

        if self._task_repo is not None:
            task = await self._task_repo.get_task(task_id)
            if task is None:
                return HistoryPage(entries=(), total=0, limit=limit, offset=offset, has_more=False)

        events = await self._event_repo.list(task_id=task_id, limit=limit, offset=offset)
        total = await self._event_repo.count(task_id=task_id)
        entries = tuple(HistoryEntry.from_event(e) for e in events)
        has_more = offset + len(entries) < total
        return HistoryPage(entries=entries, total=total, limit=limit, offset=offset, has_more=has_more)

    async def get_plan_history(
        self,
        plan_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> HistoryPage:
        """
        Return history events for a specific plan.

        Queries events directly by the persisted plan_id. A plan may have
        events even when it currently has no associated tasks. Absence of
        tasks is not treated as absence of plan history.
        """
        HistoryPage._validate_limit(limit)
        HistoryPage._validate_offset(offset)

        events = await self._event_repo.list(plan_id=plan_id, limit=limit, offset=offset)
        total = await self._event_repo.count(plan_id=plan_id)
        entries = tuple(HistoryEntry.from_event(e) for e in events)
        has_more = offset + len(entries) < total
        return HistoryPage(entries=entries, total=total, limit=limit, offset=offset, has_more=has_more)

    async def get_execution_history(
        self,
        execution_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> HistoryPage:
        """
        Return history events for a specific execution.
        """
        HistoryPage._validate_limit(limit)
        HistoryPage._validate_offset(offset)

        events = await self._event_repo.list(execution_id=execution_id, limit=limit, offset=offset)
        total = await self._event_repo.count(execution_id=execution_id)
        entries = tuple(HistoryEntry.from_event(e) for e in events)
        has_more = offset + len(entries) < total
        return HistoryPage(entries=entries, total=total, limit=limit, offset=offset, has_more=has_more)

    async def get_correlation_history(
        self,
        correlation_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> HistoryPage:
        """
        Return history events for a specific correlation group.
        """
        HistoryPage._validate_limit(limit)
        HistoryPage._validate_offset(offset)

        events = await self._event_repo.list(correlation_id=correlation_id, limit=limit, offset=offset)
        total = await self._event_repo.count(correlation_id=correlation_id)
        entries = tuple(HistoryEntry.from_event(e) for e in events)
        has_more = offset + len(entries) < total
        return HistoryPage(entries=entries, total=total, limit=limit, offset=offset, has_more=has_more)

    async def get_history_between(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int = 50,
        offset: int = 0,
        event_type: EventType | str | None = None,
    ) -> HistoryPage:
        """
        Return history events within a time range.

        Raises ValueError if start > end or if either datetime is timezone-naive.
        """
        if start.tzinfo is None:
            raise ValueError("start datetime must be timezone-aware (UTC)")
        if end.tzinfo is None:
            raise ValueError("end datetime must be timezone-aware (UTC)")
        if start > end:
            raise ValueError(f"start ({start}) must not be after end ({end})")

        HistoryPage._validate_limit(limit)
        HistoryPage._validate_offset(offset)

        events = await self._event_repo.list(
            from_occurred_at=start,
            to_occurred_at=end,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )
        total = await self._event_repo.count(
            from_occurred_at=start,
            to_occurred_at=end,
            event_type=event_type,
        )
        entries = tuple(HistoryEntry.from_event(e) for e in events)
        has_more = offset + len(entries) < total
        return HistoryPage(entries=entries, total=total, limit=limit, offset=offset, has_more=has_more)

    async def get_recent_history(
        self,
        *,
        limit: int = 50,
    ) -> HistoryPage:
        """
        Return the most recently recorded events.

        Ordering: recorded_at DESC, event_id DESC
        """
        HistoryPage._validate_limit(limit)

        events = await self._event_repo.list_recent(limit=limit)
        total = await self._event_repo.count()
        entries = tuple(HistoryEntry.from_event(e) for e in events)
        has_more = total > limit
        return HistoryPage(entries=entries, total=total, limit=limit, offset=0, has_more=has_more)
