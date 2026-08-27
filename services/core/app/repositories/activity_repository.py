"""
Repository — Activity Events
Data access layer for activity/audit log.
"""
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.activity import ActivityEvent


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        level: str,
        category: str,
        message: str,
        details: dict | None = None,
    ) -> ActivityEvent:
        event = ActivityEvent(
            level=level.upper(),
            category=category,
            message=message,
            details=details,
        )
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def get_paginated(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
        level: str | None = None,
    ) -> tuple[list[ActivityEvent], int]:
        base_query = select(ActivityEvent)
        count_query = select(func.count()).select_from(ActivityEvent)

        if category:
            base_query = base_query.where(ActivityEvent.category == category)
            count_query = count_query.where(ActivityEvent.category == category)
        if level:
            base_query = base_query.where(ActivityEvent.level == level.upper())
            count_query = count_query.where(ActivityEvent.level == level.upper())

        base_query = base_query.order_by(ActivityEvent.timestamp.desc())
        base_query = base_query.limit(limit).offset(offset)

        events_result = await self._session.execute(base_query)
        count_result = await self._session.execute(count_query)

        events = list(events_result.scalars().all())
        total = count_result.scalar_one()

        return events, total

    async def get_recent(self, limit: int = 20) -> list[ActivityEvent]:
        result = await self._session.execute(
            select(ActivityEvent)
            .order_by(ActivityEvent.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
