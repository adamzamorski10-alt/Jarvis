"""
Scheduler worker — background polling loop for advancing due scheduled jobs.

This module owns WHEN a scheduled job fires. It delegates HOW the job executes
to the existing execution architecture:

- Task execution: SchedulerHandoffService → AutonomousRunController
- Plan execution: SchedulerHandoffService → BackgroundExecutionManager

The worker does NOT execute tasks or plans itself.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Awaitable

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.scheduler.calculator import ScheduleDefinition, calculate_next_occurrence
from app.scheduler.lease import now_utc, DEFAULT_LEASE_DURATION
from app.scheduler.repository import SchedulerRepository
from app.scheduler.handoff import SchedulerHandoffService, HandoffResult
from app.events.schemas import EventType

logger = logging.getLogger(__name__)


class SchedulerWorker:
    """Background polling worker for scheduled jobs."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        handoff_factory: Callable[[Any], SchedulerHandoffService],
        poll_interval: float = 60.0,
        batch_size: int = 10,
        notification_policy: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._handoff_factory = handoff_factory
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._notification_policy = notification_policy
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the worker background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Scheduler worker started", poll_interval=self._poll_interval)

    async def stop(self) -> None:
        """Stop the worker and wait for it to finish."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Scheduler worker stopped")

    async def _run(self) -> None:
        """Main worker loop."""
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Worker poll iteration failed: %s", exc)

            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

    async def _poll_once(self) -> None:
        """Single polling iteration."""
        now = now_utc()
        async with self._session_factory() as session:
            event_emitter = self._build_event_emitter(session)
            repo = SchedulerRepository(session, event_emitter=event_emitter)
            handoff = self._handoff_factory(repo)

            try:
                # 1. Recovery: reset expired leases
                await self._recover_expired(repo, now)

                # 2. Poll due jobs
                due_jobs = await repo.get_due_pending_jobs(now, limit=self._batch_size)
                for job in due_jobs:
                    if not self._running:
                        break
                    try:
                        await self._process_job(repo, handoff, job, now)
                    except Exception as exc:
                        logger.exception(
                            "Failed to process job %s: %s",
                            job.job_id,
                            exc,
                        )
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()

    def _build_event_emitter(self, session: Any) -> Any:
        """Build an event emitter bound to the current session."""
        from app.events.repository import EventRepository
        from app.events.emitter import EventEmitter
        from app.notifications.repository import NotificationRepository
        event_repo = EventRepository(session)
        notification_repo = NotificationRepository(session) if self._notification_policy is not None else None
        return EventEmitter(event_repo, notification_policy=self._notification_policy, notification_repository=notification_repo)

    async def _recover_expired(self, repo: SchedulerRepository, now: datetime) -> None:
        """Reset expired leases back to PENDING."""
        expired = await repo.get_expired_leases(now)
        for job in expired:
            if not self._running:
                break
            try:
                next_run = self._compute_next_run(job, now)
                recovered = await repo.reset_expired_claim_to_pending(
                    job.occurrence_id,
                    now,
                    next_run or now,
                )
                if recovered is not None:
                    logger.info(
                        "Recovered expired lease",
                        job_id=job.job_id,
                        occurrence_id=job.occurrence_id,
                    )
            except Exception as exc:
                logger.exception(
                    "Failed to recover job %s: %s",
                    job.job_id,
                    exc,
                )

    async def _process_job(
        self,
        repo: SchedulerRepository,
        handoff: SchedulerHandoffService,
        job: Any,
        now: datetime,
    ) -> None:
        """Claim, handoff, and advance a single job."""
        occurrence_id = self._generate_occurrence_id(job, now)
        claim_token = self._generate_claim_token()

        claimed = await repo.claim_next_occurrence(
            job.job_id,
            now,
            DEFAULT_LEASE_DURATION,
            claim_token,
            occurrence_id,
        )
        if claimed is None:
            return

        result: HandoffResult | None = None
        try:
            result = await self._handoff(handoff, claimed)
        except Exception as exc:
            logger.error(
                "Handoff raised for %s: %s",
                occurrence_id,
                exc,
            )
            return

        if result is None:
            logger.error(
                "Handoff returned None for %s",
                occurrence_id,
            )
            return

        if result.success:
            logger.info(
                "Handoff succeeded",
                job_id=job.job_id,
                occurrence_id=occurrence_id,
                execution_id=result.execution_id,
            )
            return

        error = result.error or ""
        if self._is_already_running_error(error):
            skipped = await repo.finalize_skipped(
                occurrence_id=occurrence_id,
                claim_token=claim_token,
                now=now,
                reason_code="target_already_running",
                reason_detail=error,
            )
            if skipped is not None:
                logger.info(
                    "Skipped occurrence (target already running)",
                    job_id=job.job_id,
                    occurrence_id=occurrence_id,
                )
            else:
                logger.warning(
                    "Failed to record SKIPPED state for %s: stale claim or lease expired",
                    occurrence_id,
                )
            return

        if self._is_permanent_target_error(error):
            finalized = await repo.finalize_failed(
                occurrence_id=occurrence_id,
                claim_token=claim_token,
                now=now,
                reason_code=self._reason_code_for_error(error),
                reason_detail=error,
            )
            if finalized:
                logger.warning(
                    "Marked occurrence as FAILED",
                    job_id=job.job_id,
                    occurrence_id=occurrence_id,
                    error=error,
                )
            else:
                logger.warning(
                    "Failed to record FAILED state for %s: stale claim or lease expired",
                    occurrence_id,
                )
            return

        logger.warning(
            "Handoff failed, leaving occurrence CLAIMED for retry",
            job_id=job.job_id,
            occurrence_id=occurrence_id,
            error=error,
        )

    @staticmethod
    def _is_already_running_error(error: str) -> bool:
        if not error:
            return False
        lower = error.lower()
        return (
            "already in status" in lower
            or "plan already executing" in lower
        )

    @staticmethod
    def _is_permanent_target_error(error: str) -> bool:
        if not error:
            return False
        lower = error.lower()
        permanent_indicators = (
            "not found",
            "terminal status",
            "not executable",
            "not startable",
            "max attempts exceeded",
            "stale task state",
            "execution error",
        )
        return any(indicator in lower for indicator in permanent_indicators)

    @staticmethod
    def _reason_code_for_error(error: str) -> str:
        if not error:
            return "execution_error"
        lower = error.lower()
        if "not found" in lower:
            return "target_not_found"
        if "terminal status" in lower:
            return "target_invalid"
        if "not executable" in lower:
            return "target_invalid"
        if "not startable" in lower:
            return "target_invalid"
        if "max attempts exceeded" in lower:
            return "target_invalid"
        if "stale task state" in lower:
            return "target_invalid"
        return "execution_error"

    async def _handoff(
        self,
        handoff: SchedulerHandoffService,
        claimed: Any,
    ) -> Any:
        """Perform handoff based on target type."""
        occurrence_id = claimed.occurrence_id
        claim_token = claimed.claim_token

        if claimed.target_type == "task":
            return await handoff.handoff_task(occurrence_id, claim_token)
        if claimed.target_type == "plan":
            return await handoff.handoff_plan(occurrence_id, claim_token)

        logger.warning(
            "Unsupported target_type %s for job %s",
            claimed.target_type,
            claimed.job_id,
        )
        return type("HandoffResult", (), {"success": False, "error": "unsupported target_type"})()

    def _generate_occurrence_id(self, job: Any, now: datetime) -> str:
        """Generate a deterministic occurrence ID."""
        return f"occurrence_{job.job_id}_{now.strftime('%Y%m%d%H%M%S')}_{id(self)}"

    def _generate_claim_token(self) -> str:
        """Generate a unique claim token."""
        import secrets
        return secrets.token_hex(16)

    def _compute_next_run(self, job: Any, now: datetime) -> datetime | None:
        """Compute the next run time for a job after recovery."""
        definition = ScheduleDefinition(
            schedule_type=job.schedule_type,
            schedule_expr=job.schedule_expr,
            timezone=job.timezone,
        )
        reference = max(now, job.next_run_at)
        return calculate_next_occurrence(definition, reference)
