"""
Stage 11.3 — HistoryService Tests

Covers:
  - Task history (filtering, ordering, pagination, entity existence)
  - Plan history (filtering, ordering, pagination, entity existence)
  - Execution history (filtering, empty result)
  - Correlation history (grouping, ordering)
  - Time range (start/end filtering, event_type filtering, invalid ranges)
  - Recent history (newest first, bounded retrieval)
  - Empty vs failure semantics
  - Security (redacted payloads remain inert)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.db.models.event import EventModel
from app.events.emitter import EventEmitter, EventIntegrityConflict
from app.events.history import HistoryEntry, HistoryPage, HistoryService
from app.events.repository import EventRepository
from app.events.schemas import Event, EventType, Sensitivity
from app.repositories.task_repository import TaskRepository
from app.tasks.models import DevelopmentTask, TaskStatus


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_session_factory(db_path: str):
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, connect_args={"check_same_thread": False})
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _init_db(db_path: str) -> tuple[async_sessionmaker, Any]:
    factory, engine = _make_session_factory(db_path)
    import app.db.database as db_mod
    old_engine = db_mod._engine
    db_mod._engine = engine
    try:
        await init_db()
    finally:
        db_mod._engine = old_engine
    return factory, engine


async def _make_repo(db_path: str) -> tuple[EventRepository, TaskRepository, async_sessionmaker, Any]:
    factory, engine = await _init_db(db_path)
    session = factory()
    event_repo = EventRepository(session)
    task_repo = TaskRepository(session)
    return event_repo, task_repo, factory, engine


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_event(**kwargs: Any) -> Event:
    defaults: dict[str, Any] = {
        "event_type": EventType.TASK_STARTED,
        "source": "test",
        "task_id": "task_abc123",
        "plan_id": "plan_xyz",
        "run_id": "run_001",
        "execution_id": "exec_001",
        "correlation_id": "corr_001",
        "causation_id": "evt_prev",
        "actor": "system",
        "payload": {"key": "value"},
        "metadata": {"count": 1},
        "sensitivity": Sensitivity.INTERNAL,
        "occurred_at": _utc_now(),
        "recorded_at": _utc_now(),
        "event_version": "1.0",
    }
    defaults.update(kwargs)
    return Event(**defaults)


# ── Task History Tests ─────────────────────────────────────────────────────────


class TestTaskHistory:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        event_repo, task_repo, factory, engine = await _make_repo(db_file)
        try:
            yield event_repo, task_repo
        finally:
            await engine.dispose()

    async def test_correct_events_for_task(self, repo):
        event_repo, task_repo = repo
        task = DevelopmentTask(
            project_id="proj_1",
            title="Task 1",
            description="Test task",
        )
        created = await task_repo.create_task(task)
        await task_repo._session.commit()

        await event_repo.record(_make_event(event_id="evt_1", task_id=created.task_id, event_type=EventType.TASK_STARTED))
        await event_repo.record(_make_event(event_id="evt_2", task_id=created.task_id, event_type=EventType.TASK_COMPLETED))
        await event_repo.record(_make_event(event_id="evt_3", task_id="other_task", event_type=EventType.TASK_STARTED))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_task_history(created.task_id)
        assert len(page.entries) == 2
        assert all(e.task_id == created.task_id for e in page.entries)
        assert page.total == 2

    async def test_deterministic_ordering(self, repo):
        event_repo, task_repo = repo
        task = DevelopmentTask(
            project_id="proj_1",
            title="Task 1",
            description="Test task",
        )
        created = await task_repo.create_task(task)
        await task_repo._session.commit()

        base = _utc_now()
        await event_repo.record(_make_event(event_id="evt_b", task_id=created.task_id, occurred_at=base, recorded_at=base))
        await event_repo.record(_make_event(event_id="evt_a", task_id=created.task_id, occurred_at=base, recorded_at=base + timedelta(seconds=1)))
        await event_repo.record(_make_event(event_id="evt_c", task_id=created.task_id, occurred_at=base - timedelta(seconds=1), recorded_at=base))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_task_history(created.task_id)
        ids = [e.event_id for e in page.entries]
        assert ids == ["evt_c", "evt_a", "evt_b"]

    async def test_pagination(self, repo):
        event_repo, task_repo = repo
        task = DevelopmentTask(
            project_id="proj_1",
            title="Task 1",
            description="Test task",
        )
        created = await task_repo.create_task(task)
        await task_repo._session.commit()

        for i in range(10):
            await event_repo.record(_make_event(event_id=f"evt_{i:03d}", task_id=created.task_id, occurred_at=_utc_now() + timedelta(seconds=i)))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page1 = await svc.get_task_history(created.task_id, limit=3, offset=0)
        assert len(page1.entries) == 3
        assert page1.has_more is True
        assert page1.total == 10

        page2 = await svc.get_task_history(created.task_id, limit=3, offset=3)
        assert len(page2.entries) == 3
        assert page2.has_more is True

        page4 = await svc.get_task_history(created.task_id, limit=3, offset=9)
        assert len(page4.entries) == 1
        assert page4.has_more is False

    async def test_task_not_found_returns_empty(self, repo):
        event_repo, task_repo = repo
        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_task_history("nonexistent_task")
        assert page.entries == ()
        assert page.total == 0
        assert page.has_more is False

    async def test_no_task_repo_returns_events_anyway(self, repo):
        event_repo, task_repo = repo
        svc = HistoryService(event_repo, task_repository=None)
        await event_repo.record(_make_event(event_id="evt_1", task_id="task_xyz"))
        await event_repo.session.commit()

        page = await svc.get_task_history("task_xyz")
        assert len(page.entries) == 1


# ── Plan History Tests ─────────────────────────────────────────────────────────


class TestPlanHistory:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        event_repo, task_repo, factory, engine = await _make_repo(db_file)
        try:
            yield event_repo, task_repo
        finally:
            await engine.dispose()

    async def test_correct_events_for_plan(self, repo):
        event_repo, task_repo = repo
        task1 = DevelopmentTask(
            project_id="proj_1",
            title="Task 1",
            description="Test task",
            plan_id="plan_abc",
            task_key="task_1",
        )
        task2 = DevelopmentTask(
            project_id="proj_1",
            title="Task 2",
            description="Test task 2",
            plan_id="plan_abc",
            task_key="task_2",
        )
        created1 = await task_repo.create_task(task1)
        created2 = await task_repo.create_task(task2)
        await task_repo._session.commit()

        await event_repo.record(_make_event(event_id="evt_1", plan_id="plan_abc", event_type=EventType.PLAN_STARTED))
        await event_repo.record(_make_event(event_id="evt_2", plan_id="plan_abc", task_id=created1.task_id, event_type=EventType.TASK_STARTED))
        await event_repo.record(_make_event(event_id="evt_3", plan_id="plan_abc", task_id=created2.task_id, event_type=EventType.TASK_COMPLETED))
        await event_repo.record(_make_event(event_id="evt_4", plan_id="plan_xyz", event_type=EventType.PLAN_STARTED))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_plan_history("plan_abc")
        assert len(page.entries) == 3
        assert page.total == 3
        assert all(e.plan_id == "plan_abc" for e in page.entries)

    async def test_plan_not_found_returns_empty(self, repo):
        event_repo, task_repo = repo
        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_plan_history("nonexistent_plan")
        assert page.entries == ()
        assert page.total == 0

    async def test_plan_with_events_but_zero_tasks_returns_events(self, repo):
        event_repo, task_repo = repo
        await event_repo.record(_make_event(event_id="evt_plan_only", plan_id="plan_no_tasks", event_type=EventType.PLAN_STARTED))
        await event_repo.record(_make_event(event_id="evt_plan_only_2", plan_id="plan_no_tasks", event_type=EventType.PLAN_COMPLETED))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_plan_history("plan_no_tasks")
        assert len(page.entries) == 2
        assert page.total == 2
        assert all(e.plan_id == "plan_no_tasks" for e in page.entries)

    async def test_ordering(self, repo):
        event_repo, task_repo = repo
        task = DevelopmentTask(
            project_id="proj_1",
            title="Task 1",
            description="Test task",
            plan_id="plan_abc",
        )
        created = await task_repo.create_task(task)
        await task_repo._session.commit()

        base = _utc_now()
        await event_repo.record(_make_event(event_id="evt_b", plan_id="plan_abc", task_id=created.task_id, occurred_at=base, recorded_at=base))
        await event_repo.record(_make_event(event_id="evt_a", plan_id="plan_abc", task_id=created.task_id, occurred_at=base + timedelta(seconds=1), recorded_at=base))
        await event_repo.record(_make_event(event_id="evt_c", plan_id="plan_abc", task_id=created.task_id, occurred_at=base - timedelta(seconds=1), recorded_at=base))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_plan_history("plan_abc")
        ids = [e.event_id for e in page.entries]
        assert ids == ["evt_c", "evt_b", "evt_a"]


# ── Execution History Tests ────────────────────────────────────────────────────


class TestExecutionHistory:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        event_repo, task_repo, factory, engine = await _make_repo(db_file)
        try:
            yield event_repo, task_repo
        finally:
            await engine.dispose()

    async def test_correct_filtering(self, repo):
        event_repo, task_repo = repo
        await event_repo.record(_make_event(event_id="evt_1", execution_id="exec_abc", event_type=EventType.TASK_STARTED))
        await event_repo.record(_make_event(event_id="evt_2", execution_id="exec_abc", event_type=EventType.TASK_COMPLETED))
        await event_repo.record(_make_event(event_id="evt_3", execution_id="exec_xyz", event_type=EventType.TASK_STARTED))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_execution_history("exec_abc")
        assert len(page.entries) == 2
        assert all(e.execution_id == "exec_abc" for e in page.entries)
        assert page.total == 2

    async def test_empty_result(self, repo):
        event_repo, task_repo = repo
        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_execution_history("nonexistent_exec")
        assert page.entries == ()
        assert page.total == 0


# ── Correlation History Tests ──────────────────────────────────────────────────


class TestCorrelationHistory:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        event_repo, task_repo, factory, engine = await _make_repo(db_file)
        try:
            yield event_repo, task_repo
        finally:
            await engine.dispose()

    async def test_correct_grouping(self, repo):
        event_repo, task_repo = repo
        await event_repo.record(_make_event(event_id="evt_1", correlation_id="corr_abc", event_type=EventType.TASK_STARTED))
        await event_repo.record(_make_event(event_id="evt_2", correlation_id="corr_abc", event_type=EventType.TASK_COMPLETED))
        await event_repo.record(_make_event(event_id="evt_3", correlation_id="corr_xyz", event_type=EventType.TASK_STARTED))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_correlation_history("corr_abc")
        assert len(page.entries) == 2
        assert all(e.correlation_id == "corr_abc" for e in page.entries)
        assert page.total == 2

    async def test_ordering(self, repo):
        event_repo, task_repo = repo
        base = _utc_now()
        await event_repo.record(_make_event(event_id="evt_b", correlation_id="corr_abc", occurred_at=base, recorded_at=base))
        await event_repo.record(_make_event(event_id="evt_a", correlation_id="corr_abc", occurred_at=base + timedelta(seconds=1), recorded_at=base))
        await event_repo.record(_make_event(event_id="evt_c", correlation_id="corr_abc", occurred_at=base - timedelta(seconds=1), recorded_at=base))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_correlation_history("corr_abc")
        ids = [e.event_id for e in page.entries]
        assert ids == ["evt_c", "evt_b", "evt_a"]


# ── Time Range Tests ───────────────────────────────────────────────────────────


class TestTimeRange:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        event_repo, task_repo, factory, engine = await _make_repo(db_file)
        try:
            yield event_repo, task_repo
        finally:
            await engine.dispose()

    async def test_start_end_filtering(self, repo):
        event_repo, task_repo = repo
        now = _utc_now()
        await event_repo.record(_make_event(event_id="evt_old", occurred_at=now - timedelta(hours=2)))
        await event_repo.record(_make_event(event_id="evt_mid", occurred_at=now - timedelta(minutes=30)))
        await event_repo.record(_make_event(event_id="evt_new", occurred_at=now))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_history_between(now - timedelta(hours=1), now)
        assert len(page.entries) == 2
        ids = [e.event_id for e in page.entries]
        assert "evt_mid" in ids
        assert "evt_new" in ids
        assert "evt_old" not in ids

    async def test_event_type_filtering(self, repo):
        event_repo, task_repo = repo
        await event_repo.record(_make_event(event_id="evt_1", event_type=EventType.TASK_STARTED))
        await event_repo.record(_make_event(event_id="evt_2", event_type=EventType.TASK_COMPLETED))
        await event_repo.record(_make_event(event_id="evt_3", event_type=EventType.TASK_STARTED))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_history_between(
            _utc_now() - timedelta(days=1),
            _utc_now() + timedelta(days=1),
            event_type=EventType.TASK_STARTED,
        )
        assert len(page.entries) == 2
        assert all(e.event_type == EventType.TASK_STARTED for e in page.entries)

    async def test_invalid_range_raises(self, repo):
        event_repo, task_repo = repo
        svc = HistoryService(event_repo, task_repo)
        start = _utc_now()
        end = start - timedelta(hours=1)
        with pytest.raises(ValueError, match="must not be after"):
            await svc.get_history_between(start, end)

    async def test_naive_datetime_raises(self, repo):
        event_repo, task_repo = repo
        svc = HistoryService(event_repo, task_repo)
        naive_start = datetime.now()
        naive_end = datetime.now()
        with pytest.raises(ValueError, match="must be timezone-aware"):
            await svc.get_history_between(naive_start, naive_end)

    async def test_boundaries(self, repo):
        event_repo, task_repo = repo
        now = _utc_now()
        await event_repo.record(_make_event(event_id="evt_at_start", occurred_at=now - timedelta(hours=2)))
        await event_repo.record(_make_event(event_id="evt_at_end", occurred_at=now + timedelta(hours=2)))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_history_between(now - timedelta(hours=2), now + timedelta(hours=2))
        assert len(page.entries) == 2


# ── Recent History Tests ───────────────────────────────────────────────────────


class TestRecentHistory:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        event_repo, task_repo, factory, engine = await _make_repo(db_file)
        try:
            yield event_repo, task_repo
        finally:
            await engine.dispose()

    async def test_newest_records_first(self, repo):
        event_repo, task_repo = repo
        base = _utc_now()
        await event_repo.record(_make_event(event_id="evt_oldest", recorded_at=base - timedelta(hours=3)))
        await event_repo.record(_make_event(event_id="evt_mid", recorded_at=base - timedelta(hours=1)))
        await event_repo.record(_make_event(event_id="evt_newest", recorded_at=base))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_recent_history(limit=10)
        ids = [e.event_id for e in page.entries]
        assert ids == ["evt_newest", "evt_mid", "evt_oldest"]

    async def test_bounded_retrieval(self, repo):
        event_repo, task_repo = repo
        for i in range(10):
            await event_repo.record(_make_event(event_id=f"evt_{i:03d}", recorded_at=_utc_now() + timedelta(seconds=i)))
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_recent_history(limit=3)
        assert len(page.entries) == 3
        assert page.limit == 3

    async def test_empty_when_no_events(self, repo):
        event_repo, task_repo = repo
        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_recent_history()
        assert page.entries == ()
        assert page.total == 0


# ── Empty vs Failure Tests ─────────────────────────────────────────────────────


class TestEmptyVsFailure:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        event_repo, task_repo, factory, engine = await _make_repo(db_file)
        try:
            yield event_repo, task_repo
        finally:
            await engine.dispose()

    async def test_no_events_returns_empty_page(self, repo):
        event_repo, task_repo = repo
        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_task_history("task_no_events", limit=10, offset=0)
        assert page.entries == ()
        assert page.total == 0
        assert page.has_more is False

    async def test_database_failure_propagates(self, repo):
        event_repo, task_repo = repo
        broken_repo = MagicMock()
        broken_repo.list = AsyncMock(side_effect=sqlite3.DatabaseError("disk full"))
        svc = HistoryService(broken_repo, task_repository=None)
        with pytest.raises(sqlite3.DatabaseError):
            await svc.get_task_history("task_1")

    async def test_event_deserialization_failure_logs_and_returns_none(self, repo):
        event_repo, task_repo = repo
        task = DevelopmentTask(
            project_id="proj_1",
            title="Task 1",
            description="Test task",
        )
        created = await task_repo.create_task(task)
        await task_repo._session.commit()

        await event_repo.record(_make_event(event_id="evt_bad", task_id=created.task_id))
        await event_repo.session.commit()

        async with event_repo.session.begin():
            await event_repo.session.execute(
                EventModel.__table__.update().where(EventModel.event_id == "evt_bad").values(payload="not-json{")
            )

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_task_history(created.task_id)
        assert len(page.entries) == 1
        assert page.entries[0].payload is None


# ── Security Tests ─────────────────────────────────────────────────────────────


class TestSecurityHistory:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        event_repo, task_repo, factory, engine = await _make_repo(db_file)
        try:
            yield event_repo, task_repo
        finally:
            await engine.dispose()

    async def test_redacted_payload_remains_inert(self, repo):
        event_repo, task_repo = repo
        from app.events.sanitizer import redact_payload
        payload = {"Authorization": "Bearer sk-1234567890abcdef"}
        sanitized = redact_payload(payload)
        evt = _make_event(event_id="evt_sec_1", payload=sanitized)
        await event_repo.record(evt)
        await event_repo.session.commit()

        svc = HistoryService(event_repo, task_repo)
        page = await svc.get_recent_history()
        entry = page.entries[0]
        assert "sk-1234567890abcdef" not in str(entry.payload)
        assert "[REDACTED_BEARER]" in str(entry.payload)

    async def test_no_database_bypass(self, repo):
        event_repo, task_repo = repo
        svc = HistoryService(event_repo, task_repo)
        assert isinstance(svc._event_repo, EventRepository)
        assert not hasattr(svc, "_session")


__all__ = [
    "TestTaskHistory",
    "TestPlanHistory",
    "TestExecutionHistory",
    "TestCorrelationHistory",
    "TestTimeRange",
    "TestRecentHistory",
    "TestEmptyVsFailure",
    "TestSecurityHistory",
]
