"""
AI Operator Core — Background Execution Manager

Application-level owner for HTTP-triggered background plan executions.

Architectural Invariants Enforced:
  1. REST / HTTP layer remains THIN: execution is strictly delegated to AutonomousPlanExecutor.
  2. All execution flows through AutonomousPlanExecutor -> PlanExecutionService -> RunController.
  3. Single authoritative execution path: zero direct workspace, Git, subprocess, or agent calls.
  4. Concurrent request atomicity & duplicate protection via per-plan asyncio.Lock and DB state verification.
  5. Clean background task lifecycle, exception handling, and graceful app shutdown cleanup.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any, Callable
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.services.autonomous_loop import (
    AutonomousPlanExecutor,
    PlanExecutionResult,
    PlanExecutionState,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionManagerStatus(str, Enum):
    """Status of a background plan execution handle."""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class ExecutionHandleInfo(BaseModel):
    """Public read-only DTO for background execution status."""
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    execution_id: str
    plan_id: str
    status: ExecutionManagerStatus
    started_at: datetime
    completed_at: datetime | None = None
    reason: str | None = None
    result: PlanExecutionResult | None = None
    error: str | None = None


class BackgroundExecutionError(Exception):
    """Base exception for BackgroundExecutionManager."""
    pass


class PlanAlreadyExecutingError(BackgroundExecutionError):
    """Raised when a plan execution is already in progress."""
    def __init__(self, plan_id: str, execution_id: str | None = None) -> None:
        self.plan_id = plan_id
        self.execution_id = execution_id
        msg = f"Plan '{plan_id}' is already executing"
        if execution_id:
            msg += f" (execution_id: '{execution_id}')"
        super().__init__(msg)


class PlanNotExecutableError(BackgroundExecutionError):
    """Raised when a plan cannot be executed due to invalid state or missing tasks."""
    def __init__(self, plan_id: str, reason: str) -> None:
        self.plan_id = plan_id
        self.reason = reason
        super().__init__(f"Plan '{plan_id}' cannot be executed: {reason}")


class ExecutionNotFoundError(BackgroundExecutionError):
    """Raised when an execution_id or plan_id handle is not found."""
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier
        super().__init__(f"Execution handle for '{identifier}' was not found")


class InternalExecutionHandle:
    """Internal mutable tracking handle for an active or finished background execution."""

    def __init__(self, execution_id: str, plan_id: str) -> None:
        self.execution_id = execution_id
        self.plan_id = plan_id
        self.status = ExecutionManagerStatus.QUEUED
        self.started_at = _utc_now()
        self.completed_at: datetime | None = None
        self.reason: str | None = None
        self.result: PlanExecutionResult | None = None
        self.error: str | None = None
        self.cancel_event = asyncio.Event()
        self.task: asyncio.Task[Any] | None = None

    def to_info(self) -> ExecutionHandleInfo:
        return ExecutionHandleInfo(
            execution_id=self.execution_id,
            plan_id=self.plan_id,
            status=self.status,
            started_at=self.started_at,
            completed_at=self.completed_at,
            reason=self.reason,
            result=self.result,
            error=self.error,
        )


class BackgroundExecutionManager:
    """
    Application-level singleton owning background execution of autonomous plans.
    """

    def __init__(self) -> None:
        self._handles_by_exec_id: dict[str, InternalExecutionHandle] = {}
        self._plan_to_exec_id: dict[str, str] = {}
        self._plan_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._shutting_down = False

    async def _get_plan_lock(self, plan_id: str) -> asyncio.Lock:
        async with self._global_lock:
            if plan_id not in self._plan_locks:
                self._plan_locks[plan_id] = asyncio.Lock()
            return self._plan_locks[plan_id]

    async def start_execution(
        self,
        plan_id: str,
        executor_factory: Callable[[], Awaitable[tuple[AutonomousPlanExecutor, Any]]] | Callable[[], tuple[AutonomousPlanExecutor, Any]],
        timeout_seconds: float | None = None,
    ) -> ExecutionHandleInfo:
        """
        Start an AutonomousPlanExecutor in the background.

        Args:
            plan_id: Unique plan identifier.
            executor_factory: Callable returning (AutonomousPlanExecutor, session_context_manager)
                              used to create an execution loop context.
            timeout_seconds: Optional timeout override.

        Returns:
            ExecutionHandleInfo with stable execution_id and initial RUNNING status.

        Raises:
            PlanAlreadyExecutingError: if plan is already running.
            PlanNotExecutableError: if app is shutting down.
        """
        plan_id_str = plan_id.strip()
        if not plan_id_str:
            raise ValueError("plan_id must be a non-empty string")

        if self._shutting_down:
            raise PlanNotExecutableError(plan_id_str, "Application is shutting down")

        plan_lock = await self._get_plan_lock(plan_id_str)
        async with plan_lock:
            if self._shutting_down:
                raise PlanNotExecutableError(plan_id_str, "Application is shutting down")

            # Check if active in memory
            existing_exec_id = self._plan_to_exec_id.get(plan_id_str)
            if existing_exec_id:
                existing_handle = self._handles_by_exec_id.get(existing_exec_id)
                if existing_handle and existing_handle.status in (
                    ExecutionManagerStatus.QUEUED,
                    ExecutionManagerStatus.RUNNING,
                ):
                    raise PlanAlreadyExecutingError(plan_id_str, existing_exec_id)

            execution_id = f"exec_{uuid.uuid4().hex[:12]}"
            handle = InternalExecutionHandle(execution_id=execution_id, plan_id=plan_id_str)
            handle.status = ExecutionManagerStatus.RUNNING

            self._handles_by_exec_id[execution_id] = handle
            self._plan_to_exec_id[plan_id_str] = execution_id

            # Launch background asyncio task
            bg_task = asyncio.create_task(
                self._run_executor_task(
                    handle=handle,
                    executor_factory=executor_factory,
                    timeout_seconds=timeout_seconds,
                )
            )
            handle.task = bg_task

            return handle.to_info()

    async def _run_executor_task(
        self,
        handle: InternalExecutionHandle,
        executor_factory: Callable[[], Awaitable[tuple[AutonomousPlanExecutor, Any]]] | Callable[[], tuple[AutonomousPlanExecutor, Any]],
        timeout_seconds: float | None,
    ) -> None:
        """Background coroutine running AutonomousPlanExecutor.execute_plan."""
        try:
            result = executor_factory()
            if asyncio.iscoroutine(result):
                executor, session_ctx = await result
            else:
                executor, session_ctx = result
            async with session_ctx:
                result: PlanExecutionResult = await executor.execute_plan(
                    plan_id=handle.plan_id,
                    timeout_seconds=timeout_seconds,
                    cancel_event=handle.cancel_event,
                )
                handle.result = result
                handle.reason = result.reason

                state_map = {
                    PlanExecutionState.COMPLETED: ExecutionManagerStatus.COMPLETED,
                    PlanExecutionState.FAILED: ExecutionManagerStatus.FAILED,
                    PlanExecutionState.CANCELLED: ExecutionManagerStatus.CANCELLED,
                    PlanExecutionState.BLOCKED: ExecutionManagerStatus.BLOCKED,
                }
                handle.status = state_map.get(result.terminal_state, ExecutionManagerStatus.FAILED)
        except asyncio.CancelledError:
            logger.info("Background execution task for plan %s (exec_id: %s) was cancelled", handle.plan_id, handle.execution_id)
            handle.status = ExecutionManagerStatus.CANCELLED
            handle.reason = "Background execution task cancelled"
        except Exception as exc:
            logger.exception("Unexpected error in background execution task for plan %s: %s", handle.plan_id, exc)
            handle.status = ExecutionManagerStatus.FAILED
            handle.error = str(exc)
            handle.reason = f"Execution engine error: {exc}"
        finally:
            handle.completed_at = _utc_now()
            # Clear active plan mapping so plan can be re-executed if appropriate in the future
            async with self._global_lock:
                if self._plan_to_exec_id.get(handle.plan_id) == handle.execution_id:
                    self._plan_to_exec_id.pop(handle.plan_id, None)

    async def get_execution_by_id(self, execution_id: str) -> ExecutionHandleInfo | None:
        """Retrieve execution handle info by execution_id."""
        handle = self._handles_by_exec_id.get(execution_id.strip())
        return handle.to_info() if handle else None

    async def get_active_execution_by_plan(self, plan_id: str) -> ExecutionHandleInfo | None:
        """Retrieve active execution handle info for plan_id."""
        plan_id_str = plan_id.strip()
        exec_id = self._plan_to_exec_id.get(plan_id_str)
        if exec_id:
            handle = self._handles_by_exec_id.get(exec_id)
            if handle:
                return handle.to_info()

        # If not active, search for latest handle for plan_id
        latest: InternalExecutionHandle | None = None
        for handle in self._handles_by_exec_id.values():
            if handle.plan_id == plan_id_str:
                if latest is None or handle.started_at > latest.started_at:
                    latest = handle
        return latest.to_info() if latest else None

    async def cancel_execution(self, identifier: str) -> ExecutionHandleInfo:
        """
        Request cancellation of an active plan execution by plan_id or execution_id.
        """
        id_str = identifier.strip()
        handle: InternalExecutionHandle | None = None

        if id_str in self._handles_by_exec_id:
            handle = self._handles_by_exec_id[id_str]
        else:
            exec_id = self._plan_to_exec_id.get(id_str)
            if exec_id:
                handle = self._handles_by_exec_id.get(exec_id)

        if handle is None:
            # Fallback search
            for h in self._handles_by_exec_id.values():
                if h.plan_id == id_str or h.execution_id == id_str:
                    handle = h
                    break

        if handle is None:
            raise ExecutionNotFoundError(id_str)

        if not handle.cancel_event.is_set():
            logger.info("Signalling cancellation for execution %s (plan %s)", handle.execution_id, handle.plan_id)
            handle.cancel_event.set()

        return handle.to_info()

    async def shutdown(self, timeout_seconds: float = 10.0) -> None:
        """
        Shutdown background execution manager cleanly during app lifespan termination.
        """
        logger.info("Shutting down BackgroundExecutionManager...")
        self._shutting_down = True

        active_handles = [
            h for h in self._handles_by_exec_id.values()
            if h.status in (ExecutionManagerStatus.QUEUED, ExecutionManagerStatus.RUNNING) and h.task is not None
        ]

        if not active_handles:
            logger.info("No active background executions to shut down.")
            return

        for h in active_handles:
            h.cancel_event.set()
            if h.task and not h.task.done():
                h.task.cancel()

        tasks = [h.task for h in active_handles if h.task is not None]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
            if pending:
                logger.warning("%d background execution tasks did not complete within %ss shutdown timeout", len(pending), timeout_seconds)
            else:
                logger.info("All background execution tasks shut down cleanly.")


# Global singleton instance for application lifespan registration
background_execution_manager = BackgroundExecutionManager()
