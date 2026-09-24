"""
Notification Replay Service — Recovery for missing Notifications.

This service recovers durable gaps where an Event exists but its Notification
was not created due to policy failure, infrastructure failure, or process crash.

The service is pure recovery: it reads existing Events, applies the
NotificationPolicy, and creates missing Notifications. It does NOT create Events,
emit Events, execute tools, or invoke external systems.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.events.repository import EventRepository
from app.events.schemas import EventType
from app.notifications.models import Notification, NotificationType
from app.notifications.policy import NotificationDraft, NotificationPolicy
from app.notifications.repository import NotificationRepository

logger = logging.getLogger(__name__)

_REPLAY_ELIGIBLE_TYPES = [
    EventType.TASK_COMPLETED,
    EventType.TASK_FAILED,
    EventType.PLAN_COMPLETED,
    EventType.PLAN_FAILED,
    EventType.SCHEDULER_OCCURRENCE_FAILED,
    EventType.SCHEDULER_OCCURRENCE_RECOVERED,
]


class NotificationReplayResult:
    """Result of a replay operation."""

    __slots__ = (
        "events_scanned",
        "notifications_created",
        "notifications_skipped_existing",
        "notifications_skipped_policy_none",
        "failures",
    )

    def __init__(
        self,
        events_scanned: int = 0,
        notifications_created: int = 0,
        notifications_skipped_existing: int = 0,
        notifications_skipped_policy_none: int = 0,
        failures: list[tuple[str, str]] | None = None,
    ) -> None:
        self.events_scanned = events_scanned
        self.notifications_created = notifications_created
        self.notifications_skipped_existing = notifications_skipped_existing
        self.notifications_skipped_policy_none = notifications_skipped_policy_none
        self.failures = failures or []

    def __repr__(self) -> str:
        return (
            f"NotificationReplayResult("
            f"scanned={self.events_scanned}, "
            f"created={self.notifications_created}, "
            f"skipped_existing={self.notifications_skipped_existing}, "
            f"skipped_policy_none={self.notifications_skipped_policy_none}, "
            f"failures={len(self.failures)})"
        )


class NotificationReplayService:
    """
    Replay service for recovering missing Notifications from existing Events.

    The service queries Events of eligible types and creates Notifications
    for any that are missing. It is safe to run repeatedly, concurrently,
    and after partial failures.
    """

    def __init__(
        self,
        event_repository: EventRepository,
        notification_policy: NotificationPolicy,
        notification_repository: NotificationRepository,
        *,
        batch_size: int = 100,
    ) -> None:
        self._event_repo = event_repository
        self._policy = notification_policy
        self._notification_repo = notification_repository
        self._batch_size = max(1, min(batch_size, 1000))

    async def replay(
        self,
        *,
        event_types: list[EventType] | None = None,
        from_occurred_at: datetime | None = None,
        to_occurred_at: datetime | None = None,
        correlation_id: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        execution_id: str | None = None,
        limit: int | None = None,
    ) -> NotificationReplayResult:
        """
        Replay eligible Events to create missing Notifications.

        Args:
            event_types: Event types to replay. Defaults to V1 eligible types.
            from_occurred_at: Only replay Events occurred at or after this time.
            to_occurred_at: Only replay Events occurred before this time.
            correlation_id: Only replay Events with this correlation_id.
            task_id: Only replay Events for this task.
            plan_id: Only replay Events for this plan.
            execution_id: Only replay Events for this execution.
            limit: Maximum number of Events to process. Defaults to batch_size.

        Returns:
            NotificationReplayResult with counts and any failures.
        """
        result = NotificationReplayResult()
        types = event_types or _REPLAY_ELIGIBLE_TYPES
        max_events = limit or self._batch_size

        for event_type in types:
            events = await self._event_repo.list(
                event_type=event_type,
                from_occurred_at=from_occurred_at,
                to_occurred_at=to_occurred_at,
                correlation_id=correlation_id,
                task_id=task_id,
                plan_id=plan_id,
                execution_id=execution_id,
                limit=max_events,
            )
            for event in events:
                result.events_scanned += 1
                try:
                    await self._replay_single_event(event, result)
                except Exception as exc:
                    logger.exception(
                        "Failed to replay event %s: %s",
                        event.event_id,
                        exc,
                    )
                    result.failures.append((event.event_id, str(exc)))

        return result

    async def _replay_single_event(
        self, event: Any, result: NotificationReplayResult
    ) -> None:
        draft = self._policy.process(event)
        if draft is None:
            result.notifications_skipped_policy_none += 1
            return

        existing = await self._notification_repo.get_by_source_event(
            draft.source_event_id, draft.notification_type
        )
        if existing is not None:
            result.notifications_skipped_existing += 1
            return

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
        notification, created = await self._notification_repo.create(notification)
        if created:
            result.notifications_created += 1
        else:
            result.notifications_skipped_existing += 1
        logger.info(
            "Replay created notification",
            extra={
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "notification_type": draft.notification_type,
            },
        )


__all__ = [
    "NotificationReplayService",
    "NotificationReplayResult",
]
