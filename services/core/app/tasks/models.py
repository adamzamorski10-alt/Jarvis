"""
Domain Models and Enums for Development Tasks.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    PASSED = "PASSED"
    REPAIR_REQUIRED = "REPAIR_REQUIRED"
    COMMITTED = "COMMITTED"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ABORTED = "ABORTED"


class DevelopmentTask(BaseModel):
    """
    Domain model for an AI Operator autonomous development task.

    Identity contract:
      task_key — stable application-owned identifier within a plan.
                 Derived from the model's proposal_key at normalization time.
                 Empty string for tasks not created through plan normalization.
      task_id  — primary key.  Deterministic SHA-256("plan_id:task_key") prefixed with "task_".
                 69 characters total (5 prefix + 64 hex chars).
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    task_key: str = Field(default="")
    project_id: str
    parent_task_id: str | None = None
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 0
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Stage 6.7 — Heartbeat & Crash Recovery Tracking
    last_heartbeat: datetime | None = None
    recovery_attempt_count: int = Field(default=0, ge=0)
    max_recovery_attempts: int = Field(default=3, ge=1)
    last_recovery_at: datetime | None = None
    recovery_owner: str | None = None
    execution_token: str | None = None

    # Stage 6.8 — Workspace path for execution-bound cleanup
    workspace_path: str | None = None

    # Stage 6.8 — Execution termination & cleanup safety
    execution_terminated_at: datetime | None = None
    workspace_cleanup_pending: bool = False


class TaskTransitionResult(BaseModel):
    """
    Structured outcome of a state machine transition attempt.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    success: bool
    task_id: str
    previous_status: TaskStatus
    new_status: TaskStatus
    transitioned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str | None = None
    task: DevelopmentTask | None = None

