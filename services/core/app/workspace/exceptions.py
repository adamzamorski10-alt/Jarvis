"""
Workspace & Git Checkpoint Manager — Domain Exceptions.

All exceptions are typed so callers can handle specific failure modes
without parsing error messages.
"""


class WorkspaceError(Exception):
    """Base exception for all workspace/git errors."""


class GitCommandError(WorkspaceError):
    """
    A controlled Git subprocess command failed.

    Attributes:
        returncode: Process exit code.
        stdout: Captured stdout (may be empty).
        stderr: Captured stderr (may be empty).
        cmd: The argument list that was executed (no secrets).
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: int = -1,
        stdout: str = "",
        stderr: str = "",
        cmd: list[str] | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cmd = cmd or []
        super().__init__(message)


class GitTimeoutError(WorkspaceError):
    """A Git command exceeded its allowed timeout."""

    def __init__(self, cmd: list[str], timeout: float) -> None:
        self.cmd = cmd
        self.timeout = timeout
        super().__init__(
            f"Git command timed out after {timeout}s: {' '.join(cmd[:3])!r}..."
        )


class NotAGitRepositoryError(WorkspaceError):
    """The supplied path is not inside a valid Git repository."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Not a Git repository: {path!r}")


class PrimaryWorktreeViolationError(WorkspaceError):
    """
    Refused: the requested operation would target or modify the primary worktree.

    This is the hard safety boundary — no destructive operations are permitted
    against the user's primary workspace.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Primary worktree protection violated: {reason}")


class UnsafePathError(WorkspaceError):
    """
    A supplied path failed safety validation (traversal, symlink escape, etc.).
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Unsafe path {path!r}: {reason}")


class WorktreeAlreadyExistsError(WorkspaceError):
    """A worktree or branch for this task already exists — refusing to overwrite."""

    def __init__(self, task_id: str, path: str) -> None:
        self.task_id = task_id
        self.path = path
        super().__init__(
            f"Worktree for task {task_id!r} already exists at {path!r}"
        )


class WorktreeNotFoundError(WorkspaceError):
    """No managed worktree found for the given task."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"No workspace found for task {task_id!r}")


class DirtyPrimaryWorktreeError(WorkspaceError):
    """
    The primary worktree has uncommitted changes.

    Autonomous workspace creation is refused by default when the primary
    worktree is dirty, to avoid accidental capture of user changes.
    """

    def __init__(self, dirty_files: list[str]) -> None:
        self.dirty_files = dirty_files
        count = len(dirty_files)
        super().__init__(
            f"Primary worktree has {count} uncommitted change(s) — "
            "refusing to create autonomous workspace. "
            "Commit or stash your changes first."
        )


class CheckpointError(WorkspaceError):
    """A checkpoint operation failed."""


class RollbackViolationError(WorkspaceError):
    """
    Refused: rollback would target the primary worktree or an unregistered path.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Rollback refused: {reason}")


class WorkspaceOwnershipError(WorkspaceError):
    """
    Refused: workspace removal blocked because ownership cannot be verified
    or the workspace is still owned by an active execution.
    """

    def __init__(self, workspace_path: str, reason: str) -> None:
        self.workspace_path = workspace_path
        self.reason = reason
        super().__init__(
            f"Workspace ownership verification failed for {workspace_path!r}: {reason}"
        )
