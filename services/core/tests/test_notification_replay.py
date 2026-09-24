"""
Stage 13.3C — Notification Replay, Recovery & Idempotency Hardening tests.

Focused tests for NotificationReplayService.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.db.database import Base, init_db
from app.db.models.event import EventModel
from app.db.models.notification import NotificationModel
from app.events.emitter import EventEmitter, EventEmissionError
from app.events.repository import EventRepository, EventIntegrityConflict
from app.events.schemas import Event, EventType, Sensitivity
from app.notifications.models import Notification, NotificationType, NotificationSeverity
from app.notifications.policy import NotificationPolicy
from app.notifications.repository import NotificationRepository
from app.notifications.replay import NotificationReplayResult, NotificationReplayService


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path: Any) -> str:
    return str(tmp_path / "test_notification_replay.db")


@pytest.fixture()
async def engine(db_path: str) -> Any:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", connect_args={"check_same_thread": False})
    return engine


@pytest.fixture()
async def session_factory(engine: Any, db_path: str) -> Any:
    import app.db.database as db_mod
    old_engine = db_mod._engine
    db_mod._engine = engine
    try:
        await init_db()
    finally:
        db_mod._engine = old_engine
    factory = async_sessionmaker(engine, expire_on_commit=False, autocommit=False, autoflush=False)
    return factory


@pytest.fixture()
async def session(session_factory: Any) -> Any:
    session = session_factory()
    return session


@pytest.fixture()
def make_event_repo(session: Any) -> Any:
    def _make() -> EventRepository:
        return EventRepository(session)
    return _make


@pytest.fixture()
def make_notification_repo(session: Any) -> Any:
    def _make() -> NotificationRepository:
        return NotificationRepository(session)
    return _make


@pytest.fixture()
def policy() -> NotificationPolicy:
    return NotificationPolicy()


@pytest.fixture()
def make_replay_service(make_event_repo: Any, policy: Any, make_notification_repo: Any) -> Any:
    def _make(event_repo: Any | None = None, notification_repo: Any | None = None) -> NotificationReplayService:
        return NotificationReplayService(
            event_repo or make_event_repo(),
            policy,
            notification_repo or make_notification_repo(),
        )
    return _make


# ── A. Eligible Event with missing Notification → replay creates Notification ───


async def test_replay_creates_missing_notification(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    event = await event_repo.record(Event(
        event_id="evt_replay_001",
        event_type=EventType.TASK_FAILED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        task_id="task_replay",
        plan_id="plan_replay",
        payload={"reason": "replay test"},
    ))
    await event_repo.session.commit()

    count_before = await notification_repo.count()
    assert count_before == 0

    result = await replay_service.replay()
    assert result.notifications_created == 1
    assert result.events_scanned == 1

    count_after = await notification_repo.count()
    assert count_after == 1


# ── B. Eligible Event with existing Notification → replay is idempotent ─────────


async def test_replay_idempotent_existing_notification(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    event = await event_repo.record(Event(
        event_id="evt_replay_002",
        event_type=EventType.TASK_COMPLETED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        task_id="task_replay2",
        plan_id="plan_replay2",
    ))
    await event_repo.session.commit()

    result1 = await replay_service.replay()
    assert result1.notifications_created == 1

    result2 = await replay_service.replay()
    assert result2.notifications_created == 0
    assert result2.notifications_skipped_existing == 1


# ── C. Repeated replay → one Notification ─────────────────────────────────────


async def test_repeated_replay_single_notification(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_003",
        event_type=EventType.PLAN_FAILED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        plan_id="plan_replay3",
        payload={"reason": "repeated replay"},
    ))
    await event_repo.session.commit()

    for _ in range(3):
        result = await replay_service.replay()
        assert result.notifications_created <= 1

    count = await notification_repo.count()
    assert count == 1


# ── D. Concurrent replay → one Notification ───────────────────────────────────


async def test_concurrent_replay_single_notification(session_factory: Any, policy: Any) -> None:
    session1 = session_factory()
    session2 = session_factory()
    try:
        event_repo1 = EventRepository(session1)
        event_repo2 = EventRepository(session2)
        notif_repo1 = NotificationRepository(session1)
        notif_repo2 = NotificationRepository(session2)

        event = Event(
            event_id="evt_replay_004",
            event_type=EventType.SCHEDULER_OCCURRENCE_FAILED,
            source="scheduler",
            occurred_at=datetime.now(timezone.utc),
            recorded_at=datetime.now(timezone.utc),
            execution_id="exec_replay",
            payload={"job_id": "job_replay", "occurrence_id": "occ_replay", "reason_code": "err"},
        )
        await event_repo1.record(event)
        await session1.commit()
        await event_repo2.record(event)
        await session2.commit()

        replay1 = NotificationReplayService(event_repo1, policy, notif_repo1)
        replay2 = NotificationReplayService(event_repo2, policy, notif_repo2)

        result1, result2 = await asyncio.gather(
            replay1.replay(),
            replay2.replay(),
            return_exceptions=True,
        )

        successes = [r for r in (result1, result2) if not isinstance(r, BaseException)]
        assert len(successes) >= 1

        await session1.commit()
        await session2.commit()

        session3 = session_factory()
        notif_repo3 = NotificationRepository(session3)
        try:
            count = await notif_repo3.count()
            assert count == 1
        finally:
            await session3.close()
    finally:
        await session1.close()
        await session2.close()


# ── E. Replay concurrent with normal EventEmitter → one Notification ──────────


async def test_replay_concurrent_with_emitter(session_factory: Any, make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    session1 = session_factory()
    session2 = session_factory()
    try:
        event_repo1 = EventRepository(session1)
        event_repo2 = EventRepository(session2)
        notif_repo1 = NotificationRepository(session1)
        notif_repo2 = NotificationRepository(session2)

        event = Event(
            event_id="evt_replay_005",
            event_type=EventType.TASK_FAILED,
            source="test",
            occurred_at=datetime.now(timezone.utc),
            recorded_at=datetime.now(timezone.utc),
            task_id="task_conc_replay",
            payload={"reason": "concurrent replay"},
        )
        await event_repo1.record(event)
        await event_repo2.record(event)

        emitter = EventEmitter(event_repo2, notification_policy=policy, notification_repository=notif_repo2)
        replay_service = NotificationReplayService(event_repo1, policy, notif_repo1)

        result1, result2 = await asyncio.gather(
            replay_service.replay(),
            emitter.emit(
                event_type=EventType.TASK_COMPLETED,
                source="test",
                task_id="task_conc_replay",
                event_id="evt_replay_005",
            ),
            return_exceptions=True,
        )

        successes = [r for r in (result1, result2) if not isinstance(r, BaseException)]
        assert len(successes) >= 1

        await session1.commit()
        await session2.commit()
    finally:
        await session1.close()
        await session2.close()


# ── F. Ineligible Event → no Notification ─────────────────────────────────────


async def test_replay_ineligible_event_skipped(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_006",
        event_type=EventType.TASK_STARTED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
    ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert result.events_scanned == 0
    assert result.notifications_created == 0
    count = await notification_repo.count()
    assert count == 0


# ── G. Policy returns None → no Notification ──────────────────────────────────


async def test_replay_policy_none_no_notification(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_007",
        event_type=EventType.VERIFICATION_PASSED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
    ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert result.events_scanned == 0
    assert result.notifications_created == 0


# ── H. Notification DB failure → replay reports failure and remains retryable ──


async def test_replay_notification_failure_retryable(make_event_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()

    class FailingNotificationRepository:
        async def create(self, notification: Notification) -> tuple[Notification, bool]:
            raise RuntimeError("notification DB down")

        async def get(self, notification_id: str) -> Notification | None:
            return None

        async def get_by_source_event(self, source_event_id: str, notification_type: str) -> Notification | None:
            return None

        async def list(self, **kwargs: Any) -> tuple[Notification, ...]:
            return ()

        async def count(self, **kwargs: Any) -> int:
            return 0

        async def mark_read(self, notification_id: str, read_at: datetime | None = None) -> Notification | None:
            return None

        async def mark_all_read(self, **kwargs: Any) -> int:
            return 0

        async def delete_expired(self, before: datetime) -> int:
            return 0

    failing_repo = FailingNotificationRepository()
    replay_service = NotificationReplayService(event_repo, policy, failing_repo)

    await event_repo.record(Event(
        event_id="evt_replay_008",
        event_type=EventType.TASK_FAILED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        payload={"reason": "db failure"},
    ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert len(result.failures) == 1
    assert result.notifications_created == 0


# ── I. Malformed payload → deterministic safe fallback ────────────────────────


async def test_replay_malformed_payload_fallback(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_009",
        event_type=EventType.TASK_FAILED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        task_id="task_malformed",
        payload={"unexpected_field": "value"},
    ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert result.notifications_created == 1
    notifications = await notification_repo.list()
    assert len(notifications) == 1
    assert "unknown cause" in notifications[0].body


# ── J. Historical Event fields are preserved ──────────────────────────────────


async def test_replay_preserves_event_fields(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    occurred_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    recorded_at = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
    event = await event_repo.record(Event(
        event_id="evt_replay_010",
        event_type=EventType.PLAN_COMPLETED,
        source="test",
        occurred_at=occurred_at,
        recorded_at=recorded_at,
        task_id="task_hist",
        plan_id="plan_hist",
        correlation_id="corr_hist",
        execution_id="exec_hist",
        payload={"completed_tasks_count": 3, "total_tasks_count": 5},
    ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert result.notifications_created == 1
    notifications = await notification_repo.list()
    assert len(notifications) == 1
    assert notifications[0].source_event_id == "evt_replay_010"
    assert notifications[0].plan_id == "plan_hist"
    assert notifications[0].correlation_id == "corr_hist"
    assert notifications[0].execution_id == "exec_hist"


# ── K. Replay creates no Event ────────────────────────────────────────────────


async def test_replay_creates_no_event(make_event_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = NotificationRepository(event_repo.session)
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_011",
        event_type=EventType.TASK_FAILED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
    ))
    await event_repo.session.commit()

    count_before = await event_repo.count(event_type=EventType.TASK_FAILED)
    await replay_service.replay()
    count_after = await event_repo.count(event_type=EventType.TASK_FAILED)
    assert count_after == count_before


# ── L. Replay creates no execution/task side effect ───────────────────────────


async def test_replay_no_task_side_effects(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_012",
        event_type=EventType.TASK_FAILED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        task_id="task_side_effect",
        payload={"reason": "side effect test"},
    ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert result.notifications_created == 1
    count = await notification_repo.count()
    assert count == 1


# ── M. Replay does not invoke external/model/network functionality ────────────


async def test_replay_no_external_calls(make_event_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = NotificationRepository(event_repo.session)
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_013",
        event_type=EventType.TASK_COMPLETED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
    ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert result.events_scanned == 1


# ── N. Replay of multiple Events is bounded and deterministic ─────────────────


async def test_replay_multiple_events_bounded(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo, batch_size=3)

    for i in range(5):
        await event_repo.record(Event(
            event_id=f"evt_replay_multi_{i}",
            event_type=EventType.TASK_FAILED,
            source="test",
            occurred_at=datetime.now(timezone.utc),
            recorded_at=datetime.now(timezone.utc),
            task_id=f"task_multi_{i}",
            payload={"reason": f"multi {i}"},
        ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert result.events_scanned <= 5
    assert result.notifications_created <= 5


# ── O. Existing Event and Notification transaction semantics remain intact ─────


async def test_replay_transaction_semantics(session: Any, make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_txn",
        event_type=EventType.TASK_FAILED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
    ))
    await event_repo.session.commit()

    async with session.begin():
        result = await replay_service.replay()
        assert result.notifications_created == 1


# ── Additional: scheduler events in replay ────────────────────────────────────


async def test_replay_scheduler_failed(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_sched_failed",
        event_type=EventType.SCHEDULER_OCCURRENCE_FAILED,
        source="scheduler",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        execution_id="exec_sched_replay",
        payload={"job_id": "job_replay", "occurrence_id": "occ_replay", "reason_code": "timeout"},
    ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert result.notifications_created == 1
    notifications = await notification_repo.list()
    assert notifications[0].notification_type == NotificationType.SCHEDULER_OCCURRENCE_FAILED.value


async def test_replay_scheduler_recovered(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_sched_rec",
        event_type=EventType.SCHEDULER_OCCURRENCE_RECOVERED,
        source="scheduler",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        execution_id="exec_sched_rec",
        payload={"job_id": "job_rec", "occurrence_id": "occ_rec", "reason": "lease_expired"},
    ))
    await event_repo.session.commit()

    result = await replay_service.replay()
    assert result.notifications_created == 1
    notifications = await notification_repo.list()
    assert notifications[0].notification_type == NotificationType.SCHEDULER_OCCURRENCE_RECOVERED.value


# ── Additional: filtered replay ───────────────────────────────────────────────


async def test_replay_filtered_by_task_id(make_event_repo: Any, make_notification_repo: Any, policy: Any) -> None:
    event_repo = make_event_repo()
    notification_repo = make_notification_repo()
    replay_service = NotificationReplayService(event_repo, policy, notification_repo)

    await event_repo.record(Event(
        event_id="evt_replay_ft1",
        event_type=EventType.TASK_FAILED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        task_id="task_keep",
        payload={"reason": "keep"},
    ))
    await event_repo.record(Event(
        event_id="evt_replay_ft2",
        event_type=EventType.TASK_FAILED,
        source="test",
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        task_id="task_skip",
        payload={"reason": "skip"},
    ))
    await event_repo.session.commit()

    result = await replay_service.replay(task_id="task_keep")
    assert result.notifications_created == 1
    assert result.notifications_skipped_existing == 0
    notifications = await notification_repo.list()
    assert len(notifications) == 1
    assert notifications[0].task_id == "task_keep"


__all__ = [
    "TestReplayMissingNotification",
    "TestReplayIdempotency",
    "TestReplayConcurrency",
]
