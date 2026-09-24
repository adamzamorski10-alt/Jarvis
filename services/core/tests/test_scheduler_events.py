"""
Stage 12.2E — Scheduler Lifecycle Events Tests

Covers:
  - EventType enum values
  - Deterministic event ID generation
  - 128-bit hash length verification
  - Claim sequence atomicity
  - Occurrence preservation across recovery
  - Event payload contracts
  - Correlation/causation
  - Transaction/savepoint behavior
  - Duplicate and conflicting event behavior
  - Concurrency guarantees
  - Security: no claim_token in events
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base, init_db
from app.db.models.scheduled_job import ScheduledJobModel
from app.events.schemas import EventType, Sensitivity
from app.scheduler.lease import generate_occurrence_id, generate_claim_token, now_utc, DEFAULT_LEASE_DURATION
from app.scheduler.models import ScheduledJob, ScheduleType, SchedulerJobStatus
from app.scheduler.repository import SchedulerRepository, make_scheduler_event_id
from app.events.emitter import EventEmitter, EventEmissionError
from app.events.repository import EventRepository, EventIntegrityConflict


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
        "claim_sequence": 0,
        "claimed_at": None,
        "lease_until": None,
        "execution_id": None,
        "metadata": None,
    }
    defaults.update(kwargs)
    return ScheduledJob(**defaults)


# ── EventType Tests ─────────────────────────────────────────────────────────


class TestSchedulerEventTypes:
    def test_scheduler_event_types_exist(self):
        assert EventType.SCHEDULER_OCCURRENCE_CLAIMED.value == "scheduler.occurrence.claimed"
        assert EventType.SCHEDULER_OCCURRENCE_TRIGGERED.value == "scheduler.occurrence.triggered"
        assert EventType.SCHEDULER_OCCURRENCE_FAILED.value == "scheduler.occurrence.failed"
        assert EventType.SCHEDULER_OCCURRENCE_RECOVERED.value == "scheduler.occurrence.recovered"

    def test_scheduler_event_types_serialize(self):
        assert EventType("scheduler.occurrence.claimed") == EventType.SCHEDULER_OCCURRENCE_CLAIMED
        assert EventType("scheduler.occurrence.triggered") == EventType.SCHEDULER_OCCURRENCE_TRIGGERED
        assert EventType("scheduler.occurrence.failed") == EventType.SCHEDULER_OCCURRENCE_FAILED
        assert EventType("scheduler.occurrence.recovered") == EventType.SCHEDULER_OCCURRENCE_RECOVERED


# ── Deterministic Event ID Tests ──────────────────────────────────────────


class TestDeterministicEventId:
    def test_event_id_length(self):
        event_id = make_scheduler_event_id("occurrence_abc", 1, "scheduler.occurrence.claimed")
        assert len(event_id) == 42  # "sched_evt_" (10) + 32 hex chars = 42
        assert len(event_id) <= 64  # must fit in String(64)

    def test_event_id_prefix(self):
        event_id = make_scheduler_event_id("occurrence_abc", 1, "scheduler.occurrence.claimed")
        assert event_id.startswith("sched_evt_")

    def test_event_id_deterministic(self):
        e1 = make_scheduler_event_id("occurrence_abc", 1, "scheduler.occurrence.claimed")
        e2 = make_scheduler_event_id("occurrence_abc", 1, "scheduler.occurrence.claimed")
        assert e1 == e2

    def test_event_id_changes_with_claim_sequence(self):
        e1 = make_scheduler_event_id("occurrence_abc", 1, "scheduler.occurrence.claimed")
        e2 = make_scheduler_event_id("occurrence_abc", 2, "scheduler.occurrence.claimed")
        assert e1 != e2

    def test_event_id_changes_with_event_type(self):
        e1 = make_scheduler_event_id("occurrence_abc", 1, "scheduler.occurrence.claimed")
        e2 = make_scheduler_event_id("occurrence_abc", 1, "scheduler.occurrence.triggered")
        assert e1 != e2

    def test_event_id_changes_with_occurrence_id(self):
        e1 = make_scheduler_event_id("occurrence_abc", 1, "scheduler.occurrence.claimed")
        e2 = make_scheduler_event_id("occurrence_def", 1, "scheduler.occurrence.claimed")
        assert e1 != e2

    def test_event_id_hash_bits(self):
        event_id = make_scheduler_event_id("occurrence_abc", 1, "scheduler.occurrence.claimed")
        hex_part = event_id[len("sched_evt_"):]
        assert len(hex_part) == 32  # 128 bits = 32 hex chars

    def test_event_id_no_secrets(self):
        token = "super_secret_token_12345"
        event_id = make_scheduler_event_id("occurrence_abc", 1, token)
        assert token not in event_id


# ── Claim Sequence Tests ──────────────────────────────────────────────────


class TestClaimSequence:
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

    async def test_claim_sequence_increments(self, repo_and_factory):
        repo, factory = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await repo._session.commit()

        now = _utc_now()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION)
        assert claimed is not None
        assert claimed.claim_sequence == 1

    async def test_claim_sequence_persists(self, repo_and_factory):
        repo, factory = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await repo._session.commit()

        now = _utc_now()
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION)
        await repo._session.commit()
        assert claimed is not None
        assert claimed.claim_sequence == 1

        fetched = await repo.get_job(job.job_id)
        assert fetched is not None
        assert fetched.claim_sequence == 1

    async def test_claim_sequence_not_incremented_on_failed_claim(self, repo_and_factory):
        repo, factory = repo_and_factory
        job = _make_job(status=SchedulerJobStatus.CLAIMED)
        await repo.create_job(job)
        await repo._session.commit()

        now = _utc_now()
        result = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION)
        assert result is None  # already claimed

        fetched = await repo.get_job(job.job_id)
        assert fetched.claim_sequence == 0


# ── Event Emission Tests ──────────────────────────────────────────────────


class TestEventEmission:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        event_repo = EventRepository(session)
        emitter = EventEmitter(event_repo)
        repo = SchedulerRepository(session, event_emitter=emitter)
        try:
            yield repo, factory, session, event_repo
        finally:
            await session.close()
            await engine.dispose()

    async def test_claim_emits_event(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed is not None
        await session.commit()

        event_id = make_scheduler_event_id(occurrence_id, claimed.claim_sequence, EventType.SCHEDULER_OCCURRENCE_CLAIMED.value)
        fetched_event = await event_repo.get(event_id)
        assert fetched_event is not None
        assert fetched_event.event_type == EventType.SCHEDULER_OCCURRENCE_CLAIMED
        assert fetched_event.payload["job_id"] == job.job_id
        assert fetched_event.payload["occurrence_id"] == occurrence_id
        assert fetched_event.correlation_id == job.job_id

    async def test_triggered_emits_event(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed is not None
        await session.commit()

        triggered = await repo.finalize_triggered(occurrence_id, claimed.claim_token, "exec_123", now)
        assert triggered is not None
        await session.commit()

        claimed_event_id = make_scheduler_event_id(occurrence_id, claimed.claim_sequence, EventType.SCHEDULER_OCCURRENCE_CLAIMED.value)
        triggered_event_id = make_scheduler_event_id(occurrence_id, claimed.claim_sequence, EventType.SCHEDULER_OCCURRENCE_TRIGGERED.value)

        claimed_event = await event_repo.get(claimed_event_id)
        triggered_event = await event_repo.get(triggered_event_id)
        assert claimed_event is not None
        assert triggered_event is not None
        assert triggered_event.causation_id == claimed_event_id
        assert triggered_event.payload["execution_id"] == "exec_123"

    async def test_failed_emits_event(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed is not None
        await session.commit()

        result = await repo.finalize_failed(occurrence_id, claimed.claim_token, now, "execution_error", "boom")
        assert result is True
        await session.commit()

        event_id = make_scheduler_event_id(occurrence_id, claimed.claim_sequence, EventType.SCHEDULER_OCCURRENCE_FAILED.value)
        fetched_event = await event_repo.get(event_id)
        assert fetched_event is not None
        assert fetched_event.payload["reason_code"] == "execution_error"
        assert fetched_event.payload["reason_detail"] == "boom"

    async def test_recovered_emits_event(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed is not None
        await session.commit()

        after_expiry = now + DEFAULT_LEASE_DURATION + timedelta(seconds=1)
        reset = await repo.reset_expired_claim_to_pending(occurrence_id, after_expiry, after_expiry)
        assert reset is not None
        await session.commit()

        event_id = make_scheduler_event_id(occurrence_id, claimed.claim_sequence, EventType.SCHEDULER_OCCURRENCE_RECOVERED.value)
        fetched_event = await event_repo.get(event_id)
        assert fetched_event is not None
        assert fetched_event.payload["reason"] == "lease_expired"


# ── Transaction / Savepoint Tests ─────────────────────────────────────────


class TestTransactionSavepoint:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        event_repo = EventRepository(session)
        emitter = EventEmitter(event_repo)
        repo = SchedulerRepository(session, event_emitter=emitter)
        try:
            yield repo, factory, session, event_repo
        finally:
            await session.close()
            await engine.dispose()

    async def test_duplicate_event_does_not_rollback_scheduler_state(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed is not None
        await session.commit()

        event_id = make_scheduler_event_id(occurrence_id, claimed.claim_sequence, EventType.SCHEDULER_OCCURRENCE_CLAIMED.value)
        original_event = await event_repo.get(event_id)
        assert original_event is not None

        # Re-emit the exact same event in the same session
        duplicate = await event_repo.record(original_event)
        assert duplicate.event_id == event_id

        # Verify job state is still intact
        fetched = await repo.get_job(job.job_id)
        assert fetched.status == SchedulerJobStatus.CLAIMED

        # Verify only one event exists
        events = await event_repo.list(correlation_id=job.job_id)
        assert len(events) == 1

    async def test_duplicate_event_shared_session_savepoint(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed is not None
        await session.commit()

        event_id = make_scheduler_event_id(occurrence_id, claimed.claim_sequence, EventType.SCHEDULER_OCCURRENCE_CLAIMED.value)
        original_event = await event_repo.get(event_id)
        assert original_event is not None

        # Re-record the exact same event in the same session
        duplicate = await event_repo.record(original_event)
        assert duplicate.event_id == event_id

        events = await event_repo.list(correlation_id=job.job_id)
        assert len(events) == 1

        # Verify job state is still intact
        fetched = await repo.get_job(job.job_id)
        assert fetched.status == SchedulerJobStatus.CLAIMED

    async def test_conflicting_event_raises_without_corrupting_outer(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed is not None
        await session.commit()

        # Insert conflicting event with same event_id but different payload
        event_id = make_scheduler_event_id(occurrence_id, claimed.claim_sequence, EventType.SCHEDULER_OCCURRENCE_CLAIMED.value)
        original = await event_repo.get(event_id)
        assert original is not None

        # Create a copy with different payload
        from copy import deepcopy
        from app.events.schemas import Event
        conflicting_dict = original.model_dump()
        conflicting_dict["payload"] = {"job_id": "different"}
        conflicting_event = Event(**conflicting_dict)

        with pytest.raises(EventIntegrityConflict):
            await event_repo.record(conflicting_event)

        # Outer scheduler transaction should still be usable
        fetched = await repo.get_job(job.job_id)
        assert fetched.status == SchedulerJobStatus.CLAIMED


# ── Hostile Transaction Tests ─────────────────────────────────────────────


class TestHostileTransaction:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        event_repo = EventRepository(session)
        emitter = EventEmitter(event_repo)
        repo = SchedulerRepository(session, event_emitter=emitter)
        try:
            yield repo, factory, session, event_repo
        finally:
            await session.close()
            await engine.dispose()

    async def test_event_emission_failure_prevents_scheduler_state_commit(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)

        # Replace event repo with one that raises on record
        failing_repo = EventRepository(session)
        original_record = failing_repo.record

        async def failing_record(event):
            if event.event_type == EventType.SCHEDULER_OCCURRENCE_CLAIMED:
                raise RuntimeError("simulated event emission failure")
            return await original_record(event)

        failing_repo.record = failing_record
        failing_emitter = EventEmitter(failing_repo)
        repo2 = SchedulerRepository(session, event_emitter=failing_emitter)

        with pytest.raises(EventEmissionError, match="simulated event emission failure"):
            await repo2.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)

        # Attempting to commit should fail because the session is in a failed state
        # Or, if we rollback, the job should still be PENDING (not CLAIMED)
        await session.rollback()
        fetched = await repo.get_job(job.job_id)
        assert fetched.status == SchedulerJobStatus.PENDING

    async def test_event_emission_failure_does_not_corrupt_session(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)

        # Replace event repo with one that raises on record
        failing_repo = EventRepository(session)
        original_record = failing_repo.record

        async def failing_record(event):
            if event.event_type == EventType.SCHEDULER_OCCURRENCE_CLAIMED:
                raise RuntimeError("simulated event emission failure")
            return await original_record(event)

        failing_repo.record = failing_record
        failing_emitter = EventEmitter(failing_repo)
        repo2 = SchedulerRepository(session, event_emitter=failing_emitter)

        with pytest.raises(EventEmissionError, match="simulated event emission failure"):
            await repo2.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)

        # Session should still be usable for other operations
        await session.rollback()
        fetched = await repo.get_job(job.job_id)
        assert fetched.status == SchedulerJobStatus.PENDING


# ── Database Migration Tests ──────────────────────────────────────────────


class TestDatabaseMigration:
    @pytest.fixture
    async def legacy_db(self, tmp_path):
        """Create a legacy database without claim_sequence column."""
        import sqlite3
        db_file = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db_file)
        conn.execute("""
            CREATE TABLE scheduled_jobs (
                job_id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                schedule_type VARCHAR(20) NOT NULL,
                schedule_expr VARCHAR(200) NOT NULL,
                timezone VARCHAR(64),
                target_type VARCHAR(20) NOT NULL,
                target_id VARCHAR(64) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                last_run_at TIMESTAMP WITH TIME ZONE,
                next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                occurrence_id VARCHAR(128),
                claim_token VARCHAR(64),
                claimed_at TIMESTAMP WITH TIME ZONE,
                lease_until TIMESTAMP WITH TIME ZONE,
                execution_id VARCHAR(64),
                job_metadata TEXT
            )
        """)
        conn.execute("""
            INSERT INTO scheduled_jobs (job_id, name, enabled, schedule_type, schedule_expr, target_type, target_id, status, next_run_at, created_at, updated_at)
            VALUES ('legacy_job', 'Legacy', 1, 'cron', '*/5 * * * *', 'task', 'task_1', 'PENDING', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """)
        conn.commit()
        conn.close()
        return db_file

    async def test_legacy_db_receives_claim_sequence_column(self, legacy_db):
        from app.db.database import init_db
        import app.db.database as db_mod
        from sqlalchemy.ext.asyncio import create_async_engine

        url = f"sqlite+aiosqlite:///{legacy_db}"
        engine = create_async_engine(url, connect_args={"check_same_thread": False})
        old_engine = db_mod._engine
        db_mod._engine = engine
        try:
            await init_db()
        finally:
            db_mod._engine = old_engine
            await engine.dispose()

        # Verify claim_sequence column exists and has default value
        import sqlite3
        conn = sqlite3.connect(legacy_db)
        cursor = conn.execute("PRAGMA table_info(scheduled_jobs)")
        columns = [row[1] for row in cursor.fetchall()]
        assert "claim_sequence" in columns

        cursor = conn.execute("SELECT claim_sequence FROM scheduled_jobs WHERE job_id = 'legacy_job'")
        row = cursor.fetchone()
        assert row[0] == 0
        conn.close()


# ── Repeated Claim Tests ──────────────────────────────────────────────────


class TestRepeatedClaim:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        event_repo = EventRepository(session)
        emitter = EventEmitter(event_repo)
        repo = SchedulerRepository(session, event_emitter=emitter)
        try:
            yield repo, factory, session, event_repo
        finally:
            await session.close()
            await engine.dispose()

    async def test_repeated_claim_different_event_ids(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)

        # First claim
        claimed1 = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed1 is not None
        assert claimed1.claim_sequence == 1
        await session.commit()

        event_id_1 = make_scheduler_event_id(occurrence_id, 1, EventType.SCHEDULER_OCCURRENCE_CLAIMED.value)
        fetched_1 = await event_repo.get(event_id_1)
        assert fetched_1 is not None

        # Recover
        after_expiry = now + DEFAULT_LEASE_DURATION + timedelta(seconds=1)
        reset = await repo.reset_expired_claim_to_pending(occurrence_id, after_expiry, after_expiry)
        assert reset is not None
        assert reset.occurrence_id == occurrence_id
        await session.commit()

        # Second claim
        claimed2 = await repo.claim_next_occurrence(job.job_id, after_expiry, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed2 is not None
        assert claimed2.claim_sequence == 2
        await session.commit()

        event_id_2 = make_scheduler_event_id(occurrence_id, 2, EventType.SCHEDULER_OCCURRENCE_CLAIMED.value)
        fetched_2 = await event_repo.get(event_id_2)
        assert fetched_2 is not None

        assert event_id_1 != event_id_2

        events = await event_repo.list(correlation_id=job.job_id)
        claimed_events = [e for e in events if e.event_type == EventType.SCHEDULER_OCCURRENCE_CLAIMED]
        assert len(claimed_events) == 2


# ── Security Tests ────────────────────────────────────────────────────────


class TestSecurity:
    @pytest.fixture
    async def repo_and_factory(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        factory, engine = await _init_db(db_file)
        session = factory()
        event_repo = EventRepository(session)
        emitter = EventEmitter(event_repo)
        repo = SchedulerRepository(session, event_emitter=emitter)
        try:
            yield repo, factory, session, event_repo
        finally:
            await session.close()
            await engine.dispose()

    async def test_claim_token_not_in_event_payload(self, repo_and_factory):
        repo, factory, session, event_repo = repo_and_factory
        job = _make_job()
        await repo.create_job(job)
        await session.commit()

        now = _utc_now()
        occurrence_id = generate_occurrence_id(job.job_id, now)
        claimed = await repo.claim_next_occurrence(job.job_id, now, DEFAULT_LEASE_DURATION, occurrence_id=occurrence_id)
        assert claimed is not None
        await session.commit()

        event_id = make_scheduler_event_id(occurrence_id, claimed.claim_sequence, EventType.SCHEDULER_OCCURRENCE_CLAIMED.value)
        fetched_event = await event_repo.get(event_id)
        assert fetched_event is not None
        payload_str = str(fetched_event.payload)
        assert claimed.claim_token not in payload_str

    async def test_event_id_contains_no_secrets(self, repo_and_factory):
        token = "super_secret_token_12345"
        event_id = make_scheduler_event_id("occurrence_abc", 1, token)
        assert token not in event_id


# ── Helper for event creation ─────────────────────────────────────────────


def _make_event(**kwargs: Any) -> "Event":
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
    from app.events.schemas import Event
    return Event(**defaults)
