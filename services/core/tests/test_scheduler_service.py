"""
Stage 12.3A — SchedulerService Tests

Covers:
  - create_job: valid schedules, idempotency, conflict, validation
  - update_job: status-dependent restrictions, schedule recalculation
  - delete_job: allowed/blocked by status
  - enable_job / disable_job: toggle only
  - list_jobs / get_job: delegation
  - target existence validation
  - no execution side effects
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.db.models.development_task import DevelopmentTaskModel
from app.db.models.scheduled_job import ScheduledJobModel
from app.repositories.task_repository import TaskRepository
from app.scheduler.models import ScheduledJob, ScheduleType, SchedulerJobStatus
from app.scheduler.repository import SchedulerRepository
from app.scheduler.service import JobIdempotentReturn, SchedulerService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _make_job(**kwargs: Any) -> ScheduledJob:
    now = _utc_now()
    defaults: dict[str, Any] = {
        "job_id": f"sched_{id(AsyncMock())}",
        "name": "Test Job",
        "enabled": True,
        "schedule_type": ScheduleType.CRON,
        "schedule_expr": "*/5 * * * *",
        "timezone": None,
        "target_type": "task",
        "target_id": "task_abc123",
        "status": SchedulerJobStatus.PENDING,
        "last_run_at": None,
        "next_run_at": now,
        "created_at": now,
        "updated_at": now,
        "occurrence_id": None,
        "claim_token": None,
        "claim_sequence": 0,
        "claimed_at": None,
        "lease_until": None,
        "execution_id": None,
        "metadata": None,
    }
    defaults.update(kwargs)
    return ScheduledJob(**defaults)


class TestSchedulerServiceCreate:
    @pytest.fixture
    async def repo_and_task_repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        repo = SchedulerRepository(session, event_emitter=AsyncMock())
        task_repo = TaskRepository(session)
        try:
            yield repo, task_repo, factory
        finally:
            await session.close()
            await engine.dispose()

    async def test_create_once_future(self, repo_and_task_repo):
        repo, task_repo, factory = repo_and_task_repo
        svc = SchedulerService(repo, task_repo=task_repo)
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        target_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        async with factory() as session:
            task = DevelopmentTaskModel(
                task_id="task_abc123",
                plan_id="plan_1",
                task_key="t1",
                title="Test",
                description="Test task",
                project_id="proj_1",
                status="PENDING",
                attempt_count=0,
                max_attempts=3,
                created_at=now,
            )
            session.add(task)
            await session.commit()

        job = await svc.create_job(
            job_id="job_1",
            name="ONCE Job",
            schedule_type="once",
            schedule_expr=target_time.isoformat(),
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            enabled=True,
            now=now,
        )
        assert job.status == SchedulerJobStatus.PENDING
        assert job.next_run_at == target_time
        assert job.last_run_at is None

    async def test_create_once_elapsed_rejected(self, repo_and_task_repo):
        repo, task_repo, factory = repo_and_task_repo
        svc = SchedulerService(repo, task_repo=task_repo)
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        past_time = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        async with factory() as session:
            task = DevelopmentTaskModel(
                task_id="task_abc123",
                plan_id="plan_1",
                task_key="t1",
                title="Test",
                description="Test task",
                project_id="proj_1",
                status="PENDING",
                attempt_count=0,
                max_attempts=3,
                created_at=now,
            )
            session.add(task)
            await session.commit()

        with pytest.raises(ValueError, match="in the past"):
            await svc.create_job(
                job_id="job_elapsed",
                name="Elapsed ONCE",
                schedule_type="once",
                schedule_expr=past_time.isoformat(),
                timezone=None,
                target_type="task",
                target_id="task_abc123",
                enabled=True,
                now=now,
            )

    async def test_create_interval_initial_next_run(self, repo_and_task_repo):
        repo, task_repo, factory = repo_and_task_repo
        svc = SchedulerService(repo, task_repo=task_repo)
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        async with factory() as session:
            task = DevelopmentTaskModel(
                task_id="task_abc123",
                plan_id="plan_1",
                task_key="t1",
                title="Test",
                description="Test task",
                project_id="proj_1",
                status="PENDING",
                attempt_count=0,
                max_attempts=3,
                created_at=now,
            )
            session.add(task)
            await session.commit()

        job = await svc.create_job(
            job_id="job_interval",
            name="Interval Job",
            schedule_type="interval",
            schedule_expr="3600",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            enabled=True,
            now=now,
        )
        assert job.next_run_at == now + timedelta(seconds=3600)

    async def test_create_cron_strict_future(self, repo_and_task_repo):
        repo, task_repo, factory = repo_and_task_repo
        svc = SchedulerService(repo, task_repo=task_repo)
        now = datetime(2026, 1, 1, 10, 17, 0, tzinfo=timezone.utc)

        async with factory() as session:
            task = DevelopmentTaskModel(
                task_id="task_abc123",
                plan_id="plan_1",
                task_key="t1",
                title="Test",
                description="Test task",
                project_id="proj_1",
                status="PENDING",
                attempt_count=0,
                max_attempts=3,
                created_at=now,
            )
            session.add(task)
            await session.commit()

        job = await svc.create_job(
            job_id="job_cron",
            name="CRON Job",
            schedule_type="cron",
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            enabled=True,
            now=now,
        )
        assert job.next_run_at == datetime(2026, 1, 1, 10, 20, 0, tzinfo=timezone.utc)

    async def test_create_missing_task_target_rejected(self, repo_and_task_repo):
        repo, task_repo, factory = repo_and_task_repo
        svc = SchedulerService(repo, task_repo=task_repo)
        now = _utc_now()

        with pytest.raises(ValueError, match="not found"):
            await svc.create_job(
                job_id="job_missing",
                name="Missing Target",
                schedule_type="cron",
                schedule_expr="*/5 * * * *",
                timezone=None,
                target_type="task",
                target_id="nonexistent_task",
                enabled=True,
                now=now,
            )

    async def test_create_plan_target_with_zero_tasks_not_rejected(self, repo_and_task_repo):
        repo, task_repo, factory = repo_and_task_repo
        svc = SchedulerService(repo, task_repo=task_repo)
        now = _utc_now()

        job = await svc.create_job(
            job_id="job_plan_empty",
            name="Plan with zero tasks",
            schedule_type="cron",
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="plan",
            target_id="plan_with_zero_tasks",
            enabled=True,
            now=now,
        )
        assert job.job_id == "job_plan_empty"
        assert job.target_type == "plan"

    async def test_create_idempotent_same_definition(self, repo_and_task_repo):
        repo, task_repo, factory = repo_and_task_repo
        svc = SchedulerService(repo, task_repo=task_repo)
        now = _utc_now()

        async with factory() as session:
            task = DevelopmentTaskModel(
                task_id="task_abc123",
                plan_id="plan_1",
                task_key="t1",
                title="Test",
                description="Test task",
                project_id="proj_1",
                status="PENDING",
                attempt_count=0,
                max_attempts=3,
                created_at=now,
            )
            session.add(task)
            await session.commit()

        job1 = await svc.create_job(
            job_id="job_idem",
            name="Idempotent",
            schedule_type="cron",
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            enabled=True,
            now=now,
        )
        with pytest.raises(JobIdempotentReturn) as exc_info:
            await svc.create_job(
                job_id="job_idem",
                name="Idempotent",
                schedule_type="cron",
                schedule_expr="*/5 * * * *",
                timezone=None,
                target_type="task",
                target_id="task_abc123",
                enabled=True,
                now=now,
            )
        job2 = exc_info.value.job
        assert job1.job_id == job2.job_id
        assert job1.next_run_at == job2.next_run_at

    async def test_create_idempotent_different_definition_conflict(self, repo_and_task_repo):
        repo, task_repo, factory = repo_and_task_repo
        svc = SchedulerService(repo, task_repo=task_repo)
        now = _utc_now()

        async with factory() as session:
            task = DevelopmentTaskModel(
                task_id="task_abc123",
                plan_id="plan_1",
                task_key="t1",
                title="Test",
                description="Test task",
                project_id="proj_1",
                status="PENDING",
                attempt_count=0,
                max_attempts=3,
                created_at=now,
            )
            session.add(task)
            await session.commit()

        await svc.create_job(
            job_id="job_conflict",
            name="Original",
            schedule_type="cron",
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            enabled=True,
            now=now,
        )
        with pytest.raises(ValueError, match="different definition"):
            await svc.create_job(
                job_id="job_conflict",
                name="Modified",
                schedule_type="cron",
                schedule_expr="*/5 * * * *",
                timezone=None,
                target_type="task",
                target_id="task_abc123",
                enabled=True,
                now=now,
            )

    async def test_create_never_overwrites_existing(self, repo_and_task_repo):
        repo, task_repo, factory = repo_and_task_repo
        svc = SchedulerService(repo, task_repo=task_repo)
        now = _utc_now()

        async with factory() as session:
            task = DevelopmentTaskModel(
                task_id="task_abc123",
                plan_id="plan_1",
                task_key="t1",
                title="Test",
                description="Test task",
                project_id="proj_1",
                status="PENDING",
                attempt_count=0,
                max_attempts=3,
                created_at=now,
            )
            session.add(task)
            await session.commit()

        job1 = await svc.create_job(
            job_id="job_no_overwrite",
            name="Original",
            schedule_type="cron",
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            enabled=True,
            now=now,
        )
        original_updated_at = job1.updated_at

        with pytest.raises(ValueError, match="different definition"):
            await svc.create_job(
                job_id="job_no_overwrite",
                name="Should not overwrite",
                schedule_type="cron",
                schedule_expr="*/5 * * * *",
                timezone=None,
                target_type="task",
                target_id="task_abc123",
                enabled=True,
                now=now,
            )

        fetched = await svc.get_job("job_no_overwrite")
        assert fetched.name == "Original"
        assert fetched.updated_at == original_updated_at


class TestSchedulerServiceUpdate:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        repo = SchedulerRepository(session, event_emitter=AsyncMock())
        try:
            yield repo, factory
        finally:
            await session.close()
            await engine.dispose()

    async def test_update_pending_schedule_recalculates(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            job_id="job_pending",
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            next_run_at=now,
        )
        await repo.create_job(job)
        await repo._session.commit()

        updated = await svc.update_job(
            "job_pending",
            {"schedule_expr": "0 * * * *"},
            now=now,
        )
        assert updated.next_run_at == datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

    async def test_update_claimed_rejects_schedule_changes(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            job_id="job_claimed",
            status=SchedulerJobStatus.CLAIMED,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            next_run_at=now,
        )
        await repo.create_job(job)
        await repo._session.commit()

        with pytest.raises(ValueError, match="Cannot modify schedule while occurrence is CLAIMED"):
            await svc.update_job("job_claimed", {"schedule_expr": "0 * * * *"}, now=now)

    async def test_update_claimed_allows_enabled(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            job_id="job_claimed_enabled",
            status=SchedulerJobStatus.CLAIMED,
            enabled=True,
            next_run_at=now,
        )
        await repo.create_job(job)
        await repo._session.commit()

        updated = await svc.update_job("job_claimed_enabled", {"enabled": False}, now=now)
        assert updated.enabled is False

    async def test_update_triggered_allows_schedule_changes(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            job_id="job_triggered",
            status=SchedulerJobStatus.TRIGGERED,
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            last_run_at=now,
            next_run_at=now + timedelta(minutes=5),
        )
        await repo.create_job(job)
        await repo._session.commit()

        updated = await svc.update_job(
            "job_triggered",
            {"schedule_expr": "0 * * * *"},
            now=now,
        )
        assert updated.next_run_at == datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

    async def test_update_rejects_immutable_occurrence_fields(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(job_id="job_immutable")
        await repo.create_job(job)
        await repo._session.commit()

        with pytest.raises(ValueError, match="cannot be modified"):
            await svc.update_job(
                "job_immutable",
                {
                    "occurrence_id": "new_occurrence",
                    "claim_token": "new_token",
                    "claim_sequence": 99,
                },
                now=now,
            )

    async def test_update_no_future_occurrence_disables(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            job_id="job_no_future",
            schedule_type=ScheduleType.ONCE,
            schedule_expr="2020-01-01T00:00:00Z",
            last_run_at=now,
            next_run_at=now,
        )
        await repo.create_job(job)
        await repo._session.commit()

        updated = await svc.update_job(
            "job_no_future",
            {"schedule_expr": "2020-01-01T00:00:00Z"},
            now=now,
        )
        assert updated.status == SchedulerJobStatus.DISABLED
        assert updated.enabled is False

    async def test_update_last_run_at_never_changed(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        original_last = now - timedelta(hours=1)
        job = _make_job(
            job_id="job_last_run",
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            last_run_at=original_last,
            next_run_at=now,
        )
        await repo.create_job(job)
        await repo._session.commit()

        updated = await svc.update_job(
            "job_last_run",
            {"schedule_expr": "0 * * * *"},
            now=now,
        )
        assert updated.last_run_at == original_last


class TestSchedulerServiceDelete:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        repo = SchedulerRepository(session, event_emitter=AsyncMock())
        try:
            yield repo, factory
        finally:
            await session.close()
            await engine.dispose()

    async def test_delete_pending_allowed(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(job_id="job_delete_pending")
        await repo.create_job(job)
        await repo._session.commit()

        await svc.delete_job("job_delete_pending")
        assert await repo.get_job("job_delete_pending") is None

    async def test_delete_claimed_rejected(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(
            job_id="job_delete_claimed",
            status=SchedulerJobStatus.CLAIMED,
        )
        await repo.create_job(job)
        await repo._session.commit()

        with pytest.raises(ValueError, match="active claim"):
            await svc.delete_job("job_delete_claimed")

    async def test_delete_triggered_rejected(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(
            job_id="job_delete_triggered",
            status=SchedulerJobStatus.TRIGGERED,
        )
        await repo.create_job(job)
        await repo._session.commit()

        with pytest.raises(ValueError, match="execution in progress"):
            await svc.delete_job("job_delete_triggered")

    async def test_delete_failed_allowed(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(
            job_id="job_delete_failed",
            status=SchedulerJobStatus.FAILED,
        )
        await repo.create_job(job)
        await repo._session.commit()

        await svc.delete_job("job_delete_failed")
        assert await repo.get_job("job_delete_failed") is None


class TestSchedulerServiceEnableDisable:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        repo = SchedulerRepository(session, event_emitter=AsyncMock())
        try:
            yield repo, factory
        finally:
            await session.close()
            await engine.dispose()

    async def test_enable_toggles_only(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(job_id="job_enable", enabled=False)
        await repo.create_job(job)
        await repo._session.commit()

        updated = await svc.enable_job("job_enable")
        assert updated.enabled is True
        assert updated.status == SchedulerJobStatus.PENDING

    async def test_disable_toggles_only(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(job_id="job_disable", enabled=True)
        await repo.create_job(job)
        await repo._session.commit()

        updated = await svc.disable_job("job_disable")
        assert updated.enabled is False
        assert updated.status == SchedulerJobStatus.PENDING

    async def test_disable_does_not_cancel_execution(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(
            job_id="job_disable_triggered",
            status=SchedulerJobStatus.TRIGGERED,
            enabled=True,
        )
        await repo.create_job(job)
        await repo._session.commit()

        updated = await svc.disable_job("job_disable_triggered")
        assert updated.enabled is False
        assert updated.status == SchedulerJobStatus.TRIGGERED


class TestSchedulerServiceListGet:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        repo = SchedulerRepository(session, event_emitter=AsyncMock())
        try:
            yield repo, factory
        finally:
            await session.close()
            await engine.dispose()

    async def test_get_existing(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(job_id="job_get")
        await repo.create_job(job)
        await repo._session.commit()

        fetched = await svc.get_job("job_get")
        assert fetched is not None
        assert fetched.job_id == "job_get"

    async def test_get_missing_returns_none(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)

        fetched = await svc.get_job("nonexistent")
        assert fetched is None

    async def test_list_jobs(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        for i in range(3):
            job = _make_job(job_id=f"job_list_{i}", next_run_at=now + timedelta(minutes=i))
            await repo.create_job(job)
        await repo._session.commit()

        jobs, total = await svc.list_jobs(limit=10, offset=0)
        assert total == 3
        assert len(jobs) == 3


class TestSchedulerServiceNoExecutionSideEffects:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        repo = SchedulerRepository(session, event_emitter=AsyncMock())
        try:
            yield repo, factory
        finally:
            await session.close()
            await engine.dispose()

    async def test_create_does_not_execute_target(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo, task_repo=None)
        now = _utc_now()

        job = await svc.create_job(
            job_id="job_no_exec",
            name="No Exec",
            schedule_type="cron",
            schedule_expr="*/5 * * * *",
            timezone=None,
            target_type="task",
            target_id="task_abc123",
            enabled=True,
            now=now,
        )
        assert job.status == SchedulerJobStatus.PENDING

    async def test_update_does_not_execute_target(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(job_id="job_update_no_exec")
        await repo.create_job(job)
        await repo._session.commit()

        updated = await svc.update_job("job_update_no_exec", {"name": "Renamed"}, now=now)
        assert updated.name == "Renamed"

    async def test_delete_does_not_execute_target(self, repo_and_factory):
        repo, factory = repo_and_factory
        svc = SchedulerService(repo)
        now = _utc_now()
        job = _make_job(job_id="job_delete_no_exec")
        await repo.create_job(job)
        await repo._session.commit()

        await svc.delete_job("job_delete_no_exec")
        assert await repo.get_job("job_delete_no_exec") is None
