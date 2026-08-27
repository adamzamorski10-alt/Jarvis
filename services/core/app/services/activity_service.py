"""
Service — Activity Events
Business logic for activity/audit log management.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.activity import ActivityEvent
from app.repositories.activity_repository import ActivityRepository


class ActivityService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = ActivityRepository(session)

    async def create_event(
        self,
        message: str,
        level: str = "INFO",
        category: str = "system",
        details: dict | None = None,
    ) -> ActivityEvent:
        """Create and persist an activity event."""
        return await self._repo.create(
            level=level,
            category=category,
            message=message,
            details=details,
        )

    async def get_events(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
        level: str | None = None,
    ) -> tuple[list[ActivityEvent], int]:
        """Return paginated activity events with total count."""
        return await self._repo.get_paginated(
            limit=min(limit, 200),  # Cap at 200 per request
            offset=offset,
            category=category,
            level=level,
        )

    async def get_recent(self, limit: int = 20) -> list[ActivityEvent]:
        """Return the most recent activity events."""
        return await self._repo.get_recent(limit=min(limit, 100))
