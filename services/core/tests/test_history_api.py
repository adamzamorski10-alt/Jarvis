"""
Stage 11.4 — History REST API Tests

Covers:
  1. task history
  2. plan history
  3. execution history
  4. correlation history
  5. time-range history
  6. recent history
  7. default pagination
  8. custom pagination
  9. limit > 500 rejected
  10. offset > 100000 rejected
  11. invalid parameters rejected
  12. empty result returns valid empty page
  13. repository failure is not converted to empty success
  14. deterministic response serialization
  15. payload remains inert/untrusted
  16. GET endpoints do not mutate state
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.history import router as history_router
from app.api.schemas.history import HistoryEntryResponse, HistoryPageResponse
from app.db.database import Base, get_db, init_db
from app.db.models.event import EventModel
from app.events.emitter import EventEmitter
from app.events.history import HistoryEntry, HistoryPage, HistoryService
from app.events.repository import EventRepository
from app.events.schemas import EventType, Sensitivity
from app.identity.auth import AuthMiddleware
from app.identity.bootstrap import BootstrapSecret
from app.identity.session import SessionStore
from app.repositories.task_repository import TaskRepository
from app.tasks.models import DevelopmentTask


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_event(**kwargs: Any):
    from app.events.schemas import Event
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


# ── Test Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
async def app_with_history(tmp_path):
    db_file = str(tmp_path / "test.db")
    factory, engine = await _init_db(db_file)

    async def _get_test_db():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(history_router, prefix="/api")
    app.dependency_overrides[get_db] = _get_test_db
    app.state._test_session_factory = factory

    session_store = SessionStore()
    bootstrap_secret = BootstrapSecret()
    app.state.session_store = session_store
    app.state.bootstrap_secret = bootstrap_secret
    app.add_middleware(AuthMiddleware)

    test_session = await session_store.create(user_id="test_user")
    app.state._test_session_id = test_session.session_id

    return app, factory, engine


@pytest.fixture
async def async_client(app_with_history):
    app, factory, engine = app_with_history
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {app.state._test_session_id}"
        yield client
    await engine.dispose()


# ── Task History API Tests ─────────────────────────────────────────────────────


class TestTaskHistoryAPI:
    async def test_get_task_history_returns_page(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            task_repo = TaskRepository(session)
            event_repo = EventRepository(session)
            task = DevelopmentTask(
                project_id="proj_1",
                title="Task 1",
                description="Test task",
                task_key="task_1",
            )
            created = await task_repo.create_task(task)
            await session.commit()

            emitter = EventEmitter(event_repo)
            await emitter.emit(EventType.TASK_STARTED, source="test", task_id=created.task_id)
            await session.commit()

        response = await async_client.get(f"/api/history/tasks/{created.task_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["entries"]) == 1
        assert data["entries"][0]["task_id"] == created.task_id

    async def test_get_task_history_empty(self, async_client: AsyncClient):
        response = await async_client.get("/api/history/tasks/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []
        assert data["total"] == 0

    async def test_get_task_history_default_pagination(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            task_repo = TaskRepository(session)
            event_repo = EventRepository(session)
            task = DevelopmentTask(
                project_id="proj_1",
                title="Task 1",
                description="Test task",
                task_key="task_1",
            )
            created = await task_repo.create_task(task)
            await session.commit()

            for i in range(60):
                await event_repo.record(_make_event(event_id=f"evt_{i:03d}", task_id=created.task_id, occurred_at=_utc_now() + timedelta(seconds=i)))
            await session.commit()

        response = await async_client.get(f"/api/history/tasks/{created.task_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 50
        assert len(data["entries"]) == 50
        assert data["has_more"] is True

    async def test_get_task_history_custom_pagination(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            task_repo = TaskRepository(session)
            event_repo = EventRepository(session)
            task = DevelopmentTask(
                project_id="proj_1",
                title="Task 1",
                description="Test task",
                task_key="task_1",
            )
            created = await task_repo.create_task(task)
            await session.commit()

            for i in range(10):
                await event_repo.record(_make_event(event_id=f"evt_{i:03d}", task_id=created.task_id, occurred_at=_utc_now() + timedelta(seconds=i)))
            await session.commit()

        response = await async_client.get(f"/api/history/tasks/{created.task_id}?limit=5&offset=3")
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 5
        assert data["offset"] == 3
        assert len(data["entries"]) == 5

    async def test_get_task_history_limit_over_max_rejected(self, async_client: AsyncClient):
        response = await async_client.get("/api/history/tasks/task_1?limit=501")
        assert response.status_code == 422

    async def test_get_task_history_offset_over_max_rejected(self, async_client: AsyncClient):
        response = await async_client.get("/api/history/tasks/task_1?offset=100001")
        assert response.status_code == 422

    async def test_get_task_history_deterministic_ordering(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            task_repo = TaskRepository(session)
            event_repo = EventRepository(session)
            task = DevelopmentTask(
                project_id="proj_1",
                title="Task 1",
                description="Test task",
                task_key="task_1",
            )
            created = await task_repo.create_task(task)
            await session.commit()

            base = _utc_now()
            await event_repo.record(_make_event(event_id="evt_b", task_id=created.task_id, occurred_at=base, recorded_at=base))
            await event_repo.record(_make_event(event_id="evt_a", task_id=created.task_id, occurred_at=base, recorded_at=base + timedelta(seconds=1)))
            await event_repo.record(_make_event(event_id="evt_c", task_id=created.task_id, occurred_at=base - timedelta(seconds=1), recorded_at=base))
            await session.commit()

        response = await async_client.get(f"/api/history/tasks/{created.task_id}")
        assert response.status_code == 200
        data = response.json()
        ids = [e["event_id"] for e in data["entries"]]
        assert ids == ["evt_c", "evt_a", "evt_b"]


# ── Plan History API Tests ─────────────────────────────────────────────────────


class TestPlanHistoryAPI:
    async def test_get_plan_history_returns_page(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            task_repo = TaskRepository(session)
            event_repo = EventRepository(session)
            task = DevelopmentTask(
                project_id="proj_1",
                title="Task 1",
                description="Test task",
                plan_id="plan_abc",
                task_key="task_1",
            )
            created = await task_repo.create_task(task)
            await session.commit()

            emitter = EventEmitter(event_repo)
            await emitter.emit(EventType.PLAN_STARTED, source="test", plan_id="plan_abc")
            await emitter.emit(EventType.TASK_STARTED, source="test", plan_id="plan_abc", task_id=created.task_id)
            await session.commit()

        response = await async_client.get("/api/history/plans/plan_abc")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert all(e["plan_id"] == "plan_abc" for e in data["entries"])

    async def test_get_plan_history_empty(self, async_client: AsyncClient):
        response = await async_client.get("/api/history/plans/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []


# ── Execution History API Tests ────────────────────────────────────────────────


class TestExecutionHistoryAPI:
    async def test_get_execution_history_returns_page(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            event_repo = EventRepository(session)
            await event_repo.record(_make_event(event_id="evt_1", execution_id="exec_abc", event_type=EventType.TASK_STARTED))
            await event_repo.record(_make_event(event_id="evt_2", execution_id="exec_abc", event_type=EventType.TASK_COMPLETED))
            await event_repo.record(_make_event(event_id="evt_3", execution_id="exec_xyz", event_type=EventType.TASK_STARTED))
            await session.commit()

        response = await async_client.get("/api/history/executions/exec_abc")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert all(e["execution_id"] == "exec_abc" for e in data["entries"])

    async def test_get_execution_history_empty(self, async_client: AsyncClient):
        response = await async_client.get("/api/history/executions/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []


# ── Correlation History API Tests ──────────────────────────────────────────────


class TestCorrelationHistoryAPI:
    async def test_get_correlation_history_returns_page(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            event_repo = EventRepository(session)
            await event_repo.record(_make_event(event_id="evt_1", correlation_id="corr_abc", event_type=EventType.TASK_STARTED))
            await event_repo.record(_make_event(event_id="evt_2", correlation_id="corr_abc", event_type=EventType.TASK_COMPLETED))
            await event_repo.record(_make_event(event_id="evt_3", correlation_id="corr_xyz", event_type=EventType.TASK_STARTED))
            await session.commit()

        response = await async_client.get("/api/history/correlations/corr_abc")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert all(e["correlation_id"] == "corr_abc" for e in data["entries"])

    async def test_get_correlation_history_empty(self, async_client: AsyncClient):
        response = await async_client.get("/api/history/correlations/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []


# ── Time Range History API Tests ───────────────────────────────────────────────


class TestTimeRangeHistoryAPI:
    async def test_get_history_between(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            event_repo = EventRepository(session)
            now = _utc_now()
            await event_repo.record(_make_event(event_id="evt_old", occurred_at=now - timedelta(hours=2)))
            await event_repo.record(_make_event(event_id="evt_mid", occurred_at=now - timedelta(minutes=30)))
            await event_repo.record(_make_event(event_id="evt_new", occurred_at=now))
            await session.commit()

        start = now - timedelta(hours=1)
        end = now + timedelta(hours=1)
        start_str = quote(start.isoformat())
        end_str = quote(end.isoformat())
        response = await async_client.get(f"/api/history/between?start={start_str}&end={end_str}")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        ids = [e["event_id"] for e in data["entries"]]
        assert "evt_mid" in ids
        assert "evt_new" in ids
        assert "evt_old" not in ids

    async def test_get_history_between_invalid_range(self, async_client: AsyncClient):
        now = _utc_now()
        start = now
        end = now - timedelta(hours=1)
        start_str = quote(start.isoformat())
        end_str = quote(end.isoformat())
        response = await async_client.get(f"/api/history/between?start={start_str}&end={end_str}")
        assert response.status_code == 422

    async def test_get_history_between_invalid_event_type(self, async_client: AsyncClient):
        now = _utc_now()
        start = now - timedelta(hours=1)
        end = now + timedelta(hours=1)
        start_str = quote(start.isoformat())
        end_str = quote(end.isoformat())
        response = await async_client.get(
            f"/api/history/between?start={start_str}&end={end_str}&event_type=invalid"
        )
        assert response.status_code == 422


# ── Recent History API Tests ───────────────────────────────────────────────────


class TestRecentHistoryAPI:
    async def test_get_recent_history(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            event_repo = EventRepository(session)
            base = _utc_now()
            await event_repo.record(_make_event(event_id="evt_oldest", recorded_at=base - timedelta(hours=3)))
            await event_repo.record(_make_event(event_id="evt_mid", recorded_at=base - timedelta(hours=1)))
            await event_repo.record(_make_event(event_id="evt_newest", recorded_at=base))
            await session.commit()

        response = await async_client.get("/api/history/recent?limit=10")
        assert response.status_code == 200
        data = response.json()
        ids = [e["event_id"] for e in data["entries"]]
        assert ids == ["evt_newest", "evt_mid", "evt_oldest"]

    async def test_get_recent_history_default_limit(self, async_client: AsyncClient):
        response = await async_client.get("/api/history/recent")
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 50

    async def test_get_recent_history_empty(self, async_client: AsyncClient):
        response = await async_client.get("/api/history/recent")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []
        assert data["total"] == 0


# ── Error Semantics Tests ──────────────────────────────────────────────────────


class TestHistoryAPIErrorSemantics:
    async def test_repository_failure_not_converted_to_empty(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            broken_repo = AsyncMock()
            broken_repo.list = AsyncMock(side_effect=sqlite3.DatabaseError("disk full"))
            svc = HistoryService(broken_repo, task_repository=None)

            async def _get_broken_svc():
                yield svc

            from app.dependencies import get_history_service
            app.dependency_overrides[get_history_service] = _get_broken_svc

        try:
            with pytest.raises(sqlite3.DatabaseError):
                await async_client.get("/api/history/tasks/task_1")
        finally:
            del app.dependency_overrides[get_history_service]

    async def test_get_endpoints_do_not_mutate(self, async_client: AsyncClient):
        response1 = await async_client.get("/api/history/tasks/task_1")
        response2 = await async_client.get("/api/history/tasks/task_1")
        assert response1.status_code == response2.status_code
        assert response1.json() == response2.json()

    async def test_payload_remains_inert(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        async with factory() as session:
            event_repo = EventRepository(session)
            from app.events.sanitizer import redact_payload
            payload = {"Authorization": "Bearer sk-1234567890abcdef"}
            sanitized = redact_payload(payload)
            await event_repo.record(_make_event(event_id="evt_sec_1", payload=sanitized))
            await session.commit()

        response = await async_client.get("/api/history/recent?limit=1")
        assert response.status_code == 200
        data = response.json()
        entry = data["entries"][0]
        assert "sk-1234567890abcdef" not in str(entry["payload"])
        assert "[REDACTED_BEARER]" in str(entry["payload"])
