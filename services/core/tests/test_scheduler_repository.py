"""
Stage 12.2A — Scheduler Repository Tests

Covers:
  - create/get/update/delete
  - list/filter behavior
  - uniqueness constraints
  - persistence round-trip
  - repository error propagation
  - proof that persistence does not imply exactly-once execution
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.db.models.scheduled_job import ScheduledJobModel
from app.scheduler.models import ScheduledJob, ScheduleType, SchedulerJobStatus
from app.scheduler.repository import SchedulerRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _make_job(**kwargs: Any) -> ScheduledJob:
    now = _utc_now()
    defaults: dict[str, Any] = {
        "job_id": f"sched_{uuid4().hex[:12]}",
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
        "claimed_at": None,
        "lease_until": None,
        "execution_id": None,
        "metadata": None,
    }
    defaults.update(kwargs)
    return ScheduledJob(**defaults)


class TestSchedulerRepository:
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        yield SchedulerRepository(session, event_emitter=AsyncMock())
        await session.close()
        await engine.dispose()

    async def test_create_and_get_job(self, repo):
        job = _make_job()
        created = await repo.create_job(job)
        assert created.job_id == job.job_id
        assert created.name == job.name

        fetched = await repo.get_job(job.job_id)
        assert fetched is not None
        assert fetched.job_id == job.job_id
        assert fetched.schedule_type == ScheduleType.CRON
        assert fetched.status == SchedulerJobStatus.PENDING

    async def test_get_missing_job_returns_none(self, repo):
        result = await repo.get_job("nonexistent")
        assert result is None

    async def test_delete_existing_job(self, repo):
        job = _make_job()
        await repo.create_job(job)
        deleted = await repo.delete_job(job.job_id)
        assert deleted is True
        assert await repo.get_job(job.job_id) is None

    async def test_delete_missing_job_returns_false(self, repo):
        deleted = await repo.delete_job("nonexistent")
        assert deleted is False

    async def test_update_job(self, repo):
        job = _make_job()
        await repo.create_job(job)
        updated = await repo.update_job(job.job_id, name="Updated Name", enabled=False)
        assert updated is not None
        assert updated.name == "Updated Name"
        assert updated.enabled is False

    async def test_update_missing_job_returns_none(self, repo):
        updated = await repo.update_job("nonexistent", name="New Name")
        assert updated is None

    async def test_update_invalid_field_raises(self, repo):
        job = _make_job()
        await repo.create_job(job)
        with pytest.raises(ValueError, match="not allowed"):
            await repo.update_job(job.job_id, invalid_field="value")

    async def test_list_jobs_default(self, repo):
        job1 = _make_job(target_id="task_1")
        job2 = _make_job(target_id="task_2")
        await repo.create_job(job1)
        await repo.create_job(job2)
        jobs = await repo.list_jobs()
        assert len(jobs) == 2

    async def test_list_jobs_with_target_filter(self, repo):
        job1 = _make_job(target_type="task", target_id="task_1")
        job2 = _make_job(target_type="plan", target_id="plan_1")
        await repo.create_job(job1)
        await repo.create_job(job2)
        task_jobs = await repo.list_jobs(target_type="task", target_id="task_1")
        assert len(task_jobs) == 1
        assert task_jobs[0].job_id == job1.job_id

    async def test_list_jobs_with_status_filter(self, repo):
        job1 = _make_job(status=SchedulerJobStatus.PENDING)
        job2 = _make_job(status=SchedulerJobStatus.COMPLETED)
        await repo.create_job(job1)
        await repo.create_job(job2)
        pending = await repo.list_jobs(status=SchedulerJobStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].job_id == job1.job_id

    async def test_list_jobs_with_enabled_filter(self, repo):
        job1 = _make_job(enabled=True)
        job2 = _make_job(enabled=False)
        await repo.create_job(job1)
        await repo.create_job(job2)
        enabled_jobs = await repo.list_jobs(enabled=True)
        assert len(enabled_jobs) == 1
        assert enabled_jobs[0].job_id == job1.job_id

    async def test_list_jobs_with_schedule_type_filter(self, repo):
        job1 = _make_job(schedule_type=ScheduleType.CRON)
        job2 = _make_job(schedule_type=ScheduleType.INTERVAL)
        await repo.create_job(job1)
        await repo.create_job(job2)
        cron_jobs = await repo.list_jobs(schedule_type=ScheduleType.CRON)
        assert len(cron_jobs) == 1
        assert cron_jobs[0].job_id == job1.job_id

    async def test_list_jobs_pagination(self, repo):
        for i in range(10):
            job = _make_job(job_id=f"sched_{i:03d}", target_id=f"task_{i:03d}")
            await repo.create_job(job)
        page1 = await repo.list_jobs(limit=3, offset=0)
        assert len(page1) == 3
        page2 = await repo.list_jobs(limit=3, offset=3)
        assert len(page2) == 3
        ids1 = {j.job_id for j in page1}
        ids2 = {j.job_id for j in page2}
        assert len(ids1 & ids2) == 0

    async def test_get_jobs_by_target(self, repo):
        job1 = _make_job(target_type="task", target_id="task_abc")
        job2 = _make_job(target_type="task", target_id="task_abc")
        job3 = _make_job(target_type="task", target_id="task_xyz")
        await repo.create_job(job1)
        await repo.create_job(job2)
        await repo.create_job(job3)
        target_jobs = await repo.get_jobs_by_target("task", "task_abc")
        assert len(target_jobs) == 2
        assert all(j.target_id == "task_abc" for j in target_jobs)

    async def test_count_jobs(self, repo):
        job1 = _make_job(target_type="task", target_id="task_1")
        job2 = _make_job(target_type="plan", target_id="plan_1")
        await repo.create_job(job1)
        await repo.create_job(job2)
        total = await repo.count_jobs()
        assert total == 2
        task_count = await repo.count_jobs(target_type="task")
        assert task_count == 1

    async def test_job_id_uniqueness(self, repo):
        job1 = _make_job(job_id="sched_duplicate")
        job2 = _make_job(job_id="sched_duplicate")
        await repo.create_job(job1)
        with pytest.raises(Exception):
            await repo.create_job(job2)

    async def test_persistence_roundtrip(self, repo):
        original = _make_job(
            job_id="sched_roundtrip",
            name="Roundtrip Job",
            schedule_type=ScheduleType.INTERVAL,
            schedule_expr="600",
            timezone="America/New_York",
            target_type="plan",
            target_id="plan_abc",
            status=SchedulerJobStatus.CLAIMED,
            occurrence_id="occurrence_sched_abc123",
            claim_token="token_123",
            execution_id="exec_456",
            metadata={"key": "value"},
        )
        created = await repo.create_job(original)
        fetched = await repo.get_job(created.job_id)
        assert fetched is not None
        assert fetched.job_id == original.job_id
        assert fetched.name == original.name
        assert fetched.schedule_type == ScheduleType.INTERVAL
        assert fetched.schedule_expr == "600"
        assert fetched.timezone == "America/New_York"
        assert fetched.target_type == "plan"
        assert fetched.target_id == "plan_abc"
        assert fetched.status == SchedulerJobStatus.CLAIMED
        assert fetched.occurrence_id == "occurrence_sched_abc123"
        assert fetched.claim_token == "token_123"
        assert fetched.execution_id == "exec_456"
        assert fetched.metadata == {"key": "value"}

    async def test_datetime_persistence(self, repo):
        now = _utc_now()
        job = _make_job(
            job_id="sched_datetime",
            last_run_at=now - timedelta(hours=1),
            next_run_at=now + timedelta(hours=1),
            claimed_at=now,
            lease_until=now + timedelta(minutes=5),
        )
        created = await repo.create_job(job)
        fetched = await repo.get_job(created.job_id)
        assert fetched is not None
        assert fetched.last_run_at is not None
        assert fetched.next_run_at is not None
        assert fetched.claimed_at is not None
        assert fetched.lease_until is not None

    async def test_update_status(self, repo):
        job = _make_job()
        await repo.create_job(job)
        updated = await repo.update_job(job.job_id, status=SchedulerJobStatus.CLAIMED)
        assert updated is not None
        assert updated.status == SchedulerJobStatus.CLAIMED

    async def test_update_next_run_at(self, repo):
        job = _make_job()
        await repo.create_job(job)
        new_next = _utc_now() + timedelta(hours=2)
        updated = await repo.update_job(job.job_id, next_run_at=new_next)
        assert updated is not None
        assert updated.next_run_at is not None

    async def test_list_jobs_combined_filters(self, repo):
        job1 = _make_job(target_type="task", target_id="task_1", enabled=True, status=SchedulerJobStatus.PENDING)
        job2 = _make_job(target_type="task", target_id="task_1", enabled=False, status=SchedulerJobStatus.PENDING)
        job3 = _make_job(target_type="task", target_id="task_1", enabled=True, status=SchedulerJobStatus.COMPLETED)
        await repo.create_job(job1)
        await repo.create_job(job2)
        await repo.create_job(job3)
        filtered = await repo.list_jobs(target_type="task", target_id="task_1", enabled=True, status=SchedulerJobStatus.PENDING)
        assert len(filtered) == 1
        assert filtered[0].job_id == job1.job_id


class TestPersistenceDoesNotImplyExecution:
    """
    Prove that scheduler persistence does not imply exactly-once execution.
    """
    @pytest.fixture
    async def repo(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        yield SchedulerRepository(session, event_emitter=AsyncMock())
        await session.close()
        await engine.dispose()

    async def test_persisted_job_has_not_executed(self, repo):
        job = _make_job()
        created = await repo.create_job(job)
        assert created.status == SchedulerJobStatus.PENDING
        assert created.last_run_at is None
        assert created.execution_id is None
        assert created.occurrence_id is None
        assert created.claim_token is None
        # Persistence of job definition does not imply any execution occurred

    async def test_multiple_persisted_jobs_independent(self, repo):
        job1 = _make_job(job_id="sched_a")
        job2 = _make_job(job_id="sched_b")
        await repo.create_job(job1)
        await repo.create_job(job2)
        assert await repo.get_job("sched_a") is not None
        assert await repo.get_job("sched_b") is not None
        # Each job is independent; persisting one does not affect the other

    async def test_claim_fields_do_not_execute(self, repo):
        job = _make_job(
            occurrence_id="occurrence_abc",
            claim_token="token_abc",
            claimed_at=_utc_now(),
            lease_until=_utc_now() + timedelta(minutes=5),
        )
        created = await repo.create_job(job)
        assert created.occurrence_id == "occurrence_abc"
        assert created.claim_token == "token_abc"
        # These fields are persisted but do NOT mean execution happened
        assert created.execution_id is None
        assert created.last_run_at is None
