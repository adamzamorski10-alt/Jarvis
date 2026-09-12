"""
Service — Plan Persistence
Bridges ManagerPlanResult to TaskRepository.create_tasks_idempotent().

PlanService is intentionally thin: it validates rejection state, converts
the task_graph adjacency list into (task_id, depends_on_task_id) edges
using the existing resolve_dag_edges(), and delegates all atomic
persistence, idempotency, conflict detection, and rollback to the repository.
"""
from __future__ import annotations

from app.manager.models import (
    ManagerInvalidPlanError,
    ManagerPlanResult,
    compute_task_id,
    resolve_dag_edges,
)
from app.repositories.task_repository import PlanConflictError, TaskRepository
from app.tasks.models import DevelopmentTask, TaskStatus


class PlanService:
    """
    Persists a validated ManagerPlanResult as DevelopmentTask and
    TaskDependency records.
    """

    def __init__(self, task_repository: TaskRepository) -> None:
        self._repo = task_repository

    async def persist_manager_plan_result(
        self,
        manager_plan_result: ManagerPlanResult,
    ) -> list[DevelopmentTask]:
        if manager_plan_result.rejected:
            raise ManagerInvalidPlanError(
                manager_plan_result.rejection_reason or "Plan was rejected"
            )

        seen_task_keys: set[str] = set()
        for task in manager_plan_result.tasks:
            if task.task_key in seen_task_keys:
                raise ManagerInvalidPlanError(
                    f"Duplicate task_key: {task.task_key!r}"
                )
            seen_task_keys.add(task.task_key)

            expected_id = compute_task_id(manager_plan_result.plan_id, task.task_key)
            if task.task_id != expected_id:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has non-canonical task_id {task.task_id!r}, expected {expected_id!r}"
                )

            if task.status != TaskStatus.PENDING:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has status {task.status!r}, expected PENDING"
                )

            if task.attempt_count != 0:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has non-zero attempt_count {task.attempt_count}"
                )

            if task.max_attempts < 1 or task.max_attempts > 100:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has max_attempts {task.max_attempts}, expected 1-100"
                )

            if task.started_at is not None:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has started_at populated"
                )
            if task.completed_at is not None:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has completed_at populated"
                )
            if task.execution_token is not None:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has execution_token populated"
                )
            if task.workspace_path is not None:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has workspace_path populated"
                )
            if task.recovery_owner is not None:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has recovery_owner populated"
                )
            if task.last_heartbeat is not None:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has last_heartbeat populated"
                )
            if task.execution_terminated_at is not None:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has execution_terminated_at populated"
                )
            if task.workspace_cleanup_pending:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has workspace_cleanup_pending=True"
                )
            if task.recovery_attempt_count != 0:
                raise ManagerInvalidPlanError(
                    f"Task {task.task_key!r} has non-zero recovery_attempt_count "
                    f"{task.recovery_attempt_count}"
                )

        task_key_to_task_id = {
            task.task_key: task.task_id for task in manager_plan_result.tasks
        }
        dep_edges = resolve_dag_edges(
            task_key_to_task_id, manager_plan_result.task_graph
        )

        return await self._repo.create_tasks_idempotent(
            plan_id=manager_plan_result.plan_id,
            tasks=manager_plan_result.tasks,
            dependencies=dep_edges,
            plan_fingerprint=manager_plan_result.plan_fingerprint,
        )
