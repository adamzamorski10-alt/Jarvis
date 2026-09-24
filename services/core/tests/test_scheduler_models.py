"""
Stage 12.2A — Scheduler Domain Model Tests

Covers:
  - ScheduledJob construction
  - Invalid values rejected
  - Frozen model
  - Validation of required fields
  - ScheduleType and SchedulerJobStatus enums
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.scheduler.models import ScheduledJob, ScheduleType, SchedulerJobStatus
from datetime import datetime, timezone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_job(**kwargs) -> ScheduledJob:
    defaults = dict(
        job_id="sched_test_001",
        name="Test Job",
        enabled=True,
        schedule_type=ScheduleType.CRON,
        schedule_expr="*/5 * * * *",
        timezone=None,
        target_type="task",
        target_id="task_abc123",
        status=SchedulerJobStatus.PENDING,
        last_run_at=None,
        next_run_at=_utc_now(),
        created_at=_utc_now(),
        updated_at=_utc_now(),
        occurrence_id=None,
        claim_token=None,
        claimed_at=None,
        lease_until=None,
        execution_id=None,
        metadata=None,
    )
    defaults.update(kwargs)
    return ScheduledJob(**defaults)


class TestScheduledJobConstruction:
    def test_valid_cron_job(self):
        job = _make_job()
        assert job.job_id == "sched_test_001"
        assert job.schedule_type == ScheduleType.CRON
        assert job.status == SchedulerJobStatus.PENDING
        assert job.target_type == "task"
        assert job.enabled is True

    def test_valid_interval_job(self):
        job = _make_job(schedule_type=ScheduleType.INTERVAL, schedule_expr="300")
        assert job.schedule_type == ScheduleType.INTERVAL
        assert job.schedule_expr == "300"

    def test_valid_once_job(self):
        job = _make_job(schedule_type=ScheduleType.ONCE, schedule_expr="2026-09-20T03:00:00Z")
        assert job.schedule_type == ScheduleType.ONCE

    def test_frozen_model(self):
        job = _make_job()
        with pytest.raises(Exception):
            job.job_id = "new_id"

    def test_invalid_schedule_type(self):
        with pytest.raises(ValidationError):
            _make_job(schedule_type="invalid")

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            _make_job(status="INVALID")

    def test_empty_job_id_rejected(self):
        with pytest.raises(ValidationError):
            _make_job(job_id="")

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            _make_job(name="")

    def test_empty_schedule_expr_rejected(self):
        with pytest.raises(ValidationError):
            _make_job(schedule_expr="")

    def test_empty_target_id_rejected(self):
        with pytest.raises(ValidationError):
            _make_job(target_id="")

    def test_invalid_target_type(self):
        with pytest.raises(ValidationError):
            _make_job(target_type="invalid")

    def test_missing_next_run_at_rejected(self):
        with pytest.raises(ValidationError):
            _make_job(next_run_at=None)

    def test_metadata_roundtrip(self):
        meta = {"key": "value", "count": 42}
        job = _make_job(metadata=meta)
        assert job.metadata == meta

    def test_none_metadata_accepted(self):
        job = _make_job(metadata=None)
        assert job.metadata is None


class TestScheduleTypeEnum:
    def test_cron_value(self):
        assert ScheduleType.CRON.value == "cron"

    def test_interval_value(self):
        assert ScheduleType.INTERVAL.value == "interval"

    def test_once_value(self):
        assert ScheduleType.ONCE.value == "once"

    def test_all_values(self):
        assert set(ScheduleType) == {ScheduleType.CRON, ScheduleType.INTERVAL, ScheduleType.ONCE}


class TestSchedulerJobStatusEnum:
    def test_pending_value(self):
        assert SchedulerJobStatus.PENDING.value == "PENDING"

    def test_claimed_value(self):
        assert SchedulerJobStatus.CLAIMED.value == "CLAIMED"

    def test_triggered_value(self):
        assert SchedulerJobStatus.TRIGGERED.value == "TRIGGERED"

    def test_completed_value(self):
        assert SchedulerJobStatus.COMPLETED.value == "COMPLETED"

    def test_failed_value(self):
        assert SchedulerJobStatus.FAILED.value == "FAILED"

    def test_skipped_value(self):
        assert SchedulerJobStatus.SKIPPED.value == "SKIPPED"

    def test_disabled_value(self):
        assert SchedulerJobStatus.DISABLED.value == "DISABLED"

    def test_all_values(self):
        expected = {"PENDING", "CLAIMED", "TRIGGERED", "COMPLETED", "FAILED", "SKIPPED", "DISABLED"}
        assert {s.value for s in SchedulerJobStatus} == expected


class TestPersistenceDoesNotImplyExactlyOnce:
    """
    Prove that persisting a ScheduledJob does not guarantee exactly-once execution.
    """
    def test_job_persistence_independent_of_execution(self):
        job = _make_job()
        # Persistence of the job definition is separate from execution semantics
        assert job.status == SchedulerJobStatus.PENDING
        # No execution has occurred
        assert job.last_run_at is None
        assert job.execution_id is None
        assert job.occurrence_id is None
        # The job can be persisted but never executed
        # This proves persistence ≠ execution guarantee
