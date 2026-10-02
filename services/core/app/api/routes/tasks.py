"""
Tasks API routes.

Provides read-only task inspection endpoints for the desktop UI.
No task mutation is exposed through these routes.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.schemas.tasks import TaskDetailResponse, TaskListResponse, TaskListResponseItem
from app.dependencies import get_task_repository
from app.identity.auth import require_authenticated
from app.repositories.task_repository import TaskRepository
from app.tasks.models import TaskStatus

router = APIRouter(prefix="/tasks", tags=["Tasks"])


def _task_to_list_item(task: Any) -> TaskListResponseItem:
    return TaskListResponseItem(
        task_id=task.task_id,
        title=task.title,
        status=task.status.value if hasattr(task.status, "value") else str(task.status),
        created_at=task.created_at,
        updated_at=task.completed_at or task.started_at or task.last_recovery_at,
        plan_id=task.plan_id or None,
        parent_task_id=task.parent_task_id,
        project_id=task.project_id,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        started_at=task.started_at,
        completed_at=task.completed_at,
        failure_reason=task.failure_reason,
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    request: Request,
    status: str | None = Query(default=None, description="Filter by task status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    task_repo: TaskRepository = Depends(get_task_repository),
) -> TaskListResponse:
    _ = require_authenticated(request)
    """List tasks with optional status filtering. Newest first."""
    task_status = None
    if status:
        try:
            task_status = TaskStatus(status.upper())
        except ValueError:
            pass

    tasks = await task_repo.list_tasks(status=task_status, limit=limit, offset=offset)

    # Get total count approximation by fetching a large list (list_tasks doesn't
    # currently return total; we approximate using the returned batch size vs limit).
    # For an exact total we'd need a COUNT query, but we keep it bounded.
    total = len(tasks)
    if total >= limit:
        total = limit + offset + 1  # indicate there may be more

    return TaskListResponse(
        tasks=[_task_to_list_item(t) for t in tasks],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(
    request: Request,
    task_id: str,
    task_repo: TaskRepository = Depends(get_task_repository),
) -> TaskDetailResponse:
    _ = require_authenticated(request)
    """Get detailed information about a specific task."""
    task_id_clean = task_id.strip()
    if not task_id_clean:
        raise HTTPException(status_code=400, detail="task_id is required")

    task = await task_repo.get_task(task_id_clean)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id_clean}' not found")

    return TaskDetailResponse(
        task_id=task.task_id,
        task_key=task.task_key,
        title=task.title,
        status=task.status.value if hasattr(task.status, "value") else str(task.status),
        created_at=task.created_at,
        updated_at=task.completed_at or task.started_at or task.last_recovery_at,
        plan_id=task.plan_id or None,
        parent_task_id=task.parent_task_id,
        project_id=task.project_id,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        started_at=task.started_at,
        completed_at=task.completed_at,
        failure_reason=task.failure_reason,
        priority=task.priority,
        recovery_attempt_count=task.recovery_attempt_count,
        max_recovery_attempts=task.max_recovery_attempts,
        last_recovery_at=task.last_recovery_at,
        execution_token=task.execution_token,
        workspace_path=task.workspace_path,
    )
