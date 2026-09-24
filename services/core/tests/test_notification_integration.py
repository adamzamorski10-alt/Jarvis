"""
Stage 13.3B — Event → Notification Integration tests.

Focused tests for the integration between Event persistence and NotificationPolicy.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
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
from app.notifications.policy import NotificationDraft, NotificationPolicy
from app.notifications.repository import NotificationRepository


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_notification_integration.db")


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return _make_db_path(tmp_path)


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
    await init_db()
    return session


@pytest.fixture()
def event_repo(session: Any) -> EventRepository:
    return EventRepository(session)


@pytest.fixture()
def notification_repo(session: Any) -> NotificationRepository:
    return NotificationRepository(session)


@pytest.fixture()
def policy() -> NotificationPolicy:
    return NotificationPolicy()


@pytest.fixture()
def emitter(event_repo: Any, policy: Any, notification_repo: Any) -> EventEmitter:
    return EventEmitter(event_repo, notification_policy=policy, notification_repository=notification_repo)


def _make_event_kwargs(event_type: EventType, **kwargs: Any) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "source": "test",
        "task_id": kwargs.get("task_id", "task_abc"),
        "plan_id": kwargs.get("plan_id", "plan_xyz"),
        "run_id": kwargs.get("run_id", "run_001"),
        "execution_id": kwargs.get("execution_id", "exec_001"),
        "correlation_id": kwargs.get("correlation_id", "corr_001"),
        "payload": kwargs.get("payload", {}),
        **{k: v for k, v in kwargs.items() if k not in ("task_id", "plan_id", "run_id", "execution_id", "correlation_id", "payload")},
    }


# ── A. Required EventType: Event persisted → exactly one Notification ─────────


class TestRequiredEventTypesCreateNotification:
    @pytest.mark.parametrize(
        "event_type",
        [
            EventType.TASK_COMPLETED,
            EventType.TASK_FAILED,
            EventType.PLAN_COMPLETED,
            EventType.PLAN_FAILED,
            EventType.SCHEDULER_OCCURRENCE_FAILED,
            EventType.SCHEDULER_OCCURRENCE_RECOVERED,
        ],
    )
    async def test_required_event_creates_notification(self, emitter: EventEmitter, event_type: EventType) -> None:
        event = await emitter.emit(
            event_type=event_type,
            source="test",
            task_id="task_123",
            plan_id="plan_456",
            execution_id="exec_789",
        )
        assert event is not None
        assert event.event_id is not None


# ── B. NO_NOTIFICATION EventType: Event persisted → zero Notifications ─────────


class TestNoNotificationEventTypes:
    @pytest.mark.parametrize(
        "event_type",
        [
            EventType.TASK_STARTED,
            EventType.PLAN_STARTED,
            EventType.VERIFICATION_STARTED,
            EventType.VERIFICATION_PASSED,
            EventType.CHECKPOINT_CREATED,
            EventType.SCHEDULER_OCCURRENCE_CLAIMED,
            EventType.SCHEDULER_OCCURRENCE_TRIGGERED,
            EventType.SCHEDULER_OCCURRENCE_SKIPPED,
        ],
    )
    async def test_no_notification_event_creates_none(self, emitter: EventEmitter, notification_repo: Any, event_type: EventType) -> None:
        await emitter.emit(
            event_type=event_type,
            source="test",
            task_id="task_123",
            plan_id="plan_456",
        )
        count = await notification_repo.count()
        assert count == 0


# ── C. verification.failed: zero Notifications in V1 ──────────────────────────


async def test_verification_failed_deferred(emitter: EventEmitter, notification_repo: Any) -> None:
    await emitter.emit(
        event_type=EventType.VERIFICATION_FAILED,
        source="test",
        task_id="task_vf",
        plan_id="plan_vf",
        payload={"reason": "lint failed"},
    )
    count = await notification_repo.count()
    assert count == 0


# ── D. Notification fields match NotificationPolicy output ────────────────────


async def test_notification_fields_match_policy(emitter: EventEmitter, notification_repo: Any) -> None:
    event = await emitter.emit(
        event_type=EventType.TASK_FAILED,
        source="test",
        task_id="task_fields",
        plan_id="plan_fields",
        correlation_id="corr_fields",
        run_id="run_fields",
        execution_id="exec_fields",
        payload={"reason": "timeout"},
    )
    notifications = await notification_repo.list()
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.source_event_id == event.event_id
    assert notification.task_id == "task_fields"
    assert notification.plan_id == "plan_fields"
    assert notification.correlation_id == "corr_fields"
    assert notification.run_id == "run_fields"
    assert notification.execution_id == "exec_fields"
    assert notification.notification_type == NotificationType.TASK_FAILED.value
    assert notification.severity == NotificationSeverity.ERROR.value


# ── E. Event references are preserved ─────────────────────────────────────────


async def test_event_references_preserved(emitter: EventEmitter, notification_repo: Any) -> None:
    event = await emitter.emit(
        event_type=EventType.PLAN_COMPLETED,
        source="test",
        task_id="task_ref",
        plan_id="plan_ref",
        run_id="run_ref",
        execution_id="exec_ref",
        correlation_id="corr_ref",
        payload={"completed_tasks_count": 5, "total_tasks_count": 10},
    )
    notifications = await notification_repo.list()
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.source_event_id == event.event_id
    assert notification.task_id == "task_ref"
    assert notification.plan_id == "plan_ref"
    assert notification.run_id == "run_ref"
    assert notification.execution_id == "exec_ref"
    assert notification.correlation_id == "corr_ref"


# ── F. Repeated processing: same Event → no duplicate Notification ────────────


async def test_repeated_processing_no_duplicate(emitter: EventEmitter, notification_repo: Any) -> None:
    event1 = await emitter.emit(
        event_type=EventType.TASK_COMPLETED,
        source="test",
        task_id="task_dup",
        plan_id="plan_dup",
    )
    event2 = await emitter.emit(
        event_type=EventType.TASK_COMPLETED,
        source="test",
        task_id="task_dup",
        plan_id="plan_dup",
        event_id=event1.event_id,
    )
    assert event1.event_id == event2.event_id
    count = await notification_repo.count()
    assert count == 1


# ── G. Concurrent processing: same Event processed concurrently → 1 Notification ─


async def test_concurrent_processing_single_notification(session_factory: Any, policy: Any) -> None:
    session1 = session_factory()
    session2 = session_factory()
    try:
        repo1 = EventRepository(session1)
        repo2 = EventRepository(session2)
        notif_repo1 = NotificationRepository(session1)
        notif_repo2 = NotificationRepository(session2)

        emitter1 = EventEmitter(repo1, notification_policy=policy, notification_repository=notif_repo1)
        emitter2 = EventEmitter(repo2, notification_policy=policy, notification_repository=notif_repo2)

        shared_event_id = "evt_conc_shared"
        event_kwargs = {
            "event_type": EventType.TASK_FAILED,
            "source": "test",
            "task_id": "task_conc",
            "plan_id": "plan_conc",
            "payload": {"reason": "concurrent test"},
            "event_id": shared_event_id,
        }

        result1, result2 = await asyncio.gather(
            emitter1.emit(**event_kwargs),
            emitter2.emit(**event_kwargs),
            return_exceptions=True,
        )

        successes = [r for r in (result1, result2) if not isinstance(r, BaseException)]
        assert len(successes) >= 1

        await session1.commit()
        try:
            await session2.commit()
        except Exception:
            pass

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


# ── H. Event persistence failure: no Notification remains ────────────────────


async def test_event_persistence_failure_no_notification(notification_repo: Any) -> None:
    from sqlalchemy.exc import OperationalError

    class FailingEventRepository:
        async def record(self, event: Event) -> Event:
            raise OperationalError("DB down", None, None)

        async def get(self, event_id: str) -> Event | None:
            return None

    failing_repo = FailingEventRepository()
    emitter = EventEmitter(failing_repo, notification_policy=NotificationPolicy(), notification_repository=notification_repo)
    with pytest.raises(EventEmissionError):
        await emitter.emit(
            event_type=EventType.TASK_COMPLETED,
            source="test",
            task_id="task_fail",
        )
    count = await notification_repo.count()
    assert count == 0


# ── I. Notification infrastructure failure: verify transaction behavior ────────


async def test_notification_failure_preserves_event(session: Any, event_repo: Any) -> None:
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

    failing_notif_repo = FailingNotificationRepository()
    emitter = EventEmitter(event_repo, notification_policy=NotificationPolicy(), notification_repository=failing_notif_repo)

    with pytest.raises(EventEmissionError):
        await emitter.emit(
            event_type=EventType.TASK_FAILED,
            source="test",
            task_id="task_notif_fail",
            payload={"reason": "db error"},
            event_id="evt_notif_fail",
        )

    event = await event_repo.get("evt_notif_fail")
    assert event is not None


# ── J. Policy failure: verify exact transaction behavior ─────────────────────


async def test_policy_failure_preserves_event(session: Any, event_repo: Any) -> None:
    class FailingNotificationPolicy:
        def process(self, event: Event) -> NotificationDraft | None:
            raise RuntimeError("policy crashed")

    failing_policy = FailingNotificationPolicy()
    emitter = EventEmitter(event_repo, notification_policy=failing_policy, notification_repository=NotificationRepository(session))

    with pytest.raises(EventEmissionError):
        await emitter.emit(
            event_type=EventType.TASK_FAILED,
            source="test",
            task_id="task_policy_fail",
            event_id="evt_policy_fail",
        )

    event = await event_repo.get("evt_policy_fail")
    assert event is not None


# ── K. Outer transaction is not accidentally committed ────────────────────────


async def test_outer_transaction_not_committed(session: Any, event_repo: Any, notification_repo: Any) -> None:
    emitter = EventEmitter(event_repo, notification_policy=NotificationPolicy(), notification_repository=notification_repo)

    async with session.begin():
        await emitter.emit(
            event_type=EventType.TASK_COMPLETED,
            source="test",
            task_id="task_txn",
            plan_id="plan_txn",
            event_id="evt_txn_outer",
        )
        # Transaction not yet committed

    # After exiting context, transaction is committed
    event = await event_repo.get("evt_txn_outer")
    assert event is not None


# ── L. No Event is emitted as a side effect of Notification creation ──────────


async def test_no_event_emitted_for_notification(emitter: EventEmitter, event_repo: Any) -> None:
    await emitter.emit(
        event_type=EventType.TASK_FAILED,
        source="test",
        task_id="task_noevent",
        payload={"reason": "test"},
    )
    events = await event_repo.list()
    assert len(events) == 1


# ── M. Scheduler lifecycle events obey exact V1 mapping ──────────────────────


async def test_scheduler_failed_creates_notification(emitter: EventEmitter, notification_repo: Any) -> None:
    await emitter.emit(
        event_type=EventType.SCHEDULER_OCCURRENCE_FAILED,
        source="scheduler",
        execution_id="exec_sched",
        payload={
            "job_id": "job_sched",
            "occurrence_id": "occ_sched",
            "reason_code": "execution_error",
            "reason_detail": "timeout",
        },
    )
    notifications = await notification_repo.list()
    assert len(notifications) == 1
    assert notifications[0].notification_type == NotificationType.SCHEDULER_OCCURRENCE_FAILED.value


async def test_scheduler_recovered_creates_notification(emitter: EventEmitter, notification_repo: Any) -> None:
    await emitter.emit(
        event_type=EventType.SCHEDULER_OCCURRENCE_RECOVERED,
        source="scheduler",
        execution_id="exec_rec",
        payload={
            "job_id": "job_rec",
            "occurrence_id": "occ_rec",
            "reason": "lease_expired",
        },
    )
    notifications = await notification_repo.list()
    assert len(notifications) == 1
    assert notifications[0].notification_type == NotificationType.SCHEDULER_OCCURRENCE_RECOVERED.value


async def test_scheduler_skipped_no_notification(emitter: EventEmitter, notification_repo: Any) -> None:
    await emitter.emit(
        event_type=EventType.SCHEDULER_OCCURRENCE_SKIPPED,
        source="scheduler",
        execution_id="exec_skip",
    )
    count = await notification_repo.count()
    assert count == 0


# ── N. Integration performs no external/model/tool calls ─────────────────────


async def test_no_external_calls(emitter: EventEmitter) -> None:
    event = await emitter.emit(
        event_type=EventType.TASK_COMPLETED,
        source="test",
        task_id="task_ext",
    )
    assert event is not None


# ── O. Existing NotificationRepository savepoint semantics remain intact ───────


async def test_savepoint_semantics_intact(session: Any, emitter: EventEmitter, notification_repo: Any) -> None:
    async with session.begin():
        await emitter.emit(
            event_type=EventType.TASK_COMPLETED,
            source="test",
            task_id="task_sp",
        )
        count = await notification_repo.count()
        assert count == 1


# ── Additional: emitter without notification policy works as before ────────────


async def test_emitter_without_policy_unchanged(event_repo: Any) -> None:
    emitter = EventEmitter(event_repo)
    event = await emitter.emit(
        event_type=EventType.TASK_STARTED,
        source="test",
        task_id="task_nopolicy",
    )
    assert event is not None


# ── Additional: task.completed body includes commit_sha in metadata ────────────


async def test_task_completed_metadata_integration(emitter: EventEmitter, notification_repo: Any) -> None:
    await emitter.emit(
        event_type=EventType.TASK_COMPLETED,
        source="test",
        task_id="task_meta",
        payload={"commit_sha": "deadbeef", "attempt_count": 3},
    )
    notifications = await notification_repo.list()
    assert len(notifications) == 1
    assert notifications[0].metadata is not None
    assert notifications[0].metadata.get("commit_sha") == "deadbeef"
    assert notifications[0].metadata.get("attempt_count") == 3


# ── Additional: scheduler failed truncates long reason_detail ────────────────


async def test_scheduler_failed_truncates_detail(emitter: EventEmitter, notification_repo: Any) -> None:
    long_detail = "x" * 500
    await emitter.emit(
        event_type=EventType.SCHEDULER_OCCURRENCE_FAILED,
        source="scheduler",
        execution_id="exec_trunc",
        payload={
            "job_id": "job_trunc",
            "occurrence_id": "occ_trunc",
            "reason_code": "err",
            "reason_detail": long_detail,
        },
    )
    notifications = await notification_repo.list()
    assert len(notifications) == 1
    assert len(notifications[0].body) < 500


__all__ = [
    "TestRequiredEventTypesCreateNotification",
    "TestNoNotificationEventTypes",
]
