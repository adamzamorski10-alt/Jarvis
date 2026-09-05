"""
Task State Machine & Transition Engine.
Authoritative policy layer for DevelopmentTask state transitions.
"""
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.repositories.task_repository import TaskRepository

from app.tasks.exceptions import (
    InvalidStateTransitionError,
    StaleStateTransitionError,
    TaskNotFoundError,
)
from app.tasks.models import DevelopmentTask, TaskStatus, TaskTransitionResult


# Authoritative State Transition Matrix
_ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.VERIFYING,
        TaskStatus.REPAIR_REQUIRED,
        TaskStatus.PAUSED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.ABORTED,
    },
    TaskStatus.VERIFYING: {
        TaskStatus.PASSED,
        TaskStatus.REPAIR_REQUIRED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.ABORTED,
    },
    TaskStatus.PASSED: {
        TaskStatus.COMMITTED,
        TaskStatus.REPAIR_REQUIRED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.ABORTED,
    },
    TaskStatus.REPAIR_REQUIRED: {
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.ABORTED,
    },
    TaskStatus.PAUSED: {
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
        TaskStatus.ABORTED,
    },
    TaskStatus.FAILED: {
        TaskStatus.REPAIR_REQUIRED,
        TaskStatus.CANCELLED,
        TaskStatus.ABORTED,
    },
    TaskStatus.ABORTED: {
        TaskStatus.REPAIR_REQUIRED,
    },
    TaskStatus.CANCELLED: set(),  # Terminal state
    TaskStatus.COMMITTED: set(),  # Terminal state
}


class TaskStateMachine:
    """
    Authoritative state transition engine for DevelopmentTask objects.
    Enforces legal state transitions and executes atomic Compare-And-Set operations.
    """
    def __init__(self, task_repo: "TaskRepository") -> None:
        self._repo = task_repo

    def is_transition_allowed(
        self,
        current_status: TaskStatus | str,
        new_status: TaskStatus | str,
    ) -> bool:
        curr_enum = current_status if isinstance(current_status, TaskStatus) else TaskStatus(current_status)
        new_enum = new_status if isinstance(new_status, TaskStatus) else TaskStatus(new_status)
        
        allowed = _ALLOWED_TRANSITIONS.get(curr_enum, set())
        return new_enum in allowed

    async def transition(
        self,
        task_id: str,
        expected_status: TaskStatus | str,
        new_status: TaskStatus | str,
        reason: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> TaskTransitionResult:
        """
        Executes a validated, atomic state transition for task_id.
        Fails closed with typed exceptions on invalid matrix transition, missing task,
        or concurrent state modification.
        """
        exp_enum = expected_status if isinstance(expected_status, TaskStatus) else TaskStatus(expected_status)
        new_enum = new_status if isinstance(new_status, TaskStatus) else TaskStatus(new_status)

        # 1. Enforce transition matrix validity
        if not self.is_transition_allowed(exp_enum, new_enum):
            raise InvalidStateTransitionError(
                task_id=task_id,
                current_status=exp_enum.value,
                attempted_status=new_enum.value,
            )

        # Automatic timestamps for terminal / running states if not provided
        now = datetime.now(timezone.utc)
        if new_enum == TaskStatus.RUNNING and started_at is None:
            started_at = now
        elif new_enum in (TaskStatus.COMMITTED, TaskStatus.CANCELLED, TaskStatus.ABORTED) and completed_at is None:
            completed_at = now

        # 2. Atomic Compare-And-Set persistence query
        updated_task, success = await self._repo.transition_status_atomically(
            task_id=task_id,
            expected_status=exp_enum,
            new_status=new_enum,
            started_at=started_at,
            completed_at=completed_at,
            failure_reason=reason if new_enum in (TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.CANCELLED, TaskStatus.REPAIR_REQUIRED) else None,
        )

        if not success:
            # Re-fetch to distinguish between non-existent task and concurrent stale update
            current_task = await self._repo.get_task(task_id)
            if current_task is None:
                raise TaskNotFoundError(task_id=task_id)
            raise StaleStateTransitionError(
                task_id=task_id,
                expected_status=exp_enum.value,
                actual_status=current_task.status.value,
            )

        return TaskTransitionResult(
            success=True,
            task_id=task_id,
            previous_status=exp_enum,
            new_status=new_enum,
            transitioned_at=now,
            reason=reason,
            task=updated_task,
        )

    async def claim_attempt(
        self,
        task_id: str,
        expected_status: TaskStatus | str,
        max_attempts: int,
    ) -> TaskTransitionResult:
        """
        Atomically claim a development execution attempt for task_id.

        This is the authoritative TaskStateMachine entry point for the
        PENDING / REPAIR_REQUIRED → RUNNING transition when concurrent safety
        and attempt-count accuracy are both required.

        Architecture note
        -----------------
        TaskStateMachine remains the sole domain authority for task-status
        transitions.  The orchestrator MUST NOT call TaskRepository directly
        or mutate task status independently.  This method validates the
        domain-level transition matrix, then delegates to
        TaskRepository.claim_task_attempt() which issues ONE SQL UPDATE that
        simultaneously asserts:

            current status == expected_status
            AND current attempt_count < max_attempts

        and writes:

            status = RUNNING
            attempt_count = attempt_count + 1
            started_at = now

        Because both mutations occur in a single SQL statement there is no
        crash window between them and no losing concurrent worker can
        increment attempt_count.

        Raises
        ------
        InvalidStateTransitionError
            expected_status → RUNNING is not in the transition matrix.
        TaskNotFoundError
            task_id does not exist.
        StaleStateTransitionError
            Claim was lost — either a concurrent worker already claimed the
            task (status changed) or the attempt limit was already reached
            (attempt_count >= max_attempts).  The caller must re-fetch the
            task to determine the precise reason.
        """
        exp_enum = (
            expected_status
            if isinstance(expected_status, TaskStatus)
            else TaskStatus(expected_status)
        )

        # 1. Validate domain-level transition matrix before touching the DB.
        if not self.is_transition_allowed(exp_enum, TaskStatus.RUNNING):
            raise InvalidStateTransitionError(
                task_id=task_id,
                current_status=exp_enum.value,
                attempted_status=TaskStatus.RUNNING.value,
            )

        now = datetime.now(timezone.utc)

        # 2. Single atomic SQL UPDATE: assert status + assert count < max + set both.
        updated_task, success = await self._repo.claim_task_attempt(
            task_id=task_id,
            expected_status=exp_enum,
            max_attempts=max_attempts,
            started_at=now,
        )

        if not success:
            current_task = await self._repo.get_task(task_id)
            if current_task is None:
                raise TaskNotFoundError(task_id=task_id)
            raise StaleStateTransitionError(
                task_id=task_id,
                expected_status=exp_enum.value,
                actual_status=current_task.status.value,
            )

        return TaskTransitionResult(
            success=True,
            task_id=task_id,
            previous_status=exp_enum,
            new_status=TaskStatus.RUNNING,
            transitioned_at=now,
            reason=f"Atomic claim: attempt {updated_task.attempt_count}/{max_attempts}",
            task=updated_task,
        )

    async def transition_recovery(
        self,
        task_id: str,
        expected_recovery_owner: str,
        expected_last_recovery_at: datetime,
        new_status: TaskStatus | str,
        reason: str | None = None,
    ) -> TaskTransitionResult:
        """
        Executes an atomic, lease-guarded recovery state transition for task_id.
        Fails closed with StaleStateTransitionError if expected_recovery_owner or
        expected_last_recovery_at no longer match (e.g. recovery lease expired or took over).
        """
        new_enum = new_status if isinstance(new_status, TaskStatus) else TaskStatus(new_status)

        if not self.is_transition_allowed(TaskStatus.RUNNING, new_enum):
            raise InvalidStateTransitionError(
                task_id=task_id,
                current_status=TaskStatus.RUNNING.value,
                attempted_status=new_enum.value,
            )

        updated_task, success = await self._repo.transition_recovery_status_atomically(
            task_id=task_id,
            expected_recovery_owner=expected_recovery_owner,
            expected_last_recovery_at=expected_last_recovery_at,
            new_status=new_enum,
            failure_reason=reason if new_enum in (TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.CANCELLED, TaskStatus.REPAIR_REQUIRED) else None,
        )

        if not success:
            current_task = await self._repo.get_task(task_id)
            if current_task is None:
                raise TaskNotFoundError(task_id=task_id)
            raise StaleStateTransitionError(
                task_id=task_id,
                expected_status=TaskStatus.RUNNING.value,
                actual_status=current_task.status.value,
            )

        now = datetime.now(timezone.utc)
        return TaskTransitionResult(
            success=True,
            task_id=task_id,
            previous_status=TaskStatus.RUNNING,
            new_status=new_enum,
            transitioned_at=now,
            reason=reason,
            task=updated_task,
        )


