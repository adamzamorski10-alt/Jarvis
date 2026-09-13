"""
Autonomous Development Loop & Multi-Task Plan Execution Service.

Application-level owner for executing multi-task plan DAGs autonomously.

Architectural Invariants Enforced:
  1. TaskStateMachine & TaskRepository remain sole authorities for state & atomic claims.
  2. Task execution flows strictly through PlanExecutionService -> AutonomousRunController.
  3. VerificationGate & Stage 8.13 Repair Loop remain the single verification/repair authority.
  4. ToolExecutionService & PermissionEngine maintain sole tool/security authority.
  5. Zero direct filesystem writes, subprocess calls, Git operations, or state fabrication.
  6. Idempotent and resumable across process restarts.
  7. Fail-closed on missing dependencies, unrecoverable task failures, or invalid state.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.orchestration.exceptions import (
    MaxAttemptsExceededError,
    OrchestrationError,
    StaleTaskStateError,
    TaskNotStartableError,
)
from app.orchestration.run_controller import AutonomousRunResult, RunStatus
from app.repositories.task_repository import TaskRepository
from app.services.plan_execution_service import DependencyUnmetError, PlanExecutionService
from app.tasks.exceptions import StaleStateTransitionError, TaskNotFoundError
from app.tasks.models import DevelopmentTask, TaskStatus
from app.tools.audit_models import AuditEventType

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PlanExecutionState(str, Enum):
    """Terminal & lifecycle status for autonomous multi-task plan execution."""
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class PlanExecutionResult(BaseModel):
    """
    Structured, bounded outcome of an autonomous multi-task plan execution.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    plan_id: str
    terminal_state: PlanExecutionState
    completed_task_ids: list[str] = Field(default_factory=list)
    failed_task_ids: list[str] = Field(default_factory=list)
    cancelled_task_ids: list[str] = Field(default_factory=list)
    blocked_task_ids: list[str] = Field(default_factory=list)
    executed_task_ids: list[str] = Field(default_factory=list)

    total_tasks: int = 0
    started_tasks: int = 0
    committed_tasks: int = 0
    failed_tasks: int = 0

    reason: str | None = None
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None


class AutonomousPlanExecutor:
    """
    Multi-task autonomous development loop manager.

    Coordinates the execution of a persisted DAG of development tasks for a plan_id
    until all tasks are COMMITTED, a task terminally fails, cancellation occurs, or
    unresolvable blockage occurs.
    """

    def __init__(
        self,
        task_repository: TaskRepository,
        plan_execution_service: PlanExecutionService,
        audit_service: Any | None = None,
        max_loop_iterations: int = 1000,
    ) -> None:
        self._repo = task_repository
        self._execution_service = plan_execution_service
        self._audit_service = audit_service
        self._max_loop_iterations = max_loop_iterations

    async def execute_plan(
        self,
        plan_id: str,
        timeout_seconds: float | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> PlanExecutionResult:
        """
        Execute all runnable tasks for plan_id in DAG order until terminal completion.
        """
        started_at = _utc_now()
        plan_id_str = plan_id.strip()
        executed_task_ids: list[str] = []
        # Tracks consecutive yield iterations per task_id while waiting for an active worker.
        # After _MAX_ACTIVE_YIELDS consecutive yields with zero progress, we stop waiting.
        _MAX_ACTIVE_YIELDS = 2
        _active_yield_counts: dict[str, int] = {}

        await self._safe_audit(
            AuditEventType.PLAN_EXECUTION_STARTED,
            plan_id=plan_id_str,
            started_at=started_at.isoformat(),
        )

        iteration_count = 0
        while True:
            iteration_count += 1
            if iteration_count > self._max_loop_iterations:
                logger.error("Plan execution loop iteration limit (%d) exceeded for plan %s", self._max_loop_iterations, plan_id_str)
                return await self._finalize_result(
                    plan_id=plan_id_str,
                    terminal_state=PlanExecutionState.FAILED,
                    reason=f"Loop iteration limit of {self._max_loop_iterations} exceeded",
                    started_at=started_at,
                    executed_task_ids=executed_task_ids,
                )

            # Check cancellation signal before scheduling next step
            if cancel_event is not None and cancel_event.is_set():
                logger.info("Plan execution cancelled for plan %s", plan_id_str)
                return await self._finalize_result(
                    plan_id=plan_id_str,
                    terminal_state=PlanExecutionState.CANCELLED,
                    reason="Plan execution was cancelled",
                    started_at=started_at,
                    executed_task_ids=executed_task_ids,
                )

            # 1. Fetch current snapshot of plan tasks
            all_tasks = await self._repo.get_tasks_by_plan(plan_id_str)
            if not all_tasks:
                logger.info("Plan %s has no tasks, returning COMPLETED", plan_id_str)
                return await self._finalize_result(
                    plan_id=plan_id_str,
                    terminal_state=PlanExecutionState.COMPLETED,
                    reason="Plan contains zero tasks",
                    started_at=started_at,
                    executed_task_ids=executed_task_ids,
                )

            # 2. Analyze task states & dependency satisfaction
            committed_tasks = [t for t in all_tasks if t.status == TaskStatus.COMMITTED]
            failed_tasks = [t for t in all_tasks if t.status in (TaskStatus.FAILED, TaskStatus.ABORTED)]
            cancelled_tasks = [t for t in all_tasks if t.status == TaskStatus.CANCELLED]

            # If all tasks are COMMITTED -> Plan COMPLETED!
            if len(committed_tasks) == len(all_tasks):
                return await self._finalize_result(
                    plan_id=plan_id_str,
                    terminal_state=PlanExecutionState.COMPLETED,
                    reason="All tasks successfully completed and committed",
                    started_at=started_at,
                    executed_task_ids=executed_task_ids,
                )

            # If any task failed or aborted, default policy is stop plan execution
            if failed_tasks:
                failed_ids = [t.task_id for t in failed_tasks]
                logger.warning("Plan %s stopped due to failed tasks: %s", plan_id_str, failed_ids)
                return await self._finalize_result(
                    plan_id=plan_id_str,
                    terminal_state=PlanExecutionState.FAILED,
                    reason=f"Task(s) reached terminal failure state: {failed_ids}",
                    started_at=started_at,
                    executed_task_ids=executed_task_ids,
                )

            # 3. Discover candidate runnable tasks
            runnable_tasks: list[DevelopmentTask] = []
            for t in all_tasks:
                if t.status in (TaskStatus.PENDING, TaskStatus.REPAIR_REQUIRED):
                    if t.attempt_count < t.max_attempts:
                        satisfied, _ = await self._repo.are_dependencies_satisfied(t.task_id)
                        if satisfied:
                            runnable_tasks.append(t)

            # 4. If no tasks are runnable right now:
            if not runnable_tasks:
                uncommitted = [t for t in all_tasks if t.status != TaskStatus.COMMITTED]
                if uncommitted:
                    # If tasks are actively executing (RUNNING/VERIFYING/PASSED), they may be
                    # owned by a concurrent worker in the same event loop.  Yield control a
                    # bounded number of times to let the owning coroutine finish.  Once the
                    # yield budget is exhausted with no visible progress (no new COMMITs),
                    # declare the plan BLOCKED so the caller is not stuck forever.
                    active_states = (TaskStatus.RUNNING, TaskStatus.VERIFYING, TaskStatus.PASSED)
                    actively_executing = [t for t in uncommitted if t.status in active_states]
                    if actively_executing:
                        active_key = frozenset(t.task_id for t in actively_executing)
                        yield_count = _active_yield_counts.get(str(active_key), 0)
                        if yield_count < _MAX_ACTIVE_YIELDS:
                            _active_yield_counts[str(active_key)] = yield_count + 1
                            logger.debug(
                                "Plan %s: tasks %s actively executing; yielding (%d/%d)",
                                plan_id_str,
                                [t.task_id for t in actively_executing],
                                yield_count + 1,
                                _MAX_ACTIVE_YIELDS,
                            )
                            await asyncio.sleep(0)  # yield to concurrent coroutines
                            continue
                        # Budget exhausted with no progress — fall through to BLOCKED.
                        logger.warning(
                            "Plan %s: active-yield budget exhausted for tasks %s; declaring BLOCKED",
                            plan_id_str,
                            [t.task_id for t in actively_executing],
                        )

                    logger.warning(
                        "Plan %s is BLOCKED: uncommitted tasks exist but none are runnable: %s",
                        plan_id_str,
                        [t.task_id for t in uncommitted],
                    )
                    return await self._finalize_result(
                        plan_id=plan_id_str,
                        terminal_state=PlanExecutionState.BLOCKED,
                        reason="Uncommitted tasks exist but none are runnable due to unmet dependencies, active execution, or attempt exhaustion",
                        started_at=started_at,
                        executed_task_ids=executed_task_ids,
                    )


            # 5. Deterministic selection: runnable tasks are pre-sorted by (priority DESC, task_key ASC, task_id ASC)
            next_task = runnable_tasks[0]
            logger.info("Scheduling task %s (%s) for plan %s", next_task.task_id, next_task.task_key, plan_id_str)

            await self._safe_audit(
                AuditEventType.PLAN_TASK_SCHEDULED,
                plan_id=plan_id_str,
                task_id=next_task.task_id,
                task_key=next_task.task_key,
            )

            # 6. Execute task via existing PlanExecutionService path
            if next_task.task_id not in executed_task_ids:
                executed_task_ids.append(next_task.task_id)

            run_result: AutonomousRunResult | None = None
            try:
                run_result = await self._execution_service.execute_planned_task(
                    task_id=next_task.task_id,
                    timeout_seconds=timeout_seconds,
                )
            except (TaskNotStartableError, StaleStateTransitionError, StaleTaskStateError) as exc:
                logger.info("Task %s was not startable or claimed by another worker: %s", next_task.task_id, exc)
                continue
            except DependencyUnmetError as exc:
                logger.warning("Task %s execution rejected due to unmet dependencies: %s", next_task.task_id, exc)
                continue
            except MaxAttemptsExceededError as exc:
                logger.warning("Task %s exceeded max attempts: %s", next_task.task_id, exc)
                continue
            except Exception as exc:
                logger.exception("Unexpected error executing task %s in plan loop: %s", next_task.task_id, exc)
                # Check status from repository after crash/exception
                t_check = await self._repo.get_task(next_task.task_id)
                if t_check and t_check.status in (TaskStatus.FAILED, TaskStatus.ABORTED):
                    return await self._finalize_result(
                        plan_id=plan_id_str,
                        terminal_state=PlanExecutionState.FAILED,
                        reason=f"Task '{next_task.task_id}' failed with exception: {exc}",
                        started_at=started_at,
                        executed_task_ids=executed_task_ids,
                    )

            # 7. Audit & check execution result
            if hasattr(self._repo, "_session") and self._repo._session:
                try:
                    await self._repo._session.commit()
                except Exception:
                    pass

            if run_result:

                if run_result.status == RunStatus.SUCCEEDED and run_result.task_status == TaskStatus.COMMITTED:
                    await self._safe_audit(
                        AuditEventType.PLAN_TASK_COMPLETED,
                        plan_id=plan_id_str,
                        task_id=next_task.task_id,
                        commit_sha=run_result.commit_sha,
                    )
                    # Progress was made — clear the active-yield budget for all tasks.
                    _active_yield_counts.clear()
                elif run_result.status == RunStatus.CANCELLED or run_result.task_status == TaskStatus.CANCELLED:
                    logger.info("Task %s run was CANCELLED", next_task.task_id)
                    return await self._finalize_result(
                        plan_id=plan_id_str,
                        terminal_state=PlanExecutionState.CANCELLED,
                        reason=f"Task execution for '{next_task.task_id}' was cancelled",
                        started_at=started_at,
                        executed_task_ids=executed_task_ids,
                    )
                elif run_result.status in (RunStatus.FAILED, RunStatus.ABORTED, RunStatus.TIMED_OUT) or run_result.task_status in (TaskStatus.FAILED, TaskStatus.ABORTED):
                    await self._safe_audit(
                        AuditEventType.PLAN_TASK_FAILED,
                        plan_id=plan_id_str,
                        task_id=next_task.task_id,
                        failure_reason=run_result.failure_reason,
                    )
                    # Loop re-evaluation at next iteration top will detect FAILED task status and stop plan execution safely.

    async def _finalize_result(
        self,
        plan_id: str,
        terminal_state: PlanExecutionState,
        reason: str,
        started_at: datetime,
        executed_task_ids: list[str],
    ) -> PlanExecutionResult:
        completed_at = _utc_now()
        all_tasks = await self._repo.get_tasks_by_plan(plan_id)

        completed_ids = [t.task_id for t in all_tasks if t.status == TaskStatus.COMMITTED]
        failed_ids = [t.task_id for t in all_tasks if t.status in (TaskStatus.FAILED, TaskStatus.ABORTED)]
        cancelled_ids = [t.task_id for t in all_tasks if t.status == TaskStatus.CANCELLED]
        
        # Blocked tasks are uncommitted tasks whose dependencies are not all COMMITTED or attempt budget exhausted
        blocked_ids: list[str] = []
        for t in all_tasks:
            if t.status not in (TaskStatus.COMMITTED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.CANCELLED):
                sat, _ = await self._repo.are_dependencies_satisfied(t.task_id)
                if not sat or t.attempt_count >= t.max_attempts:
                    blocked_ids.append(t.task_id)

        started_count = len([t for t in all_tasks if t.started_at is not None or t.attempt_count > 0])

        audit_event_map = {
            PlanExecutionState.COMPLETED: AuditEventType.PLAN_EXECUTION_COMPLETED,
            PlanExecutionState.FAILED: AuditEventType.PLAN_EXECUTION_FAILED,
            PlanExecutionState.CANCELLED: AuditEventType.PLAN_EXECUTION_CANCELLED,
            PlanExecutionState.BLOCKED: AuditEventType.PLAN_EXECUTION_BLOCKED,
        }
        event_type = audit_event_map.get(terminal_state, AuditEventType.PLAN_EXECUTION_FAILED)
        await self._safe_audit(
            event_type,
            plan_id=plan_id,
            terminal_state=terminal_state.value,
            reason=reason,
            completed_tasks_count=len(completed_ids),
            total_tasks_count=len(all_tasks),
        )

        return PlanExecutionResult(
            plan_id=plan_id,
            terminal_state=terminal_state,
            completed_task_ids=completed_ids,
            failed_task_ids=failed_ids,
            cancelled_task_ids=cancelled_ids,
            blocked_task_ids=blocked_ids,
            executed_task_ids=executed_task_ids,
            total_tasks=len(all_tasks),
            started_tasks=started_count,
            committed_tasks=len(completed_ids),
            failed_tasks=len(failed_ids),
            reason=reason,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def _safe_audit(self, event_type: AuditEventType | str, **kwargs: Any) -> None:
        if self._audit_service is None:
            return
        try:
            log_fn = getattr(self._audit_service, "log_event", None)
            if callable(log_fn):
                res = log_fn(event_type=event_type, **kwargs)
                if asyncio.iscoroutine(res):
                    await res
        except Exception as exc:
            logger.warning("Failed to emit audit event %s: %s", event_type, exc)
