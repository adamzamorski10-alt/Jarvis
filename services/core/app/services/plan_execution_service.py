"""
Service — Plan Execution Bridge

Application-level bridge connecting plan persistence (PlanService/TaskRepository)
to autonomous task execution (AutonomousRunController/AutonomousTaskOrchestrator).
"""
from __future__ import annotations

import logging

from app.orchestration.exceptions import (
    MaxAttemptsExceededError,
    OrchestrationError,
    TaskNotStartableError,
)
from app.orchestration.models import OrchestrationOptions
from app.orchestration.run_controller import AutonomousRunController, AutonomousRunResult
from app.repositories.task_repository import TaskRepository
from app.tasks.exceptions import TaskNotFoundError
from app.tasks.models import TaskStatus

logger = logging.getLogger(__name__)


class DependencyUnmetError(OrchestrationError):
    """
    Raised when attempting to execute a task whose prerequisite dependencies
    are not in an execution-complete (COMMITTED) state.
    """

    def __init__(self, task_id: str, unmet_dependencies: list[str]) -> None:
        self.task_id = task_id
        self.unmet_dependencies = unmet_dependencies
        super().__init__(
            f"Task '{task_id}' cannot execute because prerequisite dependencies are not complete: "
            f"{unmet_dependencies}"
        )


class PlanExecutionService:
    """
    Application-level bridge connecting plan persistence to task execution.

    Responsibilities:
      - Validates task existence in TaskRepository
      - Enforces task startability & attempt limits
      - Enforces DAG dependency execution eligibility (all prerequisite tasks must be COMMITTED)
      - Delegates task execution to AutonomousRunController
    """

    def __init__(
        self,
        task_repository: TaskRepository,
        run_controller: AutonomousRunController,
    ) -> None:
        self._repo = task_repository
        self._run_controller = run_controller

    async def execute_planned_task(
        self,
        task_id: str,
        timeout_seconds: float | None = None,
        options: OrchestrationOptions | None = None,
    ) -> AutonomousRunResult:
        """
        Execute a persisted planned task by task_id.

        Args:
            task_id: Unique identifier of the persisted DevelopmentTask.
            timeout_seconds: Optional execution timeout override.
            options: Optional orchestration options.

        Returns:
            AutonomousRunResult detailing the outcome of the execution.

        Raises:
            TaskNotFoundError: if task_id does not exist in repository.
            TaskNotStartableError: if task status is terminal (COMMITTED, CANCELLED) or already RUNNING.
            MaxAttemptsExceededError: if task attempt_count >= max_attempts.
            DependencyUnmetError: if prerequisite tasks are not COMMITTED.
        """
        # 1. Load task from repository
        task = await self._repo.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)

        # 2. Check task status eligibility
        if task.status in (TaskStatus.COMMITTED, TaskStatus.CANCELLED):
            raise TaskNotStartableError(
                task_id=task_id,
                status=task.status.value,
                reason=f"Task is in terminal status '{task.status.value}' and cannot be executed",
            )

        if task.status == TaskStatus.RUNNING:
            raise TaskNotStartableError(
                task_id=task_id,
                status=task.status.value,
                reason="Task is already RUNNING",
            )

        # 3. Check attempt count
        if task.attempt_count >= task.max_attempts:
            raise MaxAttemptsExceededError(
                task_id=task_id,
                attempt_count=task.attempt_count,
                max_attempts=task.max_attempts,
            )

        # 4. Check DAG dependency eligibility
        satisfied, unmet = await self._repo.are_dependencies_satisfied(task_id)
        if not satisfied:
            logger.warning(
                "Execution rejected for task %s: unmet dependencies %s",
                task_id,
                unmet,
            )
            raise DependencyUnmetError(task_id=task_id, unmet_dependencies=unmet)

        # 5. Execute via AutonomousRunController
        return await self._run_controller.start_run(
            task_id=task_id,
            timeout_seconds=timeout_seconds,
            options=options,
        )
