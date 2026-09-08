"""
workspace package — Workspace & Git Checkpoint Manager.

Stage 6.4: Safe, isolated Git worktree abstraction for AI Operator autonomous tasks.
"""
from app.workspace.models import WorkspaceInfo, WorkspaceStatus, CheckpointInfo
from app.workspace.exceptions import (
    WorkspaceError,
    GitCommandError,
    GitTimeoutError,
    NotAGitRepositoryError,
    PrimaryWorktreeViolationError,
    UnsafePathError,
    WorktreeAlreadyExistsError,
    WorktreeNotFoundError,
    DirtyPrimaryWorktreeError,
    CheckpointError,
    RollbackViolationError,
    WorkspaceOwnershipError,
)
from app.workspace.git_runner import GitRunner, GitResult
from app.workspace.workspace_manager import WorkspaceManager

__all__ = [
    # Models
    "WorkspaceInfo",
    "WorkspaceStatus",
    "CheckpointInfo",
    # Exceptions
    "WorkspaceError",
    "GitCommandError",
    "GitTimeoutError",
    "NotAGitRepositoryError",
    "PrimaryWorktreeViolationError",
    "UnsafePathError",
    "WorktreeAlreadyExistsError",
    "WorktreeNotFoundError",
    "DirtyPrimaryWorktreeError",
    "CheckpointError",
    "RollbackViolationError",
    "WorkspaceOwnershipError",
    # Git runner (internal but exported for testing)
    "GitRunner",
    "GitResult",
    # Manager
    "WorkspaceManager",
]
