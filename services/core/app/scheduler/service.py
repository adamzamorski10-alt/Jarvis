"""
SchedulerService — application-owned business boundary for scheduler control-plane operations.

This service owns:
- schedule validation and next_run_at calculation
- target existence checks (task targets only; plan targets defer to execution layer)
- create idempotency
- status-dependent PATCH restrictions
- enable/disable semantics
- safe deletion

This service does NOT:
- execute tasks or plans
- bypass PermissionEngine, ApprovalService, or VerificationGate
- mutate scheduler worker semantics
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.scheduler.calculator import (
    ScheduleCalculationError,
    ScheduleDefinition,
    calculate_next_occurrence,
    validate_schedule_definition,
)
from app.scheduler.models import ScheduledJob, ScheduleType, SchedulerJobStatus
from app.scheduler.repository import SchedulerRepository

logger = logging.getLogger(__name__)

_AUTHORITATIVE_CREATE_FIELDS = (
    "name",
    "schedule_type",
    "schedule_expr",
    "timezone",
    "target_type",
    "target_id",
    "enabled",
)

_IMMUTABLE_OCCURRENCE_FIELDS = frozenset(
    {
        "job_id",
        "occurrence_id",
        "claim_token",
        "claim_sequence",
        "claimed_at",
        "lease_until",
        "execution_id",
        "created_at",
    }
)

_MUTABLE_FIELDS = frozenset(
    {
        "name",
        "enabled",
        "schedule_type",
        "schedule_expr",
        "timezone",
        "target_type",
        "target_id",
        "status",
        "last_run_at",
        "next_run_at",
        "job_metadata",
    }
)


class JobIdempotentReturn(Exception):
    """Raised when create_job finds an existing job with an identical definition."""

    def __init__(self, job: ScheduledJob) -> None:
        self.job = job
        super().__init__(f"Job already exists with identical definition")


class SchedulerService:
    """Application service for scheduler control-plane operations."""

    def __init__(
        self,
        repo: SchedulerRepository,
        task_repo: Any | None = None,
    ) -> None:
        self._repo = repo
        self._task_repo = task_repo

    async def create_job(
        self,
        job_id: str,
        name: str,
        schedule_type: str,
        schedule_expr: str,
        timezone: str | None,
        target_type: str,
        target_id: str,
        enabled: bool,
        now: datetime | None = None,
    ) -> ScheduledJob:
        """Create a new scheduled job.

        Raises:
            ValueError: if schedule is invalid, target missing, ONCE elapsed, or idempotency conflict.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        schedule_type_enum = ScheduleType(schedule_type.strip().lower())

        definition = ScheduleDefinition(
            schedule_type=schedule_type_enum,
            schedule_expr=schedule_expr.strip(),
            timezone=timezone.strip() if timezone else None,
        )
        validate_schedule_definition(definition)

        if target_type == "task" and self._task_repo is not None:
            task = await self._task_repo.get_task(target_id.strip())
            if task is None:
                raise ValueError(f"Target task '{target_id}' not found")

        next_run_at = calculate_next_occurrence(definition, now)
        if next_run_at is None:
            raise ValueError(
                "ONCE schedule target time is in the past; cannot create a repeatedly runnable job"
            )

        existing = await self._repo.get_job(job_id.strip())
        if existing is not None:
            if not self._is_semantically_equal(existing, {
                "name": name.strip(),
                "schedule_type": schedule_type_enum.value,
                "schedule_expr": schedule_expr.strip(),
                "timezone": timezone.strip() if timezone else None,
                "target_type": target_type.strip(),
                "target_id": target_id.strip(),
                "enabled": enabled,
            }):
                raise ValueError(
                    "Job exists with different definition"
                )
            raise JobIdempotentReturn(existing)

        job = ScheduledJob(
            job_id=job_id.strip(),
            name=name.strip(),
            enabled=enabled,
            schedule_type=schedule_type_enum,
            schedule_expr=schedule_expr.strip(),
            timezone=timezone.strip() if timezone else None,
            target_type=target_type.strip(),
            target_id=target_id.strip(),
            status=SchedulerJobStatus.PENDING,
            last_run_at=None,
            next_run_at=next_run_at,
            occurrence_id=None,
            claim_token=None,
            claim_sequence=0,
            claimed_at=None,
            lease_until=None,
            execution_id=None,
            metadata=None,
        )
        return await self._repo.create_job(job)

    async def update_job(
        self,
        job_id: str,
        updates: dict[str, Any],
        now: datetime | None = None,
    ) -> ScheduledJob:
        """Update a scheduled job.

        Raises:
            ValueError: if job not found, immutable field mutated, or status-dependent restriction violated.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        existing = await self._repo.get_job(job_id.strip())
        if existing is None:
            raise ValueError(f"Job '{job_id}' not found")

        immutable_mutations = _IMMUTABLE_OCCURRENCE_FIELDS & updates.keys()
        if immutable_mutations:
            raise ValueError(
                f"Fields cannot be modified: {', '.join(sorted(immutable_mutations))}"
            )

        status = existing.status
        if status == SchedulerJobStatus.CLAIMED:
            schedule_fields = {"schedule_type", "schedule_expr", "timezone"}
            if schedule_fields & updates.keys():
                raise ValueError(
                    "Cannot modify schedule while occurrence is CLAIMED"
                )
            if "target_type" in updates or "target_id" in updates:
                raise ValueError(
                    "Cannot modify target while occurrence is CLAIMED"
                )
            if "name" in updates:
                raise ValueError(
                    "Cannot modify name while occurrence is CLAIMED"
                )

        clean_updates: dict[str, Any] = {}
        for key, value in updates.items():
            if key not in _MUTABLE_FIELDS:
                raise ValueError(f"Field '{key}' is not allowed in job updates")
            if key == "schedule_type" and isinstance(value, str):
                value = ScheduleType(value.strip().lower())
            clean_updates[key] = value

        if "schedule_type" in clean_updates or "schedule_expr" in clean_updates or "timezone" in clean_updates:
            schedule_type = clean_updates.get("schedule_type", existing.schedule_type)
            schedule_expr = clean_updates.get("schedule_expr", existing.schedule_expr)
            timezone_val = clean_updates.get("timezone", existing.timezone)
            if timezone_val is not None and timezone_val == "":
                timezone_val = None

            definition = ScheduleDefinition(
                schedule_type=schedule_type if isinstance(schedule_type, ScheduleType) else ScheduleType(schedule_type.value if hasattr(schedule_type, "value") else str(schedule_type)),
                schedule_expr=schedule_expr,
                timezone=timezone_val,
            )
            reference = existing.last_run_at if existing.last_run_at is not None else now
            next_run = calculate_next_occurrence(definition, reference)
            if next_run is None:
                clean_updates["enabled"] = False
                clean_updates["status"] = SchedulerJobStatus.DISABLED.value
                clean_updates["next_run_at"] = reference
            else:
                clean_updates["next_run_at"] = next_run

        if "enabled" in clean_updates:
            clean_updates["enabled"] = 1 if clean_updates["enabled"] else 0
        if "status" in clean_updates and isinstance(clean_updates["status"], SchedulerJobStatus):
            clean_updates["status"] = clean_updates["status"].value
        if "schedule_type" in clean_updates and isinstance(clean_updates["schedule_type"], ScheduleType):
            clean_updates["schedule_type"] = clean_updates["schedule_type"].value

        result = await self._repo.update_job(job_id.strip(), **clean_updates)
        if result is None:
            raise ValueError(f"Job '{job_id}' not found")
        return result

    async def delete_job(self, job_id: str) -> None:
        """Delete a scheduled job.

        Raises:
            ValueError: if job not found or is in an active state.
        """
        existing = await self._repo.get_job(job_id.strip())
        if existing is None:
            raise ValueError(f"Job '{job_id}' not found")

        if existing.status in (SchedulerJobStatus.CLAIMED, SchedulerJobStatus.TRIGGERED):
            if existing.status == SchedulerJobStatus.CLAIMED:
                raise ValueError("Cannot delete job with active claim")
            raise ValueError("Cannot delete job with execution in progress")

        await self._repo.delete_job(job_id.strip())

    async def enable_job(self, job_id: str) -> ScheduledJob:
        """Enable a scheduled job.

        Only toggles the enabled flag. Does not change status or recalculate schedule.
        """
        existing = await self._repo.get_job(job_id.strip())
        if existing is None:
            raise ValueError(f"Job '{job_id}' not found")

        if existing.enabled:
            return existing

        result = await self._repo.update_job(job_id.strip(), enabled=True)
        if result is None:
            raise ValueError(f"Job '{job_id}' not found")
        return result

    async def disable_job(self, job_id: str) -> ScheduledJob:
        """Disable a scheduled job.

        Only toggles the enabled flag. Does not change status or recalculate schedule.
        """
        existing = await self._repo.get_job(job_id.strip())
        if existing is None:
            raise ValueError(f"Job '{job_id}' not found")

        if not existing.enabled:
            return existing

        result = await self._repo.update_job(job_id.strip(), enabled=False)
        if result is None:
            raise ValueError(f"Job '{job_id}' not found")
        return result

    async def get_job(self, job_id: str) -> ScheduledJob | None:
        """Get a single scheduled job."""
        return await self._repo.get_job(job_id.strip())

    async def list_jobs(
        self,
        status: str | None = None,
        enabled: bool | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ScheduledJob], int]:
        """List scheduled jobs with optional filters."""
        jobs = await self._repo.list_jobs(
            status=status,
            enabled=enabled,
            target_type=target_type,
            target_id=target_id,
            limit=limit,
            offset=offset,
        )
        total = await self._repo.count_jobs(
            status=status,
            enabled=enabled,
            target_type=target_type,
            target_id=target_id,
        )
        return jobs, total

    @staticmethod
    def _is_semantically_equal(existing: ScheduledJob, requested: dict[str, Any]) -> bool:
        """Compare authoritative create fields for idempotency."""
        return all(
            getattr(existing, field) == requested[field]
            for field in _AUTHORITATIVE_CREATE_FIELDS
        )
