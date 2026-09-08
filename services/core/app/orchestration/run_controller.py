"""
Autonomous Run Controller.

High-level internal control layer for coordinating autonomous development runs.

Architectural Invariants Enforced:
  1. TaskStateMachine is the SOLE authority for task state transitions.
  2. TaskOrchestrator handles underlying task lifecycle execution.
  3. Controller is provider-neutral (no Gemini, Kilo Code, Ollama, OpenAI, Anthropic, HTTP clients).
  4. Durable single-run safety via TaskStateMachine / TaskRepository atomic claims.
  5. Mandatory verification & commit validation: success requires agent success, verification PASS, and verified commit SHA.
  6. Failure-closed timeout & cancellation handling.
  7. Zero direct access or imports of ToolExecutor, BaseTool, PermissionEngine, ApprovalService, subprocess, or Git.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.orchestration.exceptions import (
    MaxAttemptsExceededError,
    OrchestrationError,
    StaleTaskStateError,
    TaskNotStartableError,
)
from app.orchestration.models import (
    OrchestrationOptions,
    OrchestrationResult,
)
from app.tasks.exceptions import StaleStateTransitionError, TaskNotFoundError
from app.tasks.models import TaskStatus
from app.verification.models import GateDecision

if TYPE_CHECKING:
    from app.orchestration.orchestrator import AutonomousTaskOrchestrator
    from app.orchestration.recovery_manager import RecoveryManager
    from app.verification.gate import VerificationGate
    from app.workspace.workspace_manager import WorkspaceManager

from app.repositories.task_repository import TaskRepository
from app.tasks.state_machine import TaskStateMachine

from app.db.models.development_task import DevelopmentTaskModel

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)



class RunStatus(str, Enum):
    """Lifecycle status for an autonomous run managed by AutonomousRunController."""
    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ABORTED = "ABORTED"
    TIMED_OUT = "TIMED_OUT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class AutonomousRunResult(BaseModel):
    """
    Structured outcome of an autonomous controller run.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    run_id: str
    task_id: str
    status: RunStatus
    task_status: TaskStatus
    attempt_count: int = 0
    verification_passed: bool = False
    commit_sha: str | None = None
    failure_reason: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    recovery_required: bool = False
    orchestration_result: OrchestrationResult | None = None


class RunControllerError(OrchestrationError):
    """Base exception for AutonomousRunController operations."""
    pass


class RunTimeoutError(RunControllerError):
    """Raised when an autonomous run exceeds its configured execution timeout."""
    def __init__(self, run_id: str, task_id: str, timeout_seconds: float) -> None:
        self.run_id = run_id
        self.task_id = task_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Autonomous run '{run_id}' for task '{task_id}' timed out after {timeout_seconds}s"
        )


class AutonomousRunController:
    """
    Internal control layer for coordinating autonomous development runs.
    """

    def __init__(
        self,
        task_repo: TaskRepository,
        state_machine: TaskStateMachine,
        workspace_manager: WorkspaceManager,
        verification_gate: VerificationGate,
        orchestrator: AutonomousTaskOrchestrator,
        recovery_manager: RecoveryManager | None = None,
        default_timeout_seconds: float = 300.0,
    ) -> None:
        if not math.isfinite(default_timeout_seconds) or default_timeout_seconds <= 0:
            raise ValueError(f"default_timeout_seconds must be a positive finite float, got {default_timeout_seconds!r}")

        self._task_repo = task_repo
        self._state_machine = state_machine
        self._workspace_manager = workspace_manager
        self._verification_gate = verification_gate
        self._orchestrator = orchestrator
        self._recovery_manager = recovery_manager
        self._default_timeout_seconds = float(default_timeout_seconds)
        self._active_cancel_events: dict[str, asyncio.Event] = {}
        self._active_abort_events: dict[str, asyncio.Event] = {}
        self._active_executions: dict[str, asyncio.Task] = {}

    def _create_fresh_session(self) -> AsyncSession:
        session = self._task_repo._session
        bind = getattr(session, "bind", None)
        if bind is None:
            raise RuntimeError("Cannot create fresh session: current session has no bind")
        factory = async_sessionmaker(bind, class_=AsyncSession, expire_on_commit=False)
        return factory()

    @property
    def default_timeout_seconds(self) -> float:
        return self._default_timeout_seconds

    async def start_run(
        self,
        task_id: str,
        timeout_seconds: float | None = None,
        options: OrchestrationOptions | None = None,
    ) -> AutonomousRunResult:
        """
        Start and coordinate an autonomous run for the given task_id.
        """
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        started_at = _utc_now()
        effective_timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout_seconds
        if not math.isfinite(effective_timeout) or effective_timeout <= 0:
            raise ValueError(f"timeout_seconds must be a positive finite float, got {effective_timeout!r}")

        # 1. Fetch task
        task = await self._task_repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)
        curr_status = task.status
        curr_attempts = task.attempt_count

        # 2. Check task startability & terminal protection
        if task.status in (TaskStatus.COMMITTED, TaskStatus.CANCELLED):
            raise TaskNotStartableError(
                task_id=task_id,
                status=task.status.value,
                reason=f"Task is in terminal status '{task.status.value}' and cannot be run",
            )

        if task.status == TaskStatus.RUNNING:
            # Check staleness if recovery manager is available
            stale_timeout = (
                self._recovery_manager.timeout_seconds if self._recovery_manager else effective_timeout
            )
            stale_before = started_at - timedelta(seconds=stale_timeout)
            last_hb = _ensure_utc(task.last_heartbeat)
            is_stale = last_hb is not None and last_hb < stale_before
            if is_stale:
                return AutonomousRunResult(
                    run_id=run_id,
                    task_id=task_id,
                    status=RunStatus.RECOVERY_REQUIRED,
                    task_status=TaskStatus.RUNNING,
                    attempt_count=task.attempt_count,
                    verification_passed=False,
                    failure_reason="Task heartbeat is stale; recovery is required before execution",
                    started_at=started_at,
                    completed_at=_utc_now(),
                    recovery_required=True,
                )
            raise TaskNotStartableError(
                task_id=task_id,
                status=task.status.value,
                reason="Task is already RUNNING",
            )

        # 3. Delegate task execution to TaskOrchestrator under bounded execution timeout
        opts = options or OrchestrationOptions()
        cancel_event = asyncio.Event()
        abort_event = asyncio.Event()
        self._active_cancel_events[task_id] = cancel_event
        self._active_abort_events[task_id] = abort_event

        orch_task = asyncio.create_task(
            self._orchestrator.start_task(
                task_id,
                options=options,
                cancel_event=cancel_event,
                abort_event=abort_event,
            )
        )
        self._active_executions[task_id] = orch_task
        orch_result = None
        sleep_task: asyncio.Task | None = None
        termination_grace_period = min(15.0, max(2.0, effective_timeout * 0.5))
        try:
            sleep_task = asyncio.create_task(asyncio.sleep(effective_timeout))
            try:
                done, pending = await asyncio.wait(
                    [orch_task, sleep_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                if sleep_task is not None and not sleep_task.done():
                    sleep_task.cancel()
                    try:
                        await sleep_task
                    except asyncio.CancelledError:
                        pass
                raise

            if orch_task in done:
                orch_result = orch_task.result()
                logger.info("start_run: orch_task completed first with status %s", orch_result.final_status if orch_result else "None")
            else:
                sleep_task.cancel()
                try:
                    await sleep_task
                except asyncio.CancelledError:
                    pass
                logger.warning("Autonomous run %s for task %s timed out after %ss", run_id, task_id, effective_timeout)
                orch_task.cancel()
                completed_at = _utc_now()
                logger.info("start_run: timeout branch, cancelling orch_task")

                terminated = await self._terminate_execution(orch_task, task_id, termination_grace_period)
                logger.info("start_run: termination result=%s", terminated)

                try:
                    await self._task_repo._session.rollback()
                except Exception:
                    pass

                current_task_status = TaskStatus.RUNNING
                current_attempts = task.attempt_count
                max_attempts = task.max_attempts
                fresh_task = None
                try:
                    async with self._create_fresh_session() as timeout_session:
                        timeout_repo = TaskRepository(timeout_session)
                        fresh_task = await timeout_repo.get_task(task_id)
                        if fresh_task:
                            current_task_status = fresh_task.status
                            current_attempts = fresh_task.attempt_count
                            max_attempts = fresh_task.max_attempts
                except Exception as exc:
                    logger.warning("Failed to re-fetch task %s after timeout: %s", task_id, exc)

                if terminated and current_task_status == TaskStatus.RUNNING:
                    target_status = (
                        TaskStatus.REPAIR_REQUIRED if current_attempts < max_attempts else TaskStatus.FAILED
                    )
                    if self._state_machine.is_transition_allowed(TaskStatus.RUNNING, target_status):
                        try:
                            async with self._create_fresh_session() as timeout_session:
                                timeout_repo = TaskRepository(timeout_session)
                                timeout_sm = TaskStateMachine(timeout_repo)
                                await timeout_sm.transition(
                                    task_id=task_id,
                                    expected_status=TaskStatus.RUNNING,
                                    new_status=target_status,
                                    reason=f"Autonomous run timed out after {effective_timeout}s",
                                )
                                await timeout_session.commit()
                            current_task_status = target_status
                        except Exception as exc:
                            logger.warning("Failed to transition timed-out task %s: %s", task_id, exc)

                if current_task_status == TaskStatus.COMMITTED:
                    return AutonomousRunResult(
                        run_id=run_id,
                        task_id=task_id,
                        status=RunStatus.SUCCEEDED,
                        task_status=TaskStatus.COMMITTED,
                        attempt_count=current_attempts,
                        verification_passed=True,
                        commit_sha=fresh_task.metadata.get("commit_sha") if fresh_task else None,
                        failure_reason=None,
                        started_at=started_at,
                        completed_at=completed_at,
                        recovery_required=False,
                    )

                if current_task_status in (TaskStatus.CANCELLED, TaskStatus.ABORTED):
                    run_stat = RunStatus.CANCELLED if current_task_status == TaskStatus.CANCELLED else RunStatus.ABORTED
                    return AutonomousRunResult(
                        run_id=run_id,
                        task_id=task_id,
                        status=run_stat,
                        task_status=current_task_status,
                        attempt_count=current_attempts,
                        verification_passed=False,
                        failure_reason=f"Autonomous run timed out after {effective_timeout}s",
                        started_at=started_at,
                        completed_at=completed_at,
                        recovery_required=True,
                    )

                if not terminated:
                    try:
                        async with self._create_fresh_session() as cleanup_session:
                            cleanup_repo = TaskRepository(cleanup_session)
                            await cleanup_repo.update_workspace_cleanup_pending(task_id, True)
                            await cleanup_session.commit()
                    except Exception as exc:
                        logger.warning("Failed to set cleanup_pending for timed-out task %s: %s", task_id, exc)

                if opts.auto_cleanup_workspace and terminated and current_task_status not in (TaskStatus.COMMITTED, TaskStatus.CANCELLED, TaskStatus.ABORTED):
                    await self._confirm_execution_terminated(task_id)
                    await self._safe_remove_workspace(task_id, execution_task=orch_task)

                logger.info("start_run: timeout branch returning TIMED_OUT, terminated=%s, current_task_status=%s", terminated, current_task_status)
                return AutonomousRunResult(
                    run_id=run_id,
                    task_id=task_id,
                    status=RunStatus.TIMED_OUT,
                    task_status=current_task_status,
                    attempt_count=current_attempts,
                    verification_passed=False,
                    failure_reason=f"Autonomous run timed out after {effective_timeout}s",
                    started_at=started_at,
                    completed_at=completed_at,
                    recovery_required=not terminated,
                )
        except asyncio.CancelledError:
            if orch_task.cancelled():
                completed_at = _utc_now()
                try:
                    await self._task_repo._session.rollback()
                except Exception:
                    pass
                fresh_task = await self._task_repo.get_task(task_id)
                current_task_status = fresh_task.status if fresh_task else TaskStatus.RUNNING
                current_attempts = fresh_task.attempt_count if fresh_task else task.attempt_count
                run_stat = RunStatus.CANCELLED if current_task_status == TaskStatus.CANCELLED else RunStatus.ABORTED if current_task_status == TaskStatus.ABORTED else RunStatus.FAILED
                return AutonomousRunResult(
                    run_id=run_id,
                    task_id=task_id,
                    status=run_stat,
                    task_status=current_task_status,
                    attempt_count=current_attempts,
                    verification_passed=False,
                    failure_reason="Run was cancelled",
                    started_at=started_at,
                    completed_at=completed_at,
                    recovery_required=True,
                )
            raise
        except (TaskNotStartableError, MaxAttemptsExceededError, StaleTaskStateError, StaleStateTransitionError) as exc:
            curr_status = task.status
            curr_attempts = task.attempt_count
            try:
                await self._task_repo._session.rollback()
            except Exception:
                pass
            try:
                session_factory = async_sessionmaker(bind=self._task_repo._session.bind, expire_on_commit=False)
                async with session_factory() as new_session:
                    raw = await new_session.execute(
                        select(DevelopmentTaskModel.status, DevelopmentTaskModel.attempt_count).where(DevelopmentTaskModel.task_id == task_id)
                    )
                    raw_row = raw.one_or_none()
                    if raw_row:
                        curr_status = TaskStatus(raw_row[0])
                        curr_attempts = raw_row[1]
            except Exception:
                pass
            try:
                await self._task_repo._session.commit()
            except Exception:
                pass
            return AutonomousRunResult(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.FAILED,
                task_status=curr_status,
                attempt_count=curr_attempts,
                verification_passed=False,
                failure_reason=str(exc),
                started_at=started_at,
                completed_at=_utc_now(),
            )
        except Exception as exc:
            logger.error("Unexpected error in run controller for task %s: %s", task_id, exc)
            try:
                fresh_task = await self._task_repo.get_task(task_id)
            except Exception:
                fresh_task = None
            curr_status = fresh_task.status if fresh_task else task.status
            curr_attempts = fresh_task.attempt_count if fresh_task else task.attempt_count
            try:
                await self._task_repo._session.commit()
            except Exception:
                pass
            return AutonomousRunResult(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.FAILED,
                task_status=curr_status,
                attempt_count=curr_attempts,
                verification_passed=False,
                failure_reason=f"Controller execution failed: {exc}",
                started_at=started_at,
                completed_at=_utc_now(),
            )
        finally:
            self._active_executions.pop(task_id, None)
            self._active_cancel_events.pop(task_id, None)
            self._active_abort_events.pop(task_id, None)

        # 4. Process OrchestrationResult & verify mandatory success conditions
        completed_at = _utc_now()
        verification_passed = False
        if orch_result.verification_report is not None:
            verification_passed = (
                orch_result.verification_report.gate_decision == GateDecision.PASS
                or getattr(orch_result.verification_report, "passed", False)
            )

        is_succeeded = (
            orch_result.final_status == TaskStatus.COMMITTED
            and orch_result.committed
            and verification_passed
            and orch_result.checkpoint_sha is not None
            and (orch_result.agent_result is None or orch_result.agent_result.is_success)
        )

        if is_succeeded:
            run_status = RunStatus.SUCCEEDED
        elif orch_result.final_status == TaskStatus.CANCELLED:
            run_status = RunStatus.CANCELLED
        elif orch_result.final_status == TaskStatus.ABORTED:
            run_status = RunStatus.ABORTED
        else:
            run_status = RunStatus.FAILED

        try:
            if hasattr(self._task_repo, "_session") and self._task_repo._session:
                await self._task_repo._session.commit()
        except Exception as exc:
            logger.warning("Failed to commit session in start_run: %s", exc)

        logger.info("start_run: success path returning %s", run_status)
        return AutonomousRunResult(
            run_id=run_id,
            task_id=task_id,
            status=run_status,
            task_status=orch_result.final_status,
            attempt_count=orch_result.attempt_count,
            verification_passed=verification_passed,
            commit_sha=orch_result.checkpoint_sha,
            failure_reason=orch_result.error_message,
            started_at=started_at,
            completed_at=completed_at,
            recovery_required=False,
            orchestration_result=orch_result,
        )

    async def _terminate_execution(
        self,
        execution_task: asyncio.Task,
        task_id: str,
        grace_period: float = 10.0,
    ) -> bool:
        """
        Actively cancel an execution task and enforce a hard deadline for termination.

        Uses asyncio.shield to prevent the inner task from consuming the cancellation
        and continuing. The grace_period is a HARD deadline - if the task does not
        complete within grace_period seconds, we return False regardless of whether
        the task catches cancellation.

        Returns:
            True if the execution task completed (terminated).
            False if the grace period elapsed without confirmed termination.
        """
        if execution_task is None or execution_task.done():
            return True

        execution_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(execution_task), timeout=grace_period)
        except asyncio.TimeoutError:
            logger.warning(
                "Execution task for task %s did not terminate within %ss grace period",
                task_id,
                grace_period,
            )
            return False
        except asyncio.CancelledError:
            if execution_task.done():
                return True
            raise
        else:
            return True

    async def _confirm_execution_terminated(self, task_id: str) -> None:
        """
        Record durable proof that the execution for task_id is terminated.
        Tries the main session first (after rollback), then falls back to a fresh session.
        """
        try:
            try:
                await self._task_repo._session.rollback()
            except Exception:
                pass
            try:
                self._task_repo._session.expunge_all()
            except Exception:
                pass
            await self._task_repo.confirm_execution_terminated(task_id)
            await self._task_repo._session.commit()
            return
        except Exception as main_exc:
            pass

        try:
            async with self._create_fresh_session() as fresh_session:
                fresh_repo = TaskRepository(fresh_session)
                await fresh_repo.confirm_execution_terminated(task_id)
                await fresh_session.commit()
        except Exception as exc:
            logger.warning("Failed to record execution_terminated_at for task %s: %s", task_id, exc)

    async def cancel_run(
        self,
        task_id: str,
        reason: str | None = None,
    ) -> AutonomousRunResult:
        """
        Request explicit internal cancellation of a task run.
        Idempotent: safe to invoke repeatedly.
        """
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        now = _utc_now()

        task = await self._task_repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)

        if task.status == TaskStatus.CANCELLED:
            return AutonomousRunResult(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.CANCELLED,
                task_status=TaskStatus.CANCELLED,
                attempt_count=task.attempt_count,
                verification_passed=False,
                failure_reason=task.failure_reason or reason or "Task already CANCELLED",
                started_at=task.started_at or now,
                completed_at=now,
            )

        if task.status == TaskStatus.COMMITTED:
            return AutonomousRunResult(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.FAILED,
                task_status=TaskStatus.COMMITTED,
                attempt_count=task.attempt_count,
                verification_passed=True,
                failure_reason="Cannot cancel a COMMITTED task",
                started_at=task.started_at or now,
                completed_at=now,
            )

        final_task_status = task.status
        attempts = task.attempt_count
        execution_token = task.execution_token
        try:
            async with self._create_fresh_session() as fresh_session:
                fresh_repo = TaskRepository(fresh_session)
                fresh_sm = TaskStateMachine(fresh_repo)
                await fresh_sm.transition(
                    task_id=task_id,
                    expected_status=task.status,
                    new_status=TaskStatus.CANCELLED,
                    reason=reason or "Cancellation requested via AutonomousRunController",
                )
                await fresh_session.commit()
                final_task_status = TaskStatus.CANCELLED
                attempts = task.attempt_count
                fresh = await fresh_repo.get_task(task_id)
                if fresh:
                    execution_token = fresh.execution_token
        except Exception as exc:
            logger.warning("cancel_run transition failed for task %s: %s", task_id, exc)
            try:
                fresh_task = await self._task_repo.get_task(task_id)
                final_task_status = fresh_task.status if fresh_task else task.status
                attempts = fresh_task.attempt_count if fresh_task else task.attempt_count
                if fresh_task:
                    execution_token = fresh_task.execution_token
            except Exception:
                pass

        self._signal_cancel(task_id)

        active_task = self._active_executions.get(task_id)
        terminated = await self._terminate_execution(active_task, task_id)

        if terminated and final_task_status == TaskStatus.CANCELLED:
            await self._confirm_execution_terminated(task_id)
            await self._safe_remove_workspace(task_id, execution_task=active_task, expected_execution_token=execution_token)
        elif not terminated:
            await self._task_repo.update_workspace_cleanup_pending(task_id, True)
            try:
                await self._task_repo._session.commit()
            except Exception:
                pass

        run_stat = RunStatus.CANCELLED if final_task_status == TaskStatus.CANCELLED else RunStatus.FAILED
        return AutonomousRunResult(
            run_id=run_id,
            task_id=task_id,
            status=run_stat,
            task_status=final_task_status,
            attempt_count=attempts,
            verification_passed=False,
            failure_reason=reason or "Cancellation requested",
            started_at=task.started_at or now,
            completed_at=now,
        )

    async def abort_run(
        self,
        task_id: str,
        reason: str | None = None,
    ) -> AutonomousRunResult:
        """
        Request explicit internal abort of a task run.
        Idempotent: safe to invoke repeatedly.
        """
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        now = _utc_now()

        task = await self._task_repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)

        if task.status == TaskStatus.ABORTED:
            return AutonomousRunResult(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.ABORTED,
                task_status=TaskStatus.ABORTED,
                attempt_count=task.attempt_count,
                verification_passed=False,
                failure_reason=task.failure_reason or reason or "Task already ABORTED",
                started_at=task.started_at or now,
                completed_at=now,
            )

        if task.status in (TaskStatus.COMMITTED, TaskStatus.CANCELLED):
            return AutonomousRunResult(
                run_id=run_id,
                task_id=task_id,
                status=RunStatus.FAILED,
                task_status=task.status,
                attempt_count=task.attempt_count,
                verification_passed=task.status == TaskStatus.COMMITTED,
                failure_reason=f"Cannot abort task in terminal status '{task.status.value}'",
                started_at=task.started_at or now,
                completed_at=now,
            )

        final_task_status = task.status
        attempts = task.attempt_count
        execution_token = task.execution_token
        try:
            async with self._create_fresh_session() as fresh_session:
                fresh_repo = TaskRepository(fresh_session)
                fresh_sm = TaskStateMachine(fresh_repo)
                await fresh_sm.transition(
                    task_id=task_id,
                    expected_status=task.status,
                    new_status=TaskStatus.ABORTED,
                    reason=reason or "Abort requested via AutonomousRunController",
                )
                await fresh_session.commit()
                final_task_status = TaskStatus.ABORTED
                attempts = task.attempt_count
                fresh = await fresh_repo.get_task(task_id)
                if fresh:
                    execution_token = fresh.execution_token
        except Exception as exc:
            logger.warning("abort_run transition failed for task %s: %s", task_id, exc)
            try:
                fresh_task = await self._task_repo.get_task(task_id)
                final_task_status = fresh_task.status if fresh_task else task.status
                attempts = fresh_task.attempt_count if fresh_task else task.attempt_count
                if fresh_task:
                    execution_token = fresh_task.execution_token
            except Exception:
                pass

        self._signal_abort(task_id)

        active_task = self._active_executions.get(task_id)
        terminated = await self._terminate_execution(active_task, task_id)

        if terminated and final_task_status == TaskStatus.ABORTED:
            await self._confirm_execution_terminated(task_id)
            await self._safe_remove_workspace(task_id, execution_task=active_task, expected_execution_token=execution_token)
        elif not terminated:
            await self._task_repo.update_workspace_cleanup_pending(task_id, True)
            try:
                await self._task_repo._session.commit()
            except Exception:
                pass

        run_stat = RunStatus.ABORTED if final_task_status == TaskStatus.ABORTED else RunStatus.FAILED
        return AutonomousRunResult(
            run_id=run_id,
            task_id=task_id,
            status=run_stat,
            task_status=final_task_status,
            attempt_count=attempts,
            verification_passed=False,
            failure_reason=reason or "Abort requested",
            started_at=task.started_at or now,
            completed_at=now,
        )

    async def get_run_status(self, task_id: str) -> AutonomousRunResult:
        """
        Retrieve structured run status for task_id based on durable database state.
        """
        task = await self._task_repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)

        now = _utc_now()
        # Map TaskStatus to RunStatus
        status_map = {
            TaskStatus.PENDING: RunStatus.CREATED,
            TaskStatus.RUNNING: RunStatus.RUNNING,
            TaskStatus.VERIFYING: RunStatus.VERIFYING,
            TaskStatus.PASSED: RunStatus.VERIFYING,
            TaskStatus.REPAIR_REQUIRED: RunStatus.FAILED,
            TaskStatus.COMMITTED: RunStatus.SUCCEEDED,
            TaskStatus.PAUSED: RunStatus.RUNNING,
            TaskStatus.FAILED: RunStatus.FAILED,
            TaskStatus.CANCELLED: RunStatus.CANCELLED,
            TaskStatus.ABORTED: RunStatus.ABORTED,
        }
        run_status = status_map.get(task.status, RunStatus.FAILED)

        return AutonomousRunResult(
            run_id=f"run_status_{task_id}",
            task_id=task_id,
            status=run_status,
            task_status=task.status,
            attempt_count=task.attempt_count,
            verification_passed=task.status == TaskStatus.COMMITTED,
            commit_sha=task.metadata.get("commit_sha"),
            failure_reason=task.failure_reason,
            started_at=task.started_at or now,
            completed_at=task.completed_at,
        )

    def _signal_cancel(self, task_id: str) -> None:
        """Signal cancellation to an active run if present."""
        event = self._active_cancel_events.get(task_id)
        if event is not None and not event.is_set():
            event.set()

    def _signal_abort(self, task_id: str) -> None:
        """Signal abort to an active run if present."""
        event = self._active_abort_events.get(task_id)
        if event is not None and not event.is_set():
            event.set()

    async def _safe_remove_workspace(
        self,
        task_id: str,
        execution_task: asyncio.Task | None = None,
        expected_execution_token: str | None = None,
    ) -> None:
        """Best-effort workspace cleanup via WorkspaceManager.

        Refuses to remove a workspace that is still owned by an active execution.
        Uses the durable workspace_path stored on the task record.
        FAIL-CLOSED: ownership verification is enforced when token is provided.

        SAFETY INVARIANT: cleanup is ONLY permitted when durable DB evidence
        proves the execution is terminated (execution_terminated_at is set).
        """
        if execution_task is not None and not execution_task.done():
            logger.info(
                "Skipping workspace cleanup for task %s: execution task is still running",
                task_id,
            )
            return

        try:
            task = await self._task_repo.get_task(task_id)
            if task is None:
                logger.warning("Skipping workspace cleanup for task %s: task not found", task_id)
                return

            if task.execution_token is not None and task.execution_terminated_at is None:
                logger.info(
                    "Skipping workspace cleanup for task %s: execution token %r is still active "
                    "and execution_terminated_at is not set — execution may still be alive",
                    task_id,
                    task.execution_token,
                )
                return

            workspace_path = task.workspace_path if task.workspace_path else None
            execution_token = expected_execution_token or task.execution_token

            target_path = Path(workspace_path) if workspace_path else None
            if target_path is None:
                base_dir = self._workspace_manager.get_worktree_base_dir()
                target_path = base_dir / task_id

            if target_path and target_path.exists():
                from app.workspace.models import WorkspaceInfo, WorkspaceStatus

                registered = self._workspace_manager.list_registered_worktrees()
                branch_name = None
                for wt in registered:
                    if "path" in wt and Path(wt["path"]).resolve() == target_path.resolve():
                        branch_name = wt.get("branch", "")
                        break

                if branch_name is None:
                    branch_name = f"ai-operator/task/{task_id}"

                dummy_info = WorkspaceInfo(
                    task_id=task_id,
                    repository_root=str(self._workspace_manager.get_repository_root()),
                    primary_worktree_path=str(self._workspace_manager.get_primary_worktree_path()),
                    autonomous_worktree_path=str(target_path),
                    branch_name=branch_name,
                    base_commit_sha="0000000000000000000000000000000000000000",
                    created_at=_utc_now(),
                    current_commit_sha="0000000000000000000000000000000000000000",
                    status=WorkspaceStatus.DIRTY,
                )
                self._workspace_manager.remove_workspace(
                    task_id,
                    dummy_info,
                    force=True,
                    expected_execution_token=execution_token,
                )
        except Exception as exc:
            logger.warning("Controller safe workspace cleanup for task %s ignored exception: %s", task_id, exc)
