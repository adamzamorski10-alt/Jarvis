"""
Repository — Scheduled Jobs

Data access layer for scheduler job definitions in SQLite.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.scheduled_job import ScheduledJobModel
from app.scheduler.lease import generate_occurrence_id, generate_claim_token, now_utc, DEFAULT_LEASE_DURATION
from app.scheduler.models import ScheduledJob, ScheduleType, SchedulerJobStatus
from app.scheduler.calculator import ScheduleDefinition, calculate_next_occurrence
from app.events.schemas import EventType, Sensitivity

def _serialize_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    return json.dumps(metadata, sort_keys=True, ensure_ascii=False)


def _deserialize_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def make_scheduler_event_id(occurrence_id: str, claim_sequence: int, event_type: str) -> str:
    message = f"{occurrence_id}\x00{claim_sequence}\x00{event_type}"
    digest = hashlib.sha256(message.encode()).hexdigest()[:32]
    return f"sched_evt_{digest}"


async def _retry_on_locked(fn, session, *args, _attempts=3, _delay=0.05, **kwargs):
    """
    Retry an async repository call when SQLite reports database is locked.

    Rolls back the session on lock errors so the session can be reused.
    """
    last_exc = None
    for attempt in range(_attempts):
        try:
            return await fn(*args, **kwargs)
        except OperationalError as exc:
            msg = str(exc).lower()
            if "database is locked" in msg or "no such table" in msg:
                last_exc = exc
                try:
                    await session.rollback()
                except Exception:
                    pass
                await asyncio.sleep(_delay * (attempt + 1))
                continue
            raise
    raise last_exc


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    """Convert a datetime to naive UTC for SQLite storage."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _to_aware_utc(dt: datetime | None) -> datetime | None:
    """Convert a naive UTC datetime from SQLite to timezone-aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SchedulerRepository:
    """
    Data access layer for ScheduledJob records.
    """
    def __init__(self, session: AsyncSession, event_emitter: Any) -> None:
        if event_emitter is None:
            raise ValueError("event_emitter is required for SchedulerRepository")
        self._session = session
        self._event_emitter = event_emitter

    async def _emit_scheduler_event(
        self,
        *,
        event_type: EventType,
        job: ScheduledJob,
        occurred_at: datetime | None = None,
        payload: dict[str, Any] | None = None,
        causation_id: str | None = None,
    ) -> None:
        event_id = make_scheduler_event_id(
            occurrence_id=job.occurrence_id or "",
            claim_sequence=job.claim_sequence,
            event_type=event_type.value,
        )
        await self._event_emitter.emit(
            event_type=event_type,
            source="scheduler",
            correlation_id=job.job_id,
            causation_id=causation_id,
            payload=payload,
            occurred_at=occurred_at or now_utc(),
            event_id=event_id,
        )

    def _to_domain(self, model: ScheduledJobModel) -> ScheduledJob:
        return ScheduledJob(
            job_id=model.job_id,
            name=model.name,
            enabled=bool(model.enabled),
            schedule_type=ScheduleType(model.schedule_type),
            schedule_expr=model.schedule_expr,
            timezone=model.timezone,
            target_type=model.target_type,
            target_id=model.target_id,
            status=SchedulerJobStatus(model.status),
            last_run_at=_to_aware_utc(model.last_run_at),
            next_run_at=_to_aware_utc(model.next_run_at) or model.next_run_at,
            created_at=_to_aware_utc(model.created_at),
            updated_at=_to_aware_utc(model.updated_at),
            occurrence_id=model.occurrence_id,
            claim_token=model.claim_token,
            claim_sequence=model.claim_sequence,
            claimed_at=_to_aware_utc(model.claimed_at),
            lease_until=_to_aware_utc(model.lease_until),
            execution_id=model.execution_id,
            metadata=_deserialize_metadata(model.job_metadata),
        )

    def _to_model(self, job: ScheduledJob) -> ScheduledJobModel:
        return ScheduledJobModel(
            job_id=job.job_id,
            name=job.name,
            enabled=1 if job.enabled else 0,
            schedule_type=job.schedule_type.value,
            schedule_expr=job.schedule_expr,
            timezone=job.timezone,
            target_type=job.target_type,
            target_id=job.target_id,
            status=job.status.value,
            last_run_at=_to_naive_utc(job.last_run_at),
            next_run_at=_to_naive_utc(job.next_run_at),
            created_at=_to_naive_utc(job.created_at),
            updated_at=_to_naive_utc(job.updated_at),
            occurrence_id=job.occurrence_id,
            claim_token=job.claim_token,
            claim_sequence=job.claim_sequence,
            claimed_at=_to_naive_utc(job.claimed_at),
            lease_until=_to_naive_utc(job.lease_until),
            execution_id=job.execution_id,
            job_metadata=_serialize_metadata(job.metadata),
        )

    async def create_job(self, job: ScheduledJob) -> ScheduledJob:
        """
        Persist a new scheduled job.
        """
        model = self._to_model(job)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_domain(model)

    async def get_job(self, job_id: str) -> ScheduledJob | None:
        """
        Retrieve a scheduled job by job_id.
        """
        stmt = select(ScheduledJobModel).where(ScheduledJobModel.job_id == job_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_domain(model)

    async def list_jobs(
        self,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        status: SchedulerJobStatus | str | None = None,
        enabled: bool | None = None,
        schedule_type: ScheduleType | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScheduledJob]:
        """
        List scheduled jobs with optional filters.
        """
        stmt = select(ScheduledJobModel)

        if target_type is not None:
            stmt = stmt.where(ScheduledJobModel.target_type == target_type)
        if target_id is not None:
            stmt = stmt.where(ScheduledJobModel.target_id == target_id)
        if status is not None:
            status_str = status.value if isinstance(status, SchedulerJobStatus) else str(status)
            stmt = stmt.where(ScheduledJobModel.status == status_str)
        if enabled is not None:
            stmt = stmt.where(ScheduledJobModel.enabled == (1 if enabled else 0))
        if schedule_type is not None:
            schedule_type_str = schedule_type.value if isinstance(schedule_type, ScheduleType) else str(schedule_type)
            stmt = stmt.where(ScheduledJobModel.schedule_type == schedule_type_str)

        stmt = stmt.order_by(ScheduledJobModel.next_run_at.asc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models]

    async def update_job(self, job_id: str, **updates: Any) -> ScheduledJob | None:
        """
        Update a scheduled job. Returns the updated job or None if not found.
        """
        allowed_fields = {
            "name", "enabled", "schedule_type", "schedule_expr", "timezone",
            "target_type", "target_id", "status", "last_run_at", "next_run_at",
            "occurrence_id", "claim_token", "claim_sequence", "claimed_at", "lease_until",
            "execution_id", "job_metadata",
        }
        update_data: dict[str, Any] = {"updated_at": _to_naive_utc(datetime.now(timezone.utc))}
        for key, value in updates.items():
            if key not in allowed_fields:
                raise ValueError(f"Field {key!r} is not allowed in job updates")
            if key in ("last_run_at", "next_run_at", "claimed_at", "lease_until"):
                value = _to_naive_utc(value)
            elif key == "enabled":
                value = 1 if value else 0
            elif key == "schedule_type" and isinstance(value, ScheduleType):
                value = value.value
            elif key == "status" and isinstance(value, SchedulerJobStatus):
                value = value.value
            elif key == "job_metadata" and isinstance(value, dict):
                value = _serialize_metadata(value)
            update_data[key] = value

        stmt = (
            update(ScheduledJobModel)
            .where(ScheduledJobModel.job_id == job_id)
            .values(**update_data)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        if result.rowcount == 0:
            return None
        return await self.get_job(job_id)

    async def delete_job(self, job_id: str) -> bool:
        """
        Delete a scheduled job. Returns True if deleted, False if not found.
        """
        stmt = select(ScheduledJobModel).where(ScheduledJobModel.job_id == job_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True

    async def get_jobs_by_target(self, target_type: str, target_id: str) -> list[ScheduledJob]:
        """
        Retrieve all scheduled jobs for a specific target (task or plan).
        """
        return await self.list_jobs(target_type=target_type, target_id=target_id)

    async def count_jobs(
        self,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        status: SchedulerJobStatus | str | None = None,
        enabled: bool | None = None,
        schedule_type: ScheduleType | str | None = None,
    ) -> int:
        """
        Count scheduled jobs matching the given filters.
        """
        stmt = select(ScheduledJobModel)

        if target_type is not None:
            stmt = stmt.where(ScheduledJobModel.target_type == target_type)
        if target_id is not None:
            stmt = stmt.where(ScheduledJobModel.target_id == target_id)
        if status is not None:
            status_str = status.value if isinstance(status, SchedulerJobStatus) else str(status)
            stmt = stmt.where(ScheduledJobModel.status == status_str)
        if enabled is not None:
            stmt = stmt.where(ScheduledJobModel.enabled == (1 if enabled else 0))
        if schedule_type is not None:
            schedule_type_str = schedule_type.value if isinstance(schedule_type, ScheduleType) else str(schedule_type)
            stmt = stmt.where(ScheduledJobModel.schedule_type == schedule_type_str)

        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(stmt.subquery())
        result = await self._session.execute(count_stmt)
        return result.scalar_one() or 0

    async def get_job_by_occurrence_id(self, occurrence_id: str) -> ScheduledJob | None:
        """Retrieve a scheduled job by occurrence_id."""
        stmt = select(ScheduledJobModel).where(
            ScheduledJobModel.occurrence_id == occurrence_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_due_pending_jobs(self, now: datetime, limit: int = 50) -> list[ScheduledJob]:
        """Return enabled pending jobs whose next_run_at has arrived."""
        stmt = (
            select(ScheduledJobModel)
            .where(
                ScheduledJobModel.status == "PENDING",
                ScheduledJobModel.next_run_at <= _to_naive_utc(now),
                ScheduledJobModel.enabled == 1,
            )
            .order_by(ScheduledJobModel.next_run_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars().all()]

    async def claim_next_occurrence(
        self,
        job_id: str,
        now: datetime,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
        claim_token: str | None = None,
        occurrence_id: str | None = None,
    ) -> ScheduledJob | None:
        """Atomically claim the next due occurrence.

        Preconditions (all enforced in SQL):
        - status = 'PENDING'
        - next_run_at <= :now
        - enabled = 1

        On success:
        - status = 'CLAIMED'
        - occurrence_id, claim_token, claimed_at, lease_until set
        - updated_at = :now

        Returns the claimed job, or None if not eligible.
        """
        if claim_token is None:
            claim_token = generate_claim_token()
        if occurrence_id is None:
            occurrence_id = generate_occurrence_id(job_id, now)

        naive_now = _to_naive_utc(now)
        lease_until = _to_naive_utc(now + lease_duration)

        async def _do_claim():
            stmt = (
                update(ScheduledJobModel)
                .where(
                    ScheduledJobModel.job_id == job_id,
                    ScheduledJobModel.status == "PENDING",
                    ScheduledJobModel.next_run_at <= naive_now,
                    ScheduledJobModel.enabled == 1,
                )
                .values(
                    status="CLAIMED",
                    occurrence_id=occurrence_id,
                    claim_token=claim_token,
                    claim_sequence=ScheduledJobModel.claim_sequence + 1,
                    claimed_at=naive_now,
                    lease_until=lease_until,
                    updated_at=naive_now,
                )
                .returning(ScheduledJobModel)
            )
            result = await self._session.execute(stmt)
            await self._session.flush()
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

        claimed = await _retry_on_locked(_do_claim, self._session)
        if claimed is not None:
            await self._emit_scheduler_event(
                event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED,
                job=claimed,
            payload={
                "job_id": claimed.job_id,
                "occurrence_id": claimed.occurrence_id,
                "claim_sequence": claimed.claim_sequence,
                "schedule_type": claimed.schedule_type.value,
                "schedule_expr": claimed.schedule_expr,
                "target_type": claimed.target_type,
                "target_id": claimed.target_id,
            },
            )
        return claimed

    async def renew_lease(
        self,
        occurrence_id: str,
        claim_token: str,
        now: datetime,
        extension: timedelta = DEFAULT_LEASE_DURATION,
    ) -> ScheduledJob | None:
        """Renew the lease for the current valid owner.

        Preconditions:
        - status = 'CLAIMED'
        - occurrence_id = :occurrence_id
        - claim_token = :claim_token
        - lease_until > :now (still valid owner)

        Returns the updated job, or None if not the current owner or lease expired.
        """
        naive_now = _to_naive_utc(now)
        lease_until = _to_naive_utc(now + extension)

        stmt = (
            update(ScheduledJobModel)
            .where(
                ScheduledJobModel.occurrence_id == occurrence_id,
                ScheduledJobModel.claim_token == claim_token,
                ScheduledJobModel.status == "CLAIMED",
                ScheduledJobModel.lease_until > naive_now,
            )
            .values(
                lease_until=lease_until,
                updated_at=naive_now,
            )
            .returning(ScheduledJobModel)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one_or_none()
        recovered = self._to_domain(row) if row else None
        if recovered is not None:
            await self._emit_scheduler_event(
                event_type=EventType.SCHEDULER_OCCURRENCE_RECOVERED,
                job=recovered,
                payload={"reason": "lease_expired"},
            )
        return recovered

    async def release_lease(
        self,
        occurrence_id: str,
        claim_token: str,
        now: datetime | None = None,
    ) -> bool:
        """Voluntarily release a claim, resetting the occurrence to PENDING.

        Preconditions:
        - status = 'CLAIMED'
        - occurrence_id = :occurrence_id
        - claim_token = :claim_token
        - lease_until > :now (still valid owner)

        Returns True if released, False if preconditions not met.
        """
        if now is None:
            now = now_utc()
        naive_now = _to_naive_utc(now)

        stmt = (
            update(ScheduledJobModel)
            .where(
                ScheduledJobModel.occurrence_id == occurrence_id,
                ScheduledJobModel.claim_token == claim_token,
                ScheduledJobModel.status == "CLAIMED",
                ScheduledJobModel.lease_until > naive_now,
            )
            .values(
                status="PENDING",
                occurrence_id=None,
                claim_token=None,
                claimed_at=None,
                lease_until=None,
                updated_at=naive_now,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount > 0

    async def finalize_triggered(
        self,
        occurrence_id: str,
        claim_token: str,
        execution_id: str,
        now: datetime,
    ) -> ScheduledJob | None:
        """Phase 2 handoff: mark claimed occurrence as triggered.

        Preconditions:
        - status = 'CLAIMED'
        - occurrence_id = :occurrence_id
        - claim_token = :claim_token

        Returns the updated job, or None if not the current owner.
        """
        naive_now = _to_naive_utc(now)

        stmt = (
            update(ScheduledJobModel)
            .where(
                ScheduledJobModel.occurrence_id == occurrence_id,
                ScheduledJobModel.claim_token == claim_token,
                ScheduledJobModel.status == "CLAIMED",
                ScheduledJobModel.lease_until > naive_now,
            )
            .values(
                status="TRIGGERED",
                execution_id=execution_id,
                updated_at=naive_now,
            )
            .returning(ScheduledJobModel)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one_or_none()
        triggered = self._to_domain(row) if row else None
        if triggered is not None:
            causation_id = make_scheduler_event_id(
                occurrence_id=triggered.occurrence_id or "",
                claim_sequence=triggered.claim_sequence,
                event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED.value,
            )
            await self._emit_scheduler_event(
                event_type=EventType.SCHEDULER_OCCURRENCE_TRIGGERED,
                job=triggered,
                causation_id=causation_id,
                payload={
                    "execution_id": execution_id,
                },
            )
        return triggered

    async def finalize_triggered_and_advance(
        self,
        occurrence_id: str,
        claim_token: str,
        execution_id: str,
        now: datetime,
    ) -> ScheduledJob | None:
        """Record TRIGGERED and advance the schedule atomically.

        Preconditions:
        - status = 'CLAIMED'
        - occurrence_id = :occurrence_id
        - claim_token = :claim_token
        - lease_until > :now

        Returns the updated job, or None if not the current owner.
        """
        naive_now = _to_naive_utc(now)

        # Read current state to compute advancement
        current = await self.get_job_by_occurrence_id(occurrence_id)
        if current is None or current.claim_token != claim_token:
            return None
        if current.status != SchedulerJobStatus.CLAIMED:
            return None
        if current.lease_until is not None and current.lease_until <= now:
            return None

        definition = ScheduleDefinition(
            schedule_type=current.schedule_type,
            schedule_expr=current.schedule_expr,
            timezone=current.timezone,
        )
        reference = max(now, current.next_run_at)
        last_run_at = current.next_run_at
        next_run_at = calculate_next_occurrence(definition, reference)

        new_status = "TRIGGERED"
        if next_run_at is None:
            next_run_at = last_run_at
            new_status = "DISABLED"

        stmt = (
            update(ScheduledJobModel)
            .where(
                ScheduledJobModel.occurrence_id == occurrence_id,
                ScheduledJobModel.claim_token == claim_token,
                ScheduledJobModel.status == "CLAIMED",
                ScheduledJobModel.lease_until > naive_now,
            )
            .values(
                status=new_status,
                next_run_at=_to_naive_utc(next_run_at),
                last_run_at=_to_naive_utc(last_run_at),
                claimed_at=None,
                lease_until=None,
                occurrence_id=occurrence_id,
                claim_token=claim_token,
                execution_id=execution_id,
                updated_at=naive_now,
            )
            .returning(ScheduledJobModel)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one_or_none()
        advanced = self._to_domain(row) if row else None
        if advanced is not None:
            causation_id = make_scheduler_event_id(
                occurrence_id=occurrence_id,
                claim_sequence=advanced.claim_sequence,
                event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED.value,
            )
            await self._emit_scheduler_event(
                event_type=EventType.SCHEDULER_OCCURRENCE_TRIGGERED,
                job=advanced,
                causation_id=causation_id,
                payload={
                    "execution_id": execution_id,
                },
            )
        return advanced

    async def finalize_failed(
        self,
        occurrence_id: str,
        claim_token: str,
        now: datetime,
        reason_code: str = "execution_error",
        reason_detail: str = "",
    ) -> bool:
        """Mark a claimed occurrence as failed.

        Preconditions:
        - status = 'CLAIMED'
        - occurrence_id = :occurrence_id
        - claim_token = :claim_token

        Returns True if marked failed, False if preconditions not met.
        """
        naive_now = _to_naive_utc(now)

        stmt = (
            update(ScheduledJobModel)
            .where(
                ScheduledJobModel.occurrence_id == occurrence_id,
                ScheduledJobModel.claim_token == claim_token,
                ScheduledJobModel.status == "CLAIMED",
                ScheduledJobModel.lease_until > naive_now,
            )
            .values(
                status="FAILED",
                updated_at=naive_now,
            )
            .returning(ScheduledJobModel)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one_or_none()
        if row is not None:
            failed = self._to_domain(row)
            causation_id = make_scheduler_event_id(
                occurrence_id=failed.occurrence_id or "",
                claim_sequence=failed.claim_sequence,
                event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED.value,
            )
            await self._emit_scheduler_event(
                event_type=EventType.SCHEDULER_OCCURRENCE_FAILED,
                job=failed,
                causation_id=causation_id,
                payload={
                    "reason_code": reason_code,
                    "reason_detail": reason_detail[:200] if reason_detail else "",
                },
            )
            return True
        return False

    async def finalize_skipped(
        self,
        occurrence_id: str,
        claim_token: str,
        now: datetime,
        reason_code: str = "target_already_running",
        reason_detail: str = "",
    ) -> ScheduledJob | None:
        """Atomically skip a claimed occurrence and advance the schedule.

        Preconditions:
        - status = 'CLAIMED'
        - occurrence_id = :occurrence_id
        - claim_token = :claim_token
        - lease_until > :now

        Returns the updated job, or None if preconditions not met.

        This is used for concurrency SKIP: when the target is already running,
        the occurrence is marked SKIPPED (not FAILED) and the schedule is
        advanced so the skipped occurrence cannot be rediscovered as due.
        """
        naive_now = _to_naive_utc(now)

        current = await self.get_job_by_occurrence_id(occurrence_id)
        if current is None or current.claim_token != claim_token:
            return None
        if current.status != SchedulerJobStatus.CLAIMED:
            return None
        if current.lease_until is not None and current.lease_until <= now:
            return None

        definition = ScheduleDefinition(
            schedule_type=current.schedule_type,
            schedule_expr=current.schedule_expr,
            timezone=current.timezone,
        )
        reference = max(now, current.next_run_at)
        last_run_at = current.next_run_at
        next_run_at = calculate_next_occurrence(definition, reference)

        new_status = "SKIPPED"
        if next_run_at is None:
            next_run_at = reference
            new_status = "DISABLED"

        stmt = (
            update(ScheduledJobModel)
            .where(
                ScheduledJobModel.occurrence_id == occurrence_id,
                ScheduledJobModel.claim_token == claim_token,
                ScheduledJobModel.status == "CLAIMED",
                ScheduledJobModel.lease_until > naive_now,
            )
            .values(
                status=new_status,
                next_run_at=_to_naive_utc(next_run_at),
                last_run_at=_to_naive_utc(last_run_at),
                claimed_at=None,
                lease_until=None,
                occurrence_id=occurrence_id,
                claim_token=claim_token,
                updated_at=naive_now,
            )
            .returning(ScheduledJobModel)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one_or_none()
        skipped = self._to_domain(row) if row else None
        if skipped is not None:
            causation_id = make_scheduler_event_id(
                occurrence_id=occurrence_id,
                claim_sequence=skipped.claim_sequence,
                event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED.value,
            )
            await self._emit_scheduler_event(
                event_type=EventType.SCHEDULER_OCCURRENCE_SKIPPED,
                job=skipped,
                causation_id=causation_id,
                payload={
                    "reason_code": reason_code,
                    "reason_detail": reason_detail[:200] if reason_detail else "",
                },
            )
        return skipped

    async def get_expired_leases(self, now: datetime) -> list[ScheduledJob]:
        """Return all occurrences whose lease has expired."""
        naive_now = _to_naive_utc(now)
        stmt = (
            select(ScheduledJobModel)
            .where(
                ScheduledJobModel.status.in_(["CLAIMED", "TRIGGERED"]),
                ScheduledJobModel.lease_until <= naive_now,
            )
            .order_by(ScheduledJobModel.lease_until.asc())
        )
        result = await self._session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars().all()]

    async def reset_expired_claim_to_pending(
        self,
        occurrence_id: str,
        now: datetime,
        next_run_at: datetime,
    ) -> ScheduledJob | None:
        """Recovery: reset an expired CLAIMED occurrence back to PENDING.

        Preconditions:
        - status = 'CLAIMED'
        - occurrence_id = :occurrence_id
        - lease_until < :now (expired)

        Returns the reset job, or None if the lease is still valid.
        """
        naive_now = _to_naive_utc(now)
        naive_next = _to_naive_utc(next_run_at)

        stmt = (
            update(ScheduledJobModel)
            .where(
                ScheduledJobModel.occurrence_id == occurrence_id,
                ScheduledJobModel.status == "CLAIMED",
                ScheduledJobModel.lease_until <= naive_now,
            )
            .values(
                status="PENDING",
                occurrence_id=occurrence_id,
                claim_token=None,
                claimed_at=None,
                lease_until=None,
                next_run_at=naive_next,
                updated_at=naive_now,
            )
            .returning(ScheduledJobModel)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one_or_none()
        recovered = self._to_domain(row) if row else None
        if recovered is not None:
            causation_id = make_scheduler_event_id(
                occurrence_id=recovered.occurrence_id or "",
                claim_sequence=recovered.claim_sequence,
                event_type=EventType.SCHEDULER_OCCURRENCE_CLAIMED.value,
            )
            await self._emit_scheduler_event(
                event_type=EventType.SCHEDULER_OCCURRENCE_RECOVERED,
                job=recovered,
                causation_id=causation_id,
                payload={"reason": "lease_expired"},
            )
        return recovered
