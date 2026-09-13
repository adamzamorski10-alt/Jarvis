"""
AI Operator Core — REST API DTO Schemas for Execution Control Plane

Pydantic schemas for /api/plans, /api/tasks, and /api/runs endpoints.
Enforces explicit, typed, immutable contracts.
"""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class PlanExecutionResponse(BaseModel):
    """Response returned immediately upon starting a plan execution."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    execution_id: str
    status: str


class PlanExecutionStatusResponse(BaseModel):
    """Authoritative execution status for a plan."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    execution_id: str | None = None
    status: str
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    running_tasks: int = 0
    reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TaskStatusResponse(BaseModel):
    """Sanitized, safe persisted task status response DTO."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    plan_id: str
    task_key: str
    title: str
    status: str
    attempt_count: int
    max_attempts: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None


class RunStatusResponse(BaseModel):
    """Authoritative run status response DTO."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    task_id: str
    status: str
    task_status: str
    attempt_count: int = 0
    verification_passed: bool = False
    commit_sha: str | None = None
    failure_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ErrorResponse(BaseModel):
    """Structured, bounded error response schema."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    error: str
    detail: str | None = None
