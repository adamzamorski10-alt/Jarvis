"""
Stage 12.2F — Scheduler Worker Tests

Covers:
  - Worker startup/shutdown
  - Due job polling
  - Claim → handoff → advancement integration
  - ONCE/INTERVAL/CRON advancement
  - Recovery
  - Handoff failure
  - Event emission
  - SKIP_ONCE behavior
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.scheduler.calculator import ScheduleDefinition, calculate_next_occurrence
from app.scheduler.lease import generate_occurrence_id, generate_claim_token, now_utc, DEFAULT_LEASE_DURATION
from app.scheduler.models import ScheduledJob, ScheduleType, SchedulerJobStatus
from app.scheduler.repository import SchedulerRepository
from app.scheduler.handoff import SchedulerHandoffService, HandoffResult
from app.scheduler.worker import SchedulerWorker
from app.tasks.models import TaskStatus


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


class TestWorkerStartupShutdown:
    @pytest.fixture
    async def factory_and_engine(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        yield factory, engine
        await engine.dispose()

    async def test_worker_starts_and_stops(self, factory_and_engine):
        factory, engine = factory_and_engine
        worker = SchedulerWorker(
            session_factory=factory,
            handoff_factory=lambda repo: MagicMock(),
        )
        await worker.start()
        assert worker._running is True
        assert worker._task is not None
        await worker.stop()
        assert worker._running is False
        assert worker._task is None

    async def test_worker_start_idempotent(self, factory_and_engine):
        factory, engine = factory_and_engine
        worker = SchedulerWorker(
            session_factory=factory,
            handoff_factory=lambda repo: MagicMock(),
        )
        await worker.start()
        await worker.start()  # second start should be no-op
        assert worker._running is True
        await worker.stop()


class TestWorkerDueFiltering:
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

    async def test_due_jobs_returned(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(next_run_at=now - timedelta(seconds=10))
        await repo.create_job(job)
        await repo._session.commit()

        due = await repo.get_due_pending_jobs(now)
        assert len(due) == 1
        assert due[0].job_id == job.job_id

    async def test_future_jobs_not_returned(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(next_run_at=now + timedelta(hours=1))
        await repo.create_job(job)
        await repo._session.commit()

        due = await repo.get_due_pending_jobs(now)
        assert len(due) == 0


class TestWorkerAdvancement:
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

    async def test_once_advancement_disables_when_elapsed(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        past = now - timedelta(days=1)
        job = _make_job(
            schedule_type=ScheduleType.ONCE,
            schedule_expr=past.isoformat(),
            next_run_at=past,
        )
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        advanced = await repo.finalize_triggered_and_advance(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            execution_id="exec_123",
            now=now,
        )
        assert advanced is not None
        # ONCE job that has already fired: DISABLED because target time is past
        assert advanced.status == SchedulerJobStatus.DISABLED

    async def test_interval_advancement(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(
            schedule_type=ScheduleType.INTERVAL,
            schedule_expr="3600",
            next_run_at=now,
        )
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        advanced = await repo.finalize_triggered_and_advance(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            execution_id="exec_123",
            now=now,
        )
        assert advanced is not None
        assert advanced.status == SchedulerJobStatus.TRIGGERED
        assert advanced.last_run_at == now
        expected_next = now + timedelta(seconds=3600)
        assert advanced.next_run_at is not None
        assert abs((advanced.next_run_at - expected_next).total_seconds()) < 1

    async def test_cron_advancement(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(
            schedule_type=ScheduleType.CRON,
            schedule_expr="0 * * * *",
            next_run_at=now,
        )
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        advanced = await repo.finalize_triggered_and_advance(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            execution_id="exec_123",
            now=now,
        )
        assert advanced is not None
        assert advanced.status == SchedulerJobStatus.TRIGGERED
        assert advanced.last_run_at == now
        assert advanced.next_run_at is not None
        assert advanced.next_run_at > now


class TestWorkerRecovery:
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

    async def test_expired_lease_recovery(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(next_run_at=now)
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        # Expire the lease
        after_expiry = now + DEFAULT_LEASE_DURATION + timedelta(seconds=1)
        recovered = await repo.reset_expired_claim_to_pending(occurrence_id, after_expiry, after_expiry)
        assert recovered is not None
        assert recovered.status == SchedulerJobStatus.PENDING


class TestWorkerHandoffFailure:
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

    async def test_failed_handoff_does_not_advance(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(next_run_at=now)
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        # Simulate failed handoff by NOT calling finalize
        fetched = await repo.get_job(job.job_id)
        assert fetched.status == SchedulerJobStatus.CLAIMED
        assert fetched.next_run_at == now  # not advanced


class TestWorkerSKIP_ONCE:
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

    async def test_interval_skips_missed_occurrences(self, repo_and_factory):
        """Missed intervals should be skipped, not caught up."""
        repo, factory = repo_and_factory
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            schedule_type=ScheduleType.INTERVAL,
            schedule_expr="3600",
            next_run_at=base,
        )
        await repo.create_job(job)
        await repo._session.commit()

        # Worker wakes at 13:37, next_run_at is still 10:00
        wake_time = datetime(2026, 1, 1, 13, 37, 0, tzinfo=timezone.utc)

        occurrence_id = generate_occurrence_id(job.job_id, wake_time)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, wake_time, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        advanced = await repo.finalize_triggered_and_advance(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            execution_id="exec_123",
            now=wake_time,
        )
        assert advanced is not None
        assert advanced.status == SchedulerJobStatus.TRIGGERED
        assert advanced.last_run_at == base
        expected_next = wake_time + timedelta(seconds=3600)
        assert advanced.next_run_at is not None
        assert abs((advanced.next_run_at - expected_next).total_seconds()) < 1


class TestWorkerSkipPath:
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

    async def test_skip_when_target_already_running(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(next_run_at=now)
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        skipped = await repo.finalize_skipped(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            now=now,
            reason_code="target_already_running",
        )
        assert skipped is not None
        assert skipped.status == SchedulerJobStatus.SKIPPED
        assert skipped.next_run_at is not None
        assert skipped.next_run_at > now

    async def test_skip_advances_schedule(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(
            schedule_type=ScheduleType.INTERVAL,
            schedule_expr="3600",
            next_run_at=now,
        )
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        skip_time = now + timedelta(seconds=10)
        skipped = await repo.finalize_skipped(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            now=skip_time,
            reason_code="target_already_running",
        )
        assert skipped is not None
        assert skipped.status == SchedulerJobStatus.SKIPPED
        assert skipped.last_run_at == now
        expected_next = skip_time + timedelta(seconds=3600)
        assert skipped.next_run_at is not None
        assert abs((skipped.next_run_at - expected_next).total_seconds()) < 1

    async def test_skip_when_lease_expired_returns_none(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(next_run_at=now)
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        after_expiry = now + DEFAULT_LEASE_DURATION + timedelta(seconds=1)
        skipped = await repo.finalize_skipped(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            now=after_expiry,
        )
        assert skipped is None

    async def test_skip_wrong_token_returns_none(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(next_run_at=now)
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        skipped = await repo.finalize_skipped(
            occurrence_id=occurrence_id,
            claim_token="wrong_token",
            now=now,
        )
        assert skipped is None


class TestWorkerRecoverySKIP_ONCE:
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

    async def test_recovery_skips_missed_occurrences_interval(self, repo_and_factory):
        """Recovery should skip missed intervals, not catch up."""
        repo, factory = repo_and_factory
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            schedule_type=ScheduleType.INTERVAL,
            schedule_expr="3600",
            next_run_at=base,
        )
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, base)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, base, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        # Worker crashes, recovers at 13:37
        wake_time = datetime(2026, 1, 1, 13, 37, 0, tzinfo=timezone.utc)

        worker = SchedulerWorker(session_factory=factory, handoff_factory=lambda repo: None)
        next_run = worker._compute_next_run(job, wake_time)
        expected = wake_time + timedelta(seconds=3600)
        assert next_run is not None
        assert abs((next_run - expected).total_seconds()) < 1

    async def test_recovery_skips_missed_occurrences_cron(self, repo_and_factory):
        """Recovery should skip missed cron matches, not catch up."""
        repo, factory = repo_and_factory
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            schedule_type=ScheduleType.CRON,
            schedule_expr="0 * * * *",
            next_run_at=base,
        )
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, base)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, base, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        # Worker crashes, recovers at 13:37
        wake_time = datetime(2026, 1, 1, 13, 37, 0, tzinfo=timezone.utc)

        worker = SchedulerWorker(session_factory=factory, handoff_factory=lambda repo: None)
        next_run = worker._compute_next_run(job, wake_time)
        expected = datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone.utc)
        assert next_run is not None
        assert abs((next_run - expected).total_seconds()) < 1


class TestWorkerFailurePath:
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

    async def test_fail_missing_target(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(
            target_type="task",
            target_id="nonexistent_task",
            next_run_at=now,
        )
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        failed = await repo.finalize_failed(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            now=now,
            reason_code="target_not_found",
        )
        assert failed is True
        fetched = await repo.get_job(job.job_id)
        assert fetched.status == SchedulerJobStatus.FAILED

    async def test_fail_terminal_target(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(next_run_at=now)
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        failed = await repo.finalize_failed(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            now=now,
            reason_code="target_invalid",
            reason_detail="Task is in terminal status 'COMMITTED'",
        )
        assert failed is True
        fetched = await repo.get_job(job.job_id)
        assert fetched.status == SchedulerJobStatus.FAILED

    async def test_fail_stale_claimant_returns_false(self, repo_and_factory):
        repo, factory = repo_and_factory
        now = _utc_now()
        job = _make_job(next_run_at=now)
        await repo.create_job(job)
        await repo._session.commit()

        occurrence_id = generate_occurrence_id(job.job_id, now)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        after_expiry = now + DEFAULT_LEASE_DURATION + timedelta(seconds=1)
        failed = await repo.finalize_failed(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            now=after_expiry,
        )
        assert failed is False
        fetched = await repo.get_job(job.job_id)
        assert fetched.status == SchedulerJobStatus.CLAIMED


class TestWorkerSkipIntegration:
    """Integration tests for the worker's skip/fail/handoff decision logic."""

    def test_is_already_running_error_detection(self):
        assert SchedulerWorker._is_already_running_error("Task is already in status 'RUNNING'")
        assert SchedulerWorker._is_already_running_error("Task is already in status 'VERIFYING'")
        assert SchedulerWorker._is_already_running_error("Task is already in status 'PASSED'")
        assert SchedulerWorker._is_already_running_error("Plan already executing: exec_abc")
        assert not SchedulerWorker._is_already_running_error("Target task not found")
        assert not SchedulerWorker._is_already_running_error("Execution error")

    def test_is_permanent_target_error_detection(self):
        assert SchedulerWorker._is_permanent_target_error("Target task not found")
        assert SchedulerWorker._is_permanent_target_error("Task is in terminal status 'COMMITTED'")
        assert SchedulerWorker._is_permanent_target_error("Plan not executable: invalid")
        assert SchedulerWorker._is_permanent_target_error("Task not startable: paused")
        assert SchedulerWorker._is_permanent_target_error("Max attempts exceeded")
        assert SchedulerWorker._is_permanent_target_error("Stale task state")
        assert SchedulerWorker._is_permanent_target_error("Execution error")
        assert not SchedulerWorker._is_permanent_target_error("already in status 'RUNNING'")

    def test_reason_code_mapping(self):
        assert SchedulerWorker._reason_code_for_error("Target task not found") == "target_not_found"
        assert SchedulerWorker._reason_code_for_error("Task is in terminal status 'COMMITTED'") == "target_invalid"
        assert SchedulerWorker._reason_code_for_error("Plan not executable") == "target_invalid"
        assert SchedulerWorker._reason_code_for_error("Unexpected") == "execution_error"
        assert SchedulerWorker._reason_code_for_error("") == "execution_error"


class TestWorkerMisfireSkipOnce:
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

    async def test_interval_misfire_skips_to_future(self, repo_and_factory):
        """Worker down from 10:00 to 13:37. Next run should be 14:37, not 11:00."""
        repo, factory = repo_and_factory
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            schedule_type=ScheduleType.INTERVAL,
            schedule_expr="3600",
            next_run_at=base,
        )
        await repo.create_job(job)
        await repo._session.commit()

        wake_time = datetime(2026, 1, 1, 13, 37, 0, tzinfo=timezone.utc)
        occurrence_id = generate_occurrence_id(job.job_id, wake_time)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, wake_time, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        advanced = await repo.finalize_triggered_and_advance(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            execution_id="exec_123",
            now=wake_time,
        )
        assert advanced is not None
        assert advanced.status == SchedulerJobStatus.TRIGGERED
        assert advanced.last_run_at == base
        expected_next = datetime(2026, 1, 1, 14, 37, 0, tzinfo=timezone.utc)
        assert advanced.next_run_at is not None
        assert abs((advanced.next_run_at - expected_next).total_seconds()) < 1

    async def test_cron_misfire_skips_to_next_match(self, repo_and_factory):
        """Worker down from 10:00 to 10:17. Next CRON match should be 10:20."""
        repo, factory = repo_and_factory
        base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        job = _make_job(
            schedule_type=ScheduleType.CRON,
            schedule_expr="*/5 * * * *",
            next_run_at=base,
        )
        await repo.create_job(job)
        await repo._session.commit()

        wake_time = datetime(2026, 1, 1, 10, 17, 0, tzinfo=timezone.utc)
        occurrence_id = generate_occurrence_id(job.job_id, wake_time)
        claim_token = generate_claim_token()
        claimed = await repo.claim_next_occurrence(job.job_id, wake_time, DEFAULT_LEASE_DURATION, claim_token, occurrence_id)
        assert claimed is not None
        await repo._session.commit()

        advanced = await repo.finalize_triggered_and_advance(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            execution_id="exec_123",
            now=wake_time,
        )
        assert advanced is not None
        assert advanced.status == SchedulerJobStatus.TRIGGERED
        expected_next = datetime(2026, 1, 1, 10, 20, 0, tzinfo=timezone.utc)
        assert advanced.next_run_at is not None
        assert abs((advanced.next_run_at - expected_next).total_seconds()) < 1


class TestWorkerPollIntegration:
    @pytest.fixture
    async def factory_and_engine(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        yield factory, engine
        await engine.dispose()

    async def test_poll_once_with_successful_handoff(self, factory_and_engine):
        factory, engine = factory_and_engine
        now = _utc_now()

        handoff_calls = []
        def mock_handoff(repo):
            class FakeHandoff:
                async def handoff_task(self, occurrence_id, claim_token):
                    handoff_calls.append((occurrence_id, claim_token))
                    return HandoffResult(
                        success=True,
                        occurrence_id=occurrence_id,
                        execution_id="exec_123",
                        target_type="task",
                        target_id="task_abc123",
                    )
                async def handoff_plan(self, occurrence_id, claim_token):
                    return HandoffResult(
                        success=False,
                        occurrence_id=occurrence_id,
                        error="plan not configured",
                    )
            return FakeHandoff()

        async with factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            job = _make_job(next_run_at=now, target_type="task", target_id="task_abc123")
            await repo.create_job(job)
            await session.commit()

        worker = SchedulerWorker(
            session_factory=factory,
            handoff_factory=mock_handoff,
            poll_interval=0.1,
        )
        await worker.start()
        try:
            await worker._poll_once()
        finally:
            await worker.stop()
        assert len(handoff_calls) == 1

    async def test_poll_once_skips_running_target(self, factory_and_engine):
        factory, engine = factory_and_engine
        now = _utc_now()

        def mock_handoff(repo):
            class FakeHandoff:
                async def handoff_task(self, occurrence_id, claim_token):
                    return HandoffResult(
                        success=False,
                        occurrence_id=occurrence_id,
                        error="Task is already in status 'RUNNING'",
                    )
                async def handoff_plan(self, occurrence_id, claim_token):
                    return HandoffResult(
                        success=False,
                        occurrence_id=occurrence_id,
                        error="plan not configured",
                    )
            return FakeHandoff()

        async with factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            job = _make_job(next_run_at=now)
            await repo.create_job(job)
            await session.commit()

        worker = SchedulerWorker(
            session_factory=factory,
            handoff_factory=mock_handoff,
            poll_interval=0.1,
        )
        await worker.start()
        try:
            await worker._poll_once()
        finally:
            await worker.stop()

        async with factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            fetched = await repo.get_job(job.job_id)
            assert fetched.status == SchedulerJobStatus.SKIPPED

    async def test_poll_once_fails_missing_target(self, factory_and_engine):
        factory, engine = factory_and_engine
        now = _utc_now()

        def mock_handoff(repo):
            class FakeHandoff:
                async def handoff_task(self, occurrence_id, claim_token):
                    return HandoffResult(
                        success=False,
                        occurrence_id=occurrence_id,
                        error="Target task not found",
                    )
                async def handoff_plan(self, occurrence_id, claim_token):
                    return HandoffResult(
                        success=False,
                        occurrence_id=occurrence_id,
                        error="plan not configured",
                    )
            return FakeHandoff()

        async with factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            job = _make_job(
                target_type="task",
                target_id="nonexistent_task",
                next_run_at=now,
            )
            await repo.create_job(job)
            await session.commit()

        worker = SchedulerWorker(
            session_factory=factory,
            handoff_factory=mock_handoff,
            poll_interval=0.1,
        )
        await worker.start()
        try:
            await worker._poll_once()
        finally:
            await worker.stop()

        async with factory() as session:
            repo = SchedulerRepository(session, event_emitter=AsyncMock())
            fetched = await repo.get_job(job.job_id)
            assert fetched.status == SchedulerJobStatus.FAILED
