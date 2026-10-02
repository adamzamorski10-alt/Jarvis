"""
Stage 12.3B — Scheduler REST API Tests

Covers all 8 scheduler endpoints:
  POST   /api/scheduler/jobs
  GET    /api/scheduler/jobs
  GET    /api/scheduler/jobs/{job_id}
  PATCH  /api/scheduler/jobs/{job_id}
  DELETE /api/scheduler/jobs/{job_id}
  POST   /api/scheduler/jobs/{job_id}/enable
  POST   /api/scheduler/jobs/{job_id}/disable
  GET    /api/scheduler/jobs/{job_id}/history
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.history import router as history_router
from app.api.routes.scheduler import router as scheduler_router
from app.api.schemas.history import HistoryPageResponse
from app.db.database import Base, get_db, init_db
from app.db.models.development_task import DevelopmentTaskModel
from app.db.models.scheduled_job import ScheduledJobModel
from app.identity.auth import AuthMiddleware
from app.identity.bootstrap import BootstrapSecret
from app.identity.session import SessionStore
from app.repositories.task_repository import TaskRepository
from app.scheduler.models import ScheduledJob, ScheduleType, SchedulerJobStatus
from app.scheduler.repository import SchedulerRepository


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_session_factory(db_path: str):
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, connect_args={"check_same_thread": False})
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _init_db(db_path: str):
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


async def _create_task(session: AsyncSession, task_id: str = "task_abc123", task_key: str = "t1") -> None:
    task = DevelopmentTaskModel(
        task_id=task_id,
        plan_id="plan_1",
        task_key=task_key,
        title="Test Task",
        description="Test task",
        project_id="proj_1",
        status="PENDING",
        attempt_count=0,
        max_attempts=3,
        created_at=_utc_now(),
    )
    session.add(task)
    await session.commit()


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def app_with_scheduler(tmp_path):
    db_file = str(tmp_path / "test.db")
    factory, engine = await _init_db(db_file)

    async def _get_test_db():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    test_app = FastAPI()
    test_app.include_router(scheduler_router, prefix="/api", tags=["Scheduler"])
    test_app.include_router(history_router, prefix="/api", tags=["History"])
    test_app.dependency_overrides[get_db] = _get_test_db
    test_app.state._test_session_factory = factory

    session_store = SessionStore()
    bootstrap_secret = BootstrapSecret()
    test_app.state.session_store = session_store
    test_app.state.bootstrap_secret = bootstrap_secret
    test_app.add_middleware(AuthMiddleware)

    test_session = await session_store.create(user_id="test_user")
    test_app.state._test_session_id = test_session.session_id

    @test_app.exception_handler(Exception)
    async def _global_exception_handler(request, exc):
        from starlette.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return test_app, factory, engine


@pytest.fixture
async def async_client(app_with_scheduler):
    app, factory, engine = app_with_scheduler
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {app.state._test_session_id}"
        yield client
    await engine.dispose()


@pytest.fixture
async def session_factory(app_with_scheduler):
    _, factory, _ = app_with_scheduler
    return factory


@pytest.fixture(autouse=True)
async def seed_task(session_factory):
    async with session_factory() as session:
        await _create_task(session, task_id="task_abc123")
        await session.commit()


# ── CREATE ─────────────────────────────────────────────────────────────────────


class TestCreateJob:
    async def test_create_valid_job_returns_201(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_1",
                "name": "Test Job",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["job_id"] == "job_1"
        assert data["status"] == "PENDING"

    async def test_create_once_future_schedule_succeeds(self, async_client: AsyncClient):
        future = (_utc_now() + timedelta(hours=2)).isoformat()
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_once_future",
                "name": "ONCE Future",
                "schedule_type": "once",
                "schedule_expr": future,
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 201

    async def test_create_once_past_returns_422(self, async_client: AsyncClient):
        past = (_utc_now() - timedelta(hours=2)).isoformat()
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_once_past",
                "name": "ONCE Past",
                "schedule_type": "once",
                "schedule_expr": past,
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 422

    async def test_create_interval_schedule_succeeds(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_interval",
                "name": "Interval Job",
                "schedule_type": "interval",
                "schedule_expr": "3600",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["schedule_type"] == "interval"

    async def test_create_cron_schedule_succeeds(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_cron",
                "name": "CRON Job",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["schedule_type"] == "cron"

    async def test_create_invalid_schedule_type_returns_422(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_invalid_sched",
                "name": "Invalid Schedule",
                "schedule_type": "yearly",
                "schedule_expr": "* * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 422

    async def test_create_invalid_timezone_returns_422(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_invalid_tz",
                "name": "Invalid TZ",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": "Not/ATimezone",
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 422

    async def test_create_task_target_exists_succeeds(self, async_client: AsyncClient, session_factory):
        async with session_factory() as session:
            await _create_task(session, task_id="task_existing", task_key="t2")
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_existing_task",
                "name": "Existing Task",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_existing",
                "enabled": True,
            },
        )
        assert response.status_code == 201

    async def test_create_missing_task_target_returns_404(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_missing_task",
                "name": "Missing Task",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "nonexistent_task",
                "enabled": True,
            },
        )
        assert response.status_code == 404

    async def test_create_plan_target_accepted_without_validation(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_plan",
                "name": "Plan Job",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "plan",
                "target_id": "plan_no_tasks",
                "enabled": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["target_type"] == "plan"
        assert data["target_id"] == "plan_no_tasks"

    async def test_create_idempotent_same_definition_returns_200(self, async_client: AsyncClient):
        payload = {
            "job_id": "job_idem",
            "name": "Idempotent",
            "schedule_type": "cron",
            "schedule_expr": "*/5 * * * *",
            "timezone": None,
            "target_type": "task",
            "target_id": "task_abc123",
            "enabled": True,
        }
        response1 = await async_client.post("/api/scheduler/jobs", json=payload)
        assert response1.status_code == 201
        response2 = await async_client.post("/api/scheduler/jobs", json=payload)
        assert response2.status_code == 200
        assert response2.json()["job_id"] == "job_idem"

    async def test_create_same_job_id_changed_name_returns_409(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_conflict_name",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_conflict_name",
                "name": "Modified",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 409

    async def test_create_same_job_id_changed_schedule_returns_409(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_conflict_sched",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_conflict_sched",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "0 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 409

    async def test_create_same_job_id_changed_timezone_returns_409(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_conflict_tz",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_conflict_tz",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": "UTC",
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 409

    async def test_create_same_job_id_changed_target_returns_409(self, async_client: AsyncClient, session_factory):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_conflict_target",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        async with session_factory() as session:
            await _create_task(session, task_id="other_task", task_key="t4")
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_conflict_target",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "other_task",
                "enabled": True,
            },
        )
        assert response.status_code == 409

    async def test_create_never_overwrites_existing(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_no_overwrite",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_no_overwrite",
                "name": "Should Not Overwrite",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 409
        get_response = await async_client.get("/api/scheduler/jobs/job_no_overwrite")
        assert get_response.status_code == 200
        assert get_response.json()["name"] == "Original"


# ── GET ────────────────────────────────────────────────────────────────────────


class TestGetJobs:
    async def test_list_empty_returns_200(self, async_client: AsyncClient):
        response = await async_client.get("/api/scheduler/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["jobs"] == []
        assert data["total"] == 0

    async def test_list_populated_returns_jobs(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_list_1",
                "name": "Job 1",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_list_2",
                "name": "Job 2",
                "schedule_type": "interval",
                "schedule_expr": "3600",
                "timezone": None,
                "target_type": "plan",
                "target_id": "plan_1",
                "enabled": False,
            },
        )
        response = await async_client.get("/api/scheduler/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["jobs"]) == 2

    async def test_list_deterministic_ordering(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_det_1",
                "name": "Job 1",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_det_2",
                "name": "Job 2",
                "schedule_type": "cron",
                "schedule_expr": "0 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.get("/api/scheduler/jobs")
        assert response.status_code == 200
        data = response.json()
        ids = [j["job_id"] for j in data["jobs"]]
        assert ids == sorted(ids)

    async def test_get_existing_returns_200(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_get_existing",
                "name": "Get Me",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.get("/api/scheduler/jobs/job_get_existing")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "job_get_existing"
        assert data["name"] == "Get Me"

    async def test_get_missing_returns_404(self, async_client: AsyncClient):
        response = await async_client.get("/api/scheduler/jobs/nonexistent")
        assert response.status_code == 404

    async def test_get_sanitized_response_no_sensitive_fields(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_sensitive",
            name="Sensitive",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.CLAIMED,
            last_run_at=None,
            next_run_at=now,
            created_at=now,
            updated_at=now,
            occurrence_id="occurrence_secret_123",
            claim_token="claim_token_secret_456",
            claim_sequence=1,
            claimed_at=now,
            lease_until=now + timedelta(minutes=5),
            execution_id="exec_secret_789",
            metadata={"key": "value"},
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.get("/api/scheduler/jobs/job_sensitive")
        assert response.status_code == 200
        data = response.json()
        assert "occurrence_id" not in data
        assert "claim_token" not in data
        assert "lease_until" not in data
        assert "execution_id" not in data
        assert "metadata" not in data
        assert "claimed_at" not in data
        assert data["claim_sequence"] == 1


# ── PATCH ──────────────────────────────────────────────────────────────────────


class TestPatchJob:
    async def test_patch_partial_update(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_partial",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.patch(
            "/api/scheduler/jobs/job_partial",
            json={"name": "Updated Name"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    async def test_patch_pending_name(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_pend_name",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.patch(
            "/api/scheduler/jobs/job_pend_name",
            json={"name": "New Name"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    async def test_patch_pending_target(self, async_client: AsyncClient, session_factory):
        async with session_factory() as session:
            await _create_task(session, task_id="new_target", task_key="t3")
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_pend_target",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.patch(
            "/api/scheduler/jobs/job_pend_target",
            json={"target_id": "new_target"},
        )
        assert response.status_code == 200
        assert response.json()["target_id"] == "new_target"

    async def test_patch_pending_schedule(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_pend_sched",
                "name": "Original",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.patch(
            "/api/scheduler/jobs/job_pend_sched",
            json={"schedule_expr": "0 * * * *"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["schedule_expr"] == "0 * * * *"
        assert data["next_run_at"] is not None

    async def test_patch_claimed_enabled_only(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_claimed_patch",
            name="Claimed",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.CLAIMED,
            last_run_at=None,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.patch(
            "/api/scheduler/jobs/job_claimed_patch",
            json={"enabled": False},
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    async def test_patch_claimed_schedule_returns_409(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_claimed_sched",
            name="Claimed",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.CLAIMED,
            last_run_at=None,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.patch(
            "/api/scheduler/jobs/job_claimed_sched",
            json={"schedule_expr": "0 * * * *"},
        )
        assert response.status_code == 409

    async def test_patch_claimed_target_returns_409(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_claimed_target",
            name="Claimed",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.CLAIMED,
            last_run_at=None,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.patch(
            "/api/scheduler/jobs/job_claimed_target",
            json={"target_id": "other_task"},
        )
        assert response.status_code == 409

    async def test_patch_claimed_name_returns_409(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_claimed_name",
            name="Claimed",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.CLAIMED,
            last_run_at=None,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.patch(
            "/api/scheduler/jobs/job_claimed_name",
            json={"name": "New Name"},
        )
        assert response.status_code == 409

    async def test_patch_triggered_allows_schedule(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_triggered_patch",
            name="Triggered",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.TRIGGERED,
            last_run_at=now,
            next_run_at=now + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.patch(
            "/api/scheduler/jobs/job_triggered_patch",
            json={"schedule_expr": "0 * * * *"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["schedule_expr"] == "0 * * * *"
        assert data["next_run_at"] is not None

    async def test_patch_failed_update_allowed(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_failed_patch",
            name="Failed",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.FAILED,
            last_run_at=now,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.patch(
            "/api/scheduler/jobs/job_failed_patch",
            json={"name": "Retry"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Retry"

    async def test_patch_skipped_update_allowed(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_skipped_patch",
            name="Skipped",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.SKIPPED,
            last_run_at=now,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.patch(
            "/api/scheduler/jobs/job_skipped_patch",
            json={"name": "Rescheduled"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Rescheduled"

    async def test_patch_disabled_update_allowed(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_disabled_patch",
            name="Disabled",
            enabled=False,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.DISABLED,
            last_run_at=now,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.patch(
            "/api/scheduler/jobs/job_disabled_patch",
            json={"enabled": True},
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is True

    async def test_patch_schedule_recalculation(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_recalc",
                "name": "Recalc",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.patch(
            "/api/scheduler/jobs/job_recalc",
            json={"schedule_expr": "0 * * * *"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["schedule_expr"] == "0 * * * *"
        assert data["next_run_at"] is not None

    async def test_patch_last_run_at_preserved(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        original_last = now - timedelta(hours=1)
        job = ScheduledJob(
            job_id="job_last_run",
            name="LastRun",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.PENDING,
            last_run_at=original_last,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.patch(
            "/api/scheduler/jobs/job_last_run",
            json={"schedule_expr": "0 * * * *"},
        )
        assert response.status_code == 200
        data = response.json()
        from datetime import datetime as dt
        returned_last = dt.fromisoformat(data["last_run_at"].replace("Z", "+00:00"))
        assert returned_last == original_last

    async def test_patch_immutable_occurrence_fields_rejected(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_immutable_patch",
                "name": "Immutable",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.patch(
            "/api/scheduler/jobs/job_immutable_patch",
            json={"occurrence_id": "new_occurrence"},
        )
        assert response.status_code == 422

    async def test_patch_no_arbitrary_fields_accepted(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_no_arbitrary",
                "name": "No Arbitrary",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.patch(
            "/api/scheduler/jobs/job_no_arbitrary",
            json={"arbitrary_field": "value"},
        )
        assert response.status_code == 422


# ── ENABLE / DISABLE ───────────────────────────────────────────────────────────


class TestEnableDisable:
    async def test_enable_job(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_enable_test",
            name="EnableTest",
            enabled=False,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.PENDING,
            last_run_at=None,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.post("/api/scheduler/jobs/job_enable_test/enable")
        assert response.status_code == 200
        assert response.json()["enabled"] is True

    async def test_disable_job(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_disable_test",
            name="DisableTest",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.PENDING,
            last_run_at=None,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.post("/api/scheduler/jobs/job_disable_test/disable")
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    async def test_enable_does_not_recalculate_schedule(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        original_next = now + timedelta(minutes=10)
        job = ScheduledJob(
            job_id="job_enable_no_recalc",
            name="NoRecalc",
            enabled=False,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.PENDING,
            last_run_at=None,
            next_run_at=original_next,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.post("/api/scheduler/jobs/job_enable_no_recalc/enable")
        assert response.status_code == 200
        data = response.json()
        from datetime import datetime as dt
        returned_next = dt.fromisoformat(data["next_run_at"].replace("Z", "+00:00"))
        assert returned_next == original_next

    async def test_disable_does_not_reset_status(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_disable_no_reset",
            name="NoReset",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.PENDING,
            last_run_at=None,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.post("/api/scheduler/jobs/job_disable_no_reset/disable")
        assert response.status_code == 200
        assert response.json()["status"] == "PENDING"

    async def test_enable_missing_job_returns_404(self, async_client: AsyncClient):
        response = await async_client.post("/api/scheduler/jobs/nonexistent/enable")
        assert response.status_code == 404

    async def test_disable_does_not_cancel_active_execution(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_disable_triggered",
            name="DisableTriggered",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.TRIGGERED,
            last_run_at=now,
            next_run_at=now + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.post("/api/scheduler/jobs/job_disable_triggered/disable")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["status"] == "TRIGGERED"


# ── DELETE ─────────────────────────────────────────────────────────────────────


class TestDeleteJob:
    async def test_delete_pending_allowed(self, async_client: AsyncClient):
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_del_pending",
                "name": "DeletePending",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        response = await async_client.delete("/api/scheduler/jobs/job_del_pending")
        assert response.status_code == 204

    async def test_delete_failed_allowed(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_del_failed",
            name="DeleteFailed",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.FAILED,
            last_run_at=now,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.delete("/api/scheduler/jobs/job_del_failed")
        assert response.status_code == 204

    async def test_delete_skipped_allowed(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_del_skipped",
            name="DeleteSkipped",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.SKIPPED,
            last_run_at=now,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.delete("/api/scheduler/jobs/job_del_skipped")
        assert response.status_code == 204

    async def test_delete_disabled_allowed(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_del_disabled",
            name="DeleteDisabled",
            enabled=False,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.DISABLED,
            last_run_at=now,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.delete("/api/scheduler/jobs/job_del_disabled")
        assert response.status_code == 204

    async def test_delete_claimed_returns_409(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_del_claimed",
            name="DeleteClaimed",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.CLAIMED,
            last_run_at=None,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.delete("/api/scheduler/jobs/job_del_claimed")
        assert response.status_code == 409

    async def test_delete_triggered_returns_409(self, async_client: AsyncClient, session_factory):
        now = _utc_now()
        job = ScheduledJob(
            job_id="job_del_triggered",
            name="DeleteTriggered",
            enabled=True,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            status=SchedulerJobStatus.TRIGGERED,
            last_run_at=now,
            next_run_at=now + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )
        async with session_factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            await repo.create_job(job)
            await session.commit()

        response = await async_client.delete("/api/scheduler/jobs/job_del_triggered")
        assert response.status_code == 409

    async def test_delete_missing_returns_404(self, async_client: AsyncClient):
        response = await async_client.delete("/api/scheduler/jobs/nonexistent")
        assert response.status_code == 404


# ── HISTORY ────────────────────────────────────────────────────────────────────


class TestJobHistory:
    async def test_history_populated(self, async_client: AsyncClient, session_factory):
        job_id = "job_hist_populated"
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": job_id,
                "name": "History Job",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        async with session_factory() as session:
            event_repo = session.__class__.mro()
            from app.events.repository import EventRepository
            event_repo = EventRepository(session)
            from app.events.schemas import Event, EventType, Sensitivity
            event = Event(
                event_id="evt_hist_1",
                event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED,
                source="scheduler",
                correlation_id=job_id,
                occurred_at=_utc_now(),
                recorded_at=_utc_now(),
                event_version="1.0",
                sensitivity=Sensitivity.INTERNAL,
                payload={"job_id": job_id},
            )
            await event_repo.record(event)
            await session.commit()

        response = await async_client.get(f"/api/scheduler/jobs/{job_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["entries"][0]["correlation_id"] == job_id

    async def test_history_empty(self, async_client: AsyncClient):
        response = await async_client.get("/api/scheduler/jobs/nonexistent_job/history")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []
        assert data["total"] == 0

    async def test_history_pagination(self, async_client: AsyncClient, session_factory):
        job_id = "job_hist_page"
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": job_id,
                "name": "Page Job",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        async with session_factory() as session:
            from app.events.repository import EventRepository
            from app.events.schemas import Event, EventType, Sensitivity
            event_repo = EventRepository(session)
            for i in range(10):
                event = Event(
                    event_id=f"evt_page_{i:03d}",
                    event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED,
                    source="scheduler",
                    correlation_id=job_id,
                    occurred_at=_utc_now() + timedelta(seconds=i),
                    recorded_at=_utc_now(),
                    event_version="1.0",
                    sensitivity=Sensitivity.INTERNAL,
                    payload={"index": i},
                )
                await event_repo.record(event)
            await session.commit()

        response = await async_client.get(f"/api/scheduler/jobs/{job_id}/history?limit=3&offset=2")
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 3
        assert data["offset"] == 2
        assert len(data["entries"]) == 3

    async def test_history_invalid_pagination(self, async_client: AsyncClient):
        response = await async_client.get("/api/scheduler/jobs/job_1/history?limit=501")
        assert response.status_code == 422

        response = await async_client.get("/api/scheduler/jobs/job_1/history?offset=100001")
        assert response.status_code == 422

    async def test_history_correlation_convention(self, async_client: AsyncClient, session_factory):
        job_id = "job_hist_corr"
        await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": job_id,
                "name": "Corr Job",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        async with session_factory() as session:
            from app.events.repository import EventRepository
            from app.events.schemas import Event, EventType, Sensitivity
            event_repo = EventRepository(session)
            for i in range(3):
                event = Event(
                    event_id=f"evt_corr_{i}",
                    event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED if i == 0 else EventType.SCHEDULER_OCCURRENCE_TRIGGERED,
                    source="scheduler",
                    correlation_id=job_id,
                    occurred_at=_utc_now() + timedelta(seconds=i),
                    recorded_at=_utc_now(),
                    event_version="1.0",
                    sensitivity=Sensitivity.INTERNAL,
                    payload={"seq": i},
                )
                await event_repo.record(event)
            await session.commit()

        response = await async_client.get(f"/api/scheduler/jobs/{job_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert all(e["correlation_id"] == job_id for e in data["entries"])


# ── ERROR / SECURITY ───────────────────────────────────────────────────────────


class TestErrorSecurity:
    async def test_malformed_json_returns_422(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    async def test_unexpected_repository_failure_returns_500(self, async_client: AsyncClient):
        app = async_client._transport.app
        factory = app.state._test_session_factory
        broken_repo = AsyncMock()
        broken_repo.create_job = AsyncMock(side_effect=sqlite3.DatabaseError("disk full"))
        broken_repo.get_job = AsyncMock(return_value=None)
        broken_repo.list_jobs = AsyncMock(return_value=([], 0))
        broken_repo.update_job = AsyncMock(side_effect=sqlite3.DatabaseError("disk full"))
        broken_repo.delete_job = AsyncMock(side_effect=sqlite3.DatabaseError("disk full"))
        broken_repo.enable_job = AsyncMock(side_effect=sqlite3.DatabaseError("disk full"))
        broken_repo.disable_job = AsyncMock(side_effect=sqlite3.DatabaseError("disk full"))

        from app.scheduler.service import SchedulerService
        broken_svc = SchedulerService(broken_repo, task_repo=None)

        async def _get_broken_svc():
            yield broken_svc

        from app.dependencies import get_scheduler_service
        app.dependency_overrides[get_scheduler_service] = _get_broken_svc

        try:
            response = await async_client.post(
                "/api/scheduler/jobs",
                json={
                    "job_id": "job_broken",
                    "name": "Broken",
                    "schedule_type": "cron",
                    "schedule_expr": "*/5 * * * *",
                    "timezone": None,
                    "target_type": "task",
                    "target_id": "task_abc123",
                    "enabled": True,
                },
            )
            assert response.status_code == 500
            assert "sk-1234567890abcdef" not in str(response.content)
        finally:
            del app.dependency_overrides[get_scheduler_service]

    async def test_no_sensitive_internal_fields_exposed(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_sec_check",
                "name": "Security",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "claim_token" not in data
        assert "occurrence_id" not in data
        assert "lease_until" not in data
        assert "execution_id" not in data
        assert "metadata" not in data
        assert "claimed_at" not in data

    async def test_no_execution_triggered_by_control_plane(self, async_client: AsyncClient):
        response = await async_client.post(
            "/api/scheduler/jobs",
            json={
                "job_id": "job_no_exec_api",
                "name": "NoExec",
                "schedule_type": "cron",
                "schedule_expr": "*/5 * * * *",
                "timezone": None,
                "target_type": "task",
                "target_id": "task_abc123",
                "enabled": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "PENDING"

        patch_response = await async_client.patch(
            "/api/scheduler/jobs/job_no_exec_api",
            json={"name": "Renamed"},
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["status"] == "PENDING"

        enable_response = await async_client.post("/api/scheduler/jobs/job_no_exec_api/enable")
        assert enable_response.status_code == 200
        assert enable_response.json()["status"] == "PENDING"

        disable_response = await async_client.post("/api/scheduler/jobs/job_no_exec_api/disable")
        assert disable_response.status_code == 200
        assert disable_response.json()["status"] == "PENDING"
