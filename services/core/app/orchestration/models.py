"""
Autonomous Task Orchestrator — Domain Models.

All models are frozen BaseModels. Carry structured metadata about agent context,
execution results, orchestration options, and final orchestration outcomes.

SECURITY: DevelopmentAgentContext MUST NOT carry PermissionEngine, ToolExecutor,
ApprovalService, secrets, or raw credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.tasks.models import TaskStatus
from app.verification.models import VerificationReport


class DevelopmentResultStatus(str, Enum):
    """Execution status returned by a DevelopmentAgent."""
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class DevelopmentAgentContext(BaseModel):
    """
    Context provided to a DevelopmentAgent for task execution.

    SECURITY INVARIANT:
      Must NOT contain ToolExecutor, BaseTool, PermissionEngine,
      ApprovalService, grants, secrets, or raw credentials.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=False)

    task_id: str
    project_id: str
    workspace_path: str
    repository_root: str
    title: str
    description: str
    attempt_count: int
    expected_base_commit: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DevelopmentResult(BaseModel):
    """
    Structured outcome of a DevelopmentAgent execution.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=False)

    status: DevelopmentResultStatus
    summary: str = ""
    changed_files: tuple[str, ...] = Field(default_factory=tuple)
    error_message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == DevelopmentResultStatus.SUCCESS


class OrchestrationOptions(BaseModel):
    """
    Configuration policy for task orchestration runs.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=False)

    max_attempts: int = Field(default=3, ge=1)
    auto_cleanup_workspace: bool = True
    base_commit_sha: str | None = None


class OrchestrationResult(BaseModel):
    """
    Final structured verdict of an AutonomousTaskOrchestrator run.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    task_id: str
    final_status: TaskStatus
    attempt_count: int
    committed: bool
    checkpoint_sha: str | None = None
    verification_report: VerificationReport | None = None
    agent_result: DevelopmentResult | None = None
    error_message: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RecoveryStatus(str, Enum):
    """Result status for task recovery attempts."""
    NOT_STALE = "NOT_STALE"
    RECOVERY_CLAIMED = "RECOVERY_CLAIMED"
    ALREADY_RECOVERED = "ALREADY_RECOVERED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    CANCELLED = "CANCELLED"
    ABORTED = "ABORTED"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    RECOVERY_LIMIT_REACHED = "RECOVERY_LIMIT_REACHED"


class RecoveryResult(BaseModel):
    """
    Structured outcome of a Crash & Timeout Recovery Manager operation.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    task_id: str
    status: RecoveryStatus
    previous_task_status: TaskStatus
    new_task_status: TaskStatus | None = None
    recovery_owner: str | None = None
    recovery_attempt_count: int = 0
    message: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

