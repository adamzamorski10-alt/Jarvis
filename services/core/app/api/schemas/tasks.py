"""
Pydantic schemas for Tasks API.
"""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict


class TaskListResponseItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime | None = None
    plan_id: str | None = None
    parent_task_id: str | None = None
    project_id: str | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None


class TaskListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tasks: list[TaskListResponseItem]
    total: int
    limit: int
    offset: int


class TaskDetailResponse(TaskListResponseItem):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_key: str = ""
    priority: int = 0
    recovery_attempt_count: int = 0
    max_recovery_attempts: int = 3
    last_recovery_at: datetime | None = None
    execution_token: str | None = None
    workspace_path: str | None = None
