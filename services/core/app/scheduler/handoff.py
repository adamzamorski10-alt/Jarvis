"""
Durable scheduler execution handoff boundary.

This module owns the transition from scheduler-owned occurrence to the
existing execution architecture. It does NOT execute tasks or plans itself;
it delegates to the already-established application-owned entry points:

  - Task execution : AutonomousRunController.start_run(task_id)
  - Plan execution  : BackgroundExecutionManager.start_execution(plan_id, ...)

Architectural invariants:
  1. Scheduler owns WHEN; existing execution layer owns HOW.
  2. Scheduler claim/lease proves scheduler ownership ONLY.
  3. Authorization, approval, and verification remain in the execution layer.
  4. TRIGGERED means the scheduler has successfully handed the occurrence to
     the execution layer and stored a durable execution reference.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, ConfigDict, Field

from app.scheduler.lease import now_utc
from app.scheduler.models import ScheduledJob, ScheduleType, SchedulerJobStatus
from app.scheduler.repository import SchedulerRepository
from app.services.background_execution_manager import PlanAlreadyExecutingError, PlanNotExecutableError
from app.tasks.models import TaskStatus
from app.orchestration.exceptions import MaxAttemptsExceededError, TaskNotStartableError
from app.tasks.exceptions import StaleStateTransitionError

logger = logging.getLogger(__name__)


class HandoffError(Exception):
    """Base exception for scheduler handoff failures."""
    pass


class TargetNotFoundError(HandoffError):
    """Raised when the scheduled target (task or plan) does not exist."""
    def __init__(self, target_type: str, target_id: str) -> None:
        self.target_type = target_type
        self.target_id = target_id
        super().__init__(f"{target_type} '{target_id}' not found")


class TargetNotStartableError(HandoffError):
    """Raised when the target exists but cannot be executed."""
    def __init__(self, target_type: str, target_id: str, reason: str) -> None:
        self.target_type = target_type
        self.target_id = target_id
        self.reason = reason
        super().__init__(f"{target_type} '{target_id}' cannot be executed: {reason}")


class HandoffExecutionError(HandoffError):
    """Raised when the execution layer rejects the handoff."""
    def __init__(self, target_type: str, target_id: str, reason: str) -> None:
        self.target_type = target_type
        self.target_id = target_id
        self.reason = reason
        super().__init__(f"Execution failed for {target_type} '{target_id}': {reason}")


class HandoffResult(BaseModel):
    """Outcome of a scheduler handoff attempt."""
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    success: bool
    occurrence_id: str
    execution_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    error: str | None = None
    handed_off_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SchedulerHandoffService:
    """
    Durable handoff boundary between scheduler occurrences and the existing
    execution architecture.

    This service does NOT execute tasks or plans. It delegates to the
    application-owned execution entry points and records the durable handoff
    marker in the scheduler's own database row.
    """

    def __init__(
        self,
        scheduler_repo: SchedulerRepository,
        task_repo: Any | None = None,
        task_state_machine: Any | None = None,
        run_controller: Any | None = None,
        background_execution_manager: Any | None = None,
        plan_executor_factory: Callable[[], tuple[Any, Any]] | Callable[[], Awaitable[tuple[Any, Any]]] | None = None,
    ) -> None:
        self._scheduler_repo = scheduler_repo
        self._task_repo = task_repo
        self._task_state_machine = task_state_machine
        self._run_controller = run_controller
        self._background_execution_manager = background_execution_manager
        self._plan_executor_factory = plan_executor_factory

    def _assert_task_dependencies(self) -> None:
        if self._run_controller is None or self._task_repo is None or self._task_state_machine is None:
            raise RuntimeError(
                "Task handoff requires run_controller, task_repo, and task_state_machine. "
                "Construct SchedulerHandoffService with all required dependencies."
            )

    def _assert_plan_dependencies(self) -> None:
        if self._background_execution_manager is None or self._plan_executor_factory is None:
            raise RuntimeError(
                "Plan handoff requires background_execution_manager and plan_executor_factory. "
                "Construct SchedulerHandoffService with all required dependencies."
            )

    async def handoff_task(self, occurrence_id: str, claim_token: str) -> HandoffResult:
        """
        Handoff a scheduler occurrence to the existing task execution path.

        Preconditions:
        - occurrence is CLAIMED with matching claim_token and valid lease
        - target_type == 'task'
        - task exists and is startable

        On success:
        - occurrence is marked TRIGGERED with execution_id = task.execution_token
        - task execution has started via AutonomousRunController

        On failure:
        - occurrence remains CLAIMED (for retry) or FAILED
        - no task state mutation
        """
        self._assert_task_dependencies()
        now = now_utc()

        job = await self._scheduler_repo.get_job_by_occurrence_id(occurrence_id)
        if job is None:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                error=f"Occurrence '{occurrence_id}' not found",
            )

        if job.claim_token != claim_token:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                error="Invalid claim token",
            )

        if job.status != SchedulerJobStatus.CLAIMED:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                error=f"Occurrence is in status '{job.status.value}', expected CLAIMED",
            )

        if job.lease_until is not None and job.lease_until <= now:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                error="Lease has expired",
            )

        if job.target_type != "task":
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type=job.target_type,
                target_id=job.target_id,
                error=f"Expected target_type='task', got '{job.target_type}'",
            )

        task = await self._task_repo.get_task(job.target_id)
        if task is None:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="task",
                target_id=job.target_id,
                error="Target task not found",
            )

        if task.status in (TaskStatus.COMMITTED, TaskStatus.CANCELLED):
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="task",
                target_id=job.target_id,
                error=f"Task is in terminal status '{task.status.value}'",
            )

        if task.status in (TaskStatus.RUNNING, TaskStatus.VERIFYING, TaskStatus.PASSED):
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="task",
                target_id=job.target_id,
                error=f"Task is already in status '{task.status.value}'",
            )

        try:
            run_result = await self._run_controller.start_run(
                task_id=job.target_id,
            )
        except TaskNotStartableError as exc:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="task",
                target_id=job.target_id,
                error=f"Task not startable: {exc}",
            )
        except MaxAttemptsExceededError as exc:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="task",
                target_id=job.target_id,
                error=f"Max attempts exceeded: {exc}",
            )
        except StaleStateTransitionError as exc:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="task",
                target_id=job.target_id,
                error=f"Stale task state: {exc}",
            )
        except Exception as exc:
            logger.exception("Unexpected error during task handoff for occurrence %s: %s", occurrence_id, exc)
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="task",
                target_id=job.target_id,
                error=f"Execution error: {exc}",
            )

        execution_id = None
        if run_result is not None:
            fresh_task = await self._task_repo.get_task(job.target_id)
            if fresh_task is not None and fresh_task.execution_token is not None:
                execution_id = fresh_task.execution_token

        finalized = await self._scheduler_repo.finalize_triggered_and_advance(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            execution_id=execution_id or "",
            now=now,
        )
        if finalized is None:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="task",
                target_id=job.target_id,
                error="Failed to record TRIGGERED state (stale claim or lease expired)",
            )

        return HandoffResult(
            success=True,
            occurrence_id=occurrence_id,
            execution_id=execution_id,
            target_type="task",
            target_id=job.target_id,
        )

    async def handoff_plan(self, occurrence_id: str, claim_token: str) -> HandoffResult:
        """
        Handoff a scheduler occurrence to the existing plan execution path.

        Preconditions:
        - occurrence is CLAIMED with matching claim_token and valid lease
        - target_type == 'plan'
        - plan exists (validated by BackgroundExecutionManager)

        On success:
        - occurrence is marked TRIGGERED with execution_id = BackgroundExecutionManager.execution_id
        - plan execution has been queued

        On failure:
        - occurrence remains CLAIMED (for retry) or FAILED
        - no plan state mutation

        NOTE: BackgroundExecutionManager.execution_id is in-memory only and
        NOT durably persisted. If the process crashes after handoff but before
        the scheduler records TRIGGERED, the execution_id may be lost. Recovery
        must use plan-level durable markers (task execution_tokens, events)
        to reconcile.
        """
        self._assert_plan_dependencies()
        now = now_utc()

        job = await self._scheduler_repo.get_job_by_occurrence_id(occurrence_id)
        if job is None:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                error=f"Occurrence '{occurrence_id}' not found",
            )

        if job.claim_token != claim_token:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                error="Invalid claim token",
            )

        if job.status != SchedulerJobStatus.CLAIMED:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                error=f"Occurrence is in status '{job.status.value}', expected CLAIMED",
            )

        if job.lease_until is not None and job.lease_until <= now:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                error="Lease has expired",
            )

        if job.target_type != "plan":
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type=job.target_type,
                target_id=job.target_id,
                error=f"Expected target_type='plan', got '{job.target_type}'",
            )

        try:
            handle_info = await self._background_execution_manager.start_execution(
                plan_id=job.target_id,
                executor_factory=self._plan_executor_factory,
            )
            execution_id = handle_info.execution_id
        except PlanAlreadyExecutingError as exc:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="plan",
                target_id=job.target_id,
                error=f"Plan already executing: {exc}",
            )
        except PlanNotExecutableError as exc:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="plan",
                target_id=job.target_id,
                error=f"Plan not executable: {exc}",
            )
        except Exception as exc:
            logger.exception("Unexpected error during plan handoff for occurrence %s: %s", occurrence_id, exc)
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="plan",
                target_id=job.target_id,
                error=f"Execution error: {exc}",
            )

        finalized = await self._scheduler_repo.finalize_triggered_and_advance(
            occurrence_id=occurrence_id,
            claim_token=claim_token,
            execution_id=execution_id,
            now=now,
        )
        if finalized is None:
            return HandoffResult(
                success=False,
                occurrence_id=occurrence_id,
                target_type="plan",
                target_id=job.target_id,
                error="Failed to record TRIGGERED state (stale claim or lease expired)",
            )

        return HandoffResult(
            success=True,
            occurrence_id=occurrence_id,
            execution_id=execution_id,
            target_type="plan",
            target_id=job.target_id,
        )
