"""
Workspace & Git Checkpoint Manager — Domain Models.

All models are immutable Pydantic dataclasses.  They carry structured
metadata about autonomous workspaces and checkpoints.

IMPORTANT: These models are informational — Git itself is always the
           authoritative source of truth.  The caller must re-verify
           against Git state rather than relying on cached model values.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceStatus(str, Enum):
    """Lifecycle status of an autonomous worktree."""

    CREATING = "CREATING"
    ACTIVE = "ACTIVE"
    DIRTY = "DIRTY"      # Autonomous worktree has unexpected uncommitted changes
    CLEAN = "CLEAN"      # Autonomous worktree is at a checkpoint, nothing staged
    REMOVING = "REMOVING"
    REMOVED = "REMOVED"
    FAILED = "FAILED"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkspaceInfo(BaseModel):
    """
    Metadata for a single autonomous task workspace (Git worktree).

    This is NOT the authoritative source of truth — callers must verify
    current Git state when safety matters.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    task_id: str
    """The DevelopmentTask identifier this workspace belongs to."""

    repository_root: str
    """Absolute path to the root of the Git repository."""

    primary_worktree_path: str
    """Absolute path to the user's primary worktree (must never be touched destructively)."""

    autonomous_worktree_path: str
    """Absolute path of the isolated worktree created for this task."""

    branch_name: str
    """Task-specific branch name, e.g. 'ai-operator/task/<task_id>'."""

    base_commit_sha: str
    """The exact, immutable commit SHA the autonomous worktree was based on."""

    created_at: datetime = Field(default_factory=_utc_now)
    """UTC timestamp when the workspace was created."""

    current_commit_sha: str = ""
    """Latest commit SHA in the autonomous worktree (updated on checkpoint)."""

    status: WorkspaceStatus = WorkspaceStatus.ACTIVE


class CheckpointInfo(BaseModel):
    """
    Metadata for a single checkpoint commit inside an autonomous worktree.

    A checkpoint is a real Git commit — the SHA here is the authoritative
    identifier.  The implementation must verify the SHA against Git output
    before returning this model.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    checkpoint_id: str
    """Unique identifier, e.g. '<task_id>-cp-<n>'."""

    task_id: str
    """The DevelopmentTask identifier this checkpoint belongs to."""

    commit_sha: str
    """Verified Git commit SHA for this checkpoint."""

    commit_message: str
    """The commit message used for this checkpoint."""

    created_at: datetime = Field(default_factory=_utc_now)
    """UTC timestamp when the checkpoint was created."""

    branch_name: str
    """The branch in the autonomous worktree where this commit lives."""

    autonomous_worktree_path: str
    """Absolute path of the worktree this checkpoint was created in."""

    intended_paths: list[str] = Field(default_factory=list)
    """The file paths that were explicitly staged for this checkpoint."""
