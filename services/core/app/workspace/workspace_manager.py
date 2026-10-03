"""
Workspace & Git Checkpoint Manager.

Provides a safe, isolated Git worktree abstraction for autonomous AI Operator tasks.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL SAFETY INVARIANTS (enforced throughout this module)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. NEVER perform destructive Git operations (reset --hard, clean -fd/fdx,
   checkout -- ., restore .) against the primary worktree.

2. ALL rollback/reset operations are scoped to the dedicated autonomous
   worktree only. Any attempt to target the primary worktree raises
   PrimaryWorktreeViolationError immediately.

3. Git commands are executed via GitRunner — no shell=True, no string
   interpolation of user-supplied data, explicit argument arrays, explicit
   timeouts.

4. Checkpoint staging is EXPLICIT: callers must supply the intended file
   paths. No blind `git add .` or `git add -A`.

5. Autonomous worktree paths are derived from task_id using a fixed scheme
   and are verified to be outside the primary worktree directory.

6. The base_commit_sha recorded in WorkspaceInfo is the commit SHA that Git
   actually used — verified after worktree creation.

7. Worktrees and branches are unique per task; collision causes an explicit
   error rather than silent overwrite.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCOPE LIMITATIONS (belong to later stages)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Full crash-recovery subsystem (Stage 6.7)
- Automatic merge / PR creation
- Remote push
- Gemini / Kilo integration
- REST API exposure
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.workspace.exceptions import (
    CheckpointError,
    DirtyPrimaryWorktreeError,
    GitCommandError,
    NotAGitRepositoryError,
    PrimaryWorktreeViolationError,
    RollbackViolationError,
    UnsafePathError,
    WorktreeAlreadyExistsError,
    WorktreeNotFoundError,
    WorkspaceOwnershipError,
)
from app.workspace.git_runner import GitRunner
from app.workspace.models import CheckpointInfo, WorkspaceInfo, WorkspaceStatus


# ── Branch / path naming ────────────────────────────────────────────────────

# Prefix for all task-specific branches created by this manager.
_BRANCH_PREFIX = "ai-operator/task/"

# Pattern for valid task_id characters — no path separators, no shell metacharacters.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

# Default timeout per git operation (seconds).
_GIT_TIMEOUT = 30.0


# ── Helpers ─────────────────────────────────────────────────────────────────

def _validate_task_id(task_id: str) -> None:
    """Reject task IDs that could be used for path traversal or shell injection."""
    if not _TASK_ID_RE.match(task_id):
        raise UnsafePathError(
            task_id,
            "task_id must match [A-Za-z0-9_-]{1..128} — "
            "no path separators, no shell metacharacters",
        )


def _resolve_safe(path: Path) -> Path:
    """
    Resolve a path and return its absolute form.

    Does NOT follow symlinks (resolve(strict=False)) so that non-existent
    paths can be checked before creation without raising FileNotFoundError.
    We then check it is absolute.
    """
    resolved = path.resolve()  # strict=True on CPython 3.6+, raises if not exist
    if not resolved.is_absolute():
        raise UnsafePathError(str(path), "resolved path is not absolute")
    return resolved


def _resolve_safe_nonexistent(path: Path) -> Path:
    """
    Like _resolve_safe but accepts non-existent paths (for pre-creation checks).
    """
    # Path.resolve() on Python 3.6+ resolves symlinks strictly; use parents trick
    # to get an absolute, normalised path without requiring existence.
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        # Build absolute path by resolving the existing parent chain
        parts = list(path.parts)
        resolved = Path(*parts).resolve(strict=False)
    if not resolved.is_absolute():
        raise UnsafePathError(str(path), "resolved path is not absolute")
    return resolved


def _assert_not_inside(candidate: Path, forbidden: Path, label: str) -> None:
    """
    Raise UnsafePathError if *candidate* is equal to or inside *forbidden*.
    Both paths must already be resolved (absolute, no symlinks).
    """
    try:
        candidate.relative_to(forbidden)
        # If the above succeeds, candidate IS inside (or equal to) forbidden
        raise PrimaryWorktreeViolationError(
            f"{label} path {str(candidate)!r} is inside or equal to "
            f"the primary worktree {str(forbidden)!r}"
        )
    except ValueError:
        # relative_to raised ValueError → candidate is NOT inside forbidden ✓
        pass


def _assert_path_inside_worktree(target: Path, worktree: Path, label: str) -> None:
    """
    Raise UnsafePathError if *target* is NOT inside *worktree*.
    Used to validate that staged file paths cannot escape the autonomous workspace.
    """
    try:
        target.relative_to(worktree)
    except ValueError:
        raise UnsafePathError(
            str(target),
            f"{label} escapes the autonomous worktree {str(worktree)!r}",
        )


# ── WorkspaceManager ────────────────────────────────────────────────────────

class WorkspaceManager:
    """
    Safe workspace abstraction for autonomous AI Operator development tasks.

    Each DevelopmentTask gets an isolated Git worktree.  The primary worktree
    is treated as read-only from an autonomous-mutation perspective.

    Worktree placement strategy
    ---------------------------
    Autonomous worktrees are placed in a *worktree_base_dir* that must be
    completely OUTSIDE the primary worktree directory.  The default is a
    sibling directory next to the repository root::

        <repo_root>/../.ai_operator/worktrees/<task_id>

    Callers may supply an explicit *worktree_base_dir* — it will be validated
    to ensure it is not inside (or equal to) the primary worktree.

    Intended lifecycle::

        wm = WorkspaceManager(repo_root=Path("/my/project"))
        info = wm.create_workspace(task_id="task_abc123", base_commit_sha="<sha>")
        cp   = wm.create_checkpoint(
                    task_id="task_abc123",
                    workspace=info,
                    intended_paths=["src/foo.py"],
                    message="feat: implement foo")
        wm.rollback_to_checkpoint(task_id="task_abc123", workspace=info,
                                  checkpoint_sha=cp.commit_sha)
        wm.remove_workspace(task_id="task_abc123", workspace=info)

    WorkspaceManager is infrastructure only — it has no access to:
      - ToolExecutor / BaseTool
      - PermissionEngine
      - ApprovalService / grant logic

    Notes on crash / partial-creation recovery:
        Partial worktree creation (e.g. directory created but `git worktree add`
        failed) is cleaned up by create_workspace() before re-raising.
        Full crash recovery across process restarts is deferred to Stage 6.7.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        worktree_base_dir: Path | None = None,
        git_timeout: float = _GIT_TIMEOUT,
        event_emitter: Any | None = None,
    ) -> None:
        """
        Initialise the manager for a specific Git repository root.

        Args:
            repo_root: Absolute path to the repository root.  Must be the
                       actual repo root (where .git lives), not a subdirectory.
            worktree_base_dir: Directory where autonomous worktrees will be
                               created.  Must be OUTSIDE the primary worktree.
                               Defaults to ``<repo_root>/../.ai_operator/worktrees``.
            git_timeout: Timeout in seconds for individual git subprocess calls.
            event_emitter: Optional EventEmitter for lifecycle events.

        Raises:
            NotAGitRepositoryError: if repo_root is not a Git repository.
            UnsafePathError: if repo_root is not an absolute path.
            PrimaryWorktreeViolationError: if worktree_base_dir is inside the
                                          primary worktree.
        """
        repo_root = _resolve_safe(repo_root)
        runner = GitRunner(cwd=repo_root, timeout=git_timeout)
        self._verify_is_git_repo(runner, repo_root)

        self._repo_root = repo_root
        self._git_timeout = git_timeout
        self._event_emitter = event_emitter

        # Determine and validate worktree base directory
        if worktree_base_dir is None:
            # Default: sibling directory next to the repo root — guaranteed outside
            self._worktree_base = _resolve_safe_nonexistent(
                repo_root.parent / ".ai_operator" / "worktrees"
            )
        else:
            self._worktree_base = _resolve_safe_nonexistent(worktree_base_dir)

        # Validate the base is outside the primary worktree
        primary = self._detect_primary_worktree(runner)
        _assert_not_inside(self._worktree_base, primary, "worktree_base_dir")
        # Also ensure primary is not inside the base
        try:
            primary.relative_to(self._worktree_base)
            raise PrimaryWorktreeViolationError(
                f"worktree_base_dir {str(self._worktree_base)!r} would contain "
                f"the primary worktree {str(primary)!r}"
            )
        except ValueError:
            pass  # primary is not inside base ✓

    # ─── Public API ─────────────────────────────────────────────────────────

    def get_primary_worktree_path(self) -> Path:
        """Return the primary worktree path as detected by Git."""
        runner = GitRunner(cwd=self._repo_root, timeout=self._git_timeout)
        return self._detect_primary_worktree(runner)

    def get_worktree_base_dir(self) -> Path:
        """Return the base directory where autonomous worktrees are stored."""
        return self._worktree_base

    def get_repository_root(self) -> Path:
        """Return the confirmed repository root."""
        return self._repo_root

    def inspect_primary_worktree_status(self) -> list[str]:
        """
        Return a list of dirty files in the primary worktree.
        Empty list means the primary worktree is clean.
        """
        runner = GitRunner(cwd=self._repo_root, timeout=self._git_timeout)
        return self._get_dirty_files(runner, self._repo_root)

    def get_current_head(self, worktree_path: Path | None = None) -> str:
        """
        Return the current HEAD commit SHA for the given worktree path.
        Defaults to the primary worktree (repo root).
        """
        cwd = worktree_path or self._repo_root
        runner = GitRunner(cwd=cwd, timeout=self._git_timeout)
        result = runner.run(["rev-parse", "HEAD"])
        return result.first_line()

    def create_workspace(
        self,
        task_id: str,
        base_commit_sha: str,
        execution_token: str,
    ) -> WorkspaceInfo:
        """
        Create an isolated Git worktree for an autonomous task execution attempt.

        Safety guarantees:
          - Primary worktree is never modified.
          - Autonomous worktree is placed outside the primary worktree directory.
          - The workspace path is unique per execution attempt (task_id + execution_token).
          - The branch is execution-specific and collision-safe.
          - base_commit_sha is verified as a valid, existing commit.
          - Creation is ALWAYS refused if the primary worktree is dirty.
          - Partial-creation failures are cleaned up before re-raising.
          - An ownership marker is written to the workspace for later verification.

        Args:
            task_id: Identifier for the DevelopmentTask.  Must match
                     [A-Za-z0-9_-]{1..128}.
            base_commit_sha: The exact Git commit SHA to base the worktree on.
                             Must exist in the repository.
            execution_token: Unique execution identity from claim_task_attempt().
                             Used to derive a unique workspace path and write
                             an ownership marker.

        Returns:
            WorkspaceInfo describing the created workspace.

        Raises:
            UnsafePathError: bad task_id.
            NotAGitRepositoryError: repo not valid.
            DirtyPrimaryWorktreeError: primary has uncommitted changes.
            WorktreeAlreadyExistsError: worktree/branch already exists.
            GitCommandError: underlying git failure.
            PrimaryWorktreeViolationError: safety invariant check failed.
        """
        _validate_task_id(task_id)
        runner = GitRunner(cwd=self._repo_root, timeout=self._git_timeout)

        # 1. Verify base commit exists
        base_sha = self._verify_commit_sha(runner, base_commit_sha)

        # 2. Detect primary worktree
        primary_wt = self._detect_primary_worktree(runner)

        # 3. Check dirty state (FAIL-CLOSED: always refuse if primary is dirty)
        dirty = self._get_dirty_files(runner, primary_wt)
        if dirty:
            raise DirtyPrimaryWorktreeError(dirty)

        # 4. Compute unique workspace identity per execution attempt
        token_suffix = execution_token.split("_")[-1] if "_" in execution_token else execution_token
        workspace_suffix = f"{task_id}_{token_suffix}"
        autonomous_path_resolved = _resolve_safe_nonexistent(
            self._worktree_base / workspace_suffix
        )

        # 5. Final path safety assertion (defence-in-depth)
        _assert_not_inside(autonomous_path_resolved, primary_wt, "autonomous worktree")

        # 6. Collision check: refuse if path already exists
        if autonomous_path_resolved.exists():
            raise WorktreeAlreadyExistsError(task_id, str(autonomous_path_resolved))

        # 7. Compute execution-specific branch name
        branch_name = f"{_BRANCH_PREFIX}{workspace_suffix}"
        self._check_branch_does_not_exist(runner, branch_name, task_id)

        # 8. Create parent directory (if needed) — before calling git
        autonomous_path_resolved.parent.mkdir(parents=True, exist_ok=True)

        # 9. Create the worktree + branch atomically
        #    `git worktree add <path> -b <branch> <base_sha>`
        #    Uses the verified SHA, not a mutable branch ref.
        try:
            runner.run([
                "worktree", "add",
                str(autonomous_path_resolved),
                "-b", branch_name,
                base_sha,
            ])
        except (GitCommandError, Exception) as exc:
            # Partial cleanup: remove the directory if it was created
            self._try_cleanup_path(autonomous_path_resolved)
            raise GitCommandError(
                f"Failed to create worktree for task {task_id!r}: {exc}",
                cmd=["worktree", "add", "..."],
            ) from exc

        # 10. Verify the worktree is actually registered and at the right commit
        actual_sha = self._get_worktree_head(autonomous_path_resolved)
        if actual_sha != base_sha:
            self._try_remove_worktree(runner, autonomous_path_resolved)
            raise GitCommandError(
                f"Worktree HEAD {actual_sha!r} does not match expected base "
                f"{base_sha!r} — creation aborted and cleaned up.",
            )

        # 11. Write durable ownership marker inside the worktree
        try:
            marker = autonomous_path_resolved / ".ai_operator_ownership"
            marker.write_text(execution_token, encoding="utf-8")
        except Exception as exc:
            self._try_remove_worktree(runner, autonomous_path_resolved)
            self._try_cleanup_path(autonomous_path_resolved)
            raise WorkspaceOwnershipError(
                str(autonomous_path_resolved),
                f"Failed to write ownership marker: {exc}",
            ) from exc

        # 12. Write execution identity into Git config for verification
        try:
            wt_runner = GitRunner(cwd=autonomous_path_resolved, timeout=self._git_timeout)
            wt_runner.run([
                "config", "ai-operator.execution-token", execution_token,
            ])
            # Autonomous checkpoints must work even when the host/CI runner
            # has no global Git identity configured.
            wt_runner.run([
                "config", "user.name", "AI Operator",
            ])
            wt_runner.run([
                "config", "user.email", "ai-operator@localhost",
            ])
        except (GitCommandError, Exception) as exc:
            self._try_remove_worktree(runner, autonomous_path_resolved)
            self._try_cleanup_path(autonomous_path_resolved)
            raise WorkspaceOwnershipError(
                str(autonomous_path_resolved),
                f"Failed to write ownership config: {exc}",
            ) from exc

        return WorkspaceInfo(
            task_id=task_id,
            repository_root=str(self._repo_root),
            primary_worktree_path=str(primary_wt),
            autonomous_worktree_path=str(autonomous_path_resolved),
            branch_name=branch_name,
            base_commit_sha=base_sha,
            created_at=datetime.now(timezone.utc),
            current_commit_sha=actual_sha,
            status=WorkspaceStatus.ACTIVE,
        )

    def get_changed_files(self, workspace: WorkspaceInfo, base_commit_sha: str | None = None) -> list[str]:
        """
        Return the actual files changed in the autonomous worktree.

        The result is derived from Git, never from an agent-reported file list.
        It includes tracked changes relative to the verified base commit and
        untracked files, while excluding the internal ownership marker.
        """
        wt_path = self._validated_autonomous_path(workspace)
        base_sha = base_commit_sha or workspace.base_commit_sha
        runner = GitRunner(cwd=wt_path, timeout=self._git_timeout)

        tracked = runner.run(
            ["diff", "--name-only", base_sha],
            check=False,
        )
        if tracked.returncode != 0:
            raise CheckpointError(
                f"Failed to inspect tracked changes from base {base_sha!r}: {tracked.stderr.strip()}"
            )

        untracked = runner.run(
            ["ls-files", "--others", "--exclude-standard"],
            check=False,
        )
        if untracked.returncode != 0:
            raise CheckpointError(
                f"Failed to inspect untracked changes: {untracked.stderr.strip()}"
            )

        files = {
            line.strip().replace("\\", "/")
            for line in (
                tracked.stdout.splitlines() + untracked.stdout.splitlines()
            )
            if line.strip()
        }
        files.discard(".ai_operator_ownership")
        return sorted(files)

    def inspect_workspace_status(self, workspace: WorkspaceInfo) -> WorkspaceStatus:
        """
        Inspect the current status of an autonomous worktree.

        Returns CLEAN if no staged/unstaged changes, DIRTY otherwise.
        Verifies the path is a real registered Git worktree.

        Raises:
            PrimaryWorktreeViolationError: if workspace path equals primary.
            WorktreeNotFoundError: if the path is not a registered worktree.
        """
        wt_path = self._validated_autonomous_path(workspace)
        dirty = self._get_dirty_files(
            GitRunner(cwd=wt_path, timeout=self._git_timeout),
            wt_path,
        )
        return WorkspaceStatus.DIRTY if dirty else WorkspaceStatus.CLEAN

    def create_checkpoint(
        self,
        task_id: str,
        workspace: WorkspaceInfo,
        intended_paths: list[str],
        message: str,
    ) -> CheckpointInfo:
        """
        Create a Git checkpoint (commit) inside the autonomous worktree.

        EXPLICIT STAGING: only the files in *intended_paths* are staged.
        No blind `git add .` or `git add -A` is performed.

        Safety guarantees:
          - Each path is resolved and verified to be inside the autonomous worktree.
          - No path can escape the workspace root.
          - The resulting commit SHA is read back from Git and verified.
          - The worktree must be the autonomous (non-primary) one.

        Args:
            task_id: Must match workspace.task_id.
            workspace: The WorkspaceInfo describing the target worktree.
            intended_paths: Relative or absolute paths to stage.  Each must
                            resolve inside autonomous_worktree_path.
            message: Commit message for the checkpoint.

        Returns:
            CheckpointInfo with the verified commit SHA.

        Raises:
            PrimaryWorktreeViolationError: if workspace targets the primary wt.
            UnsafePathError: if any intended_path escapes the workspace.
            CheckpointError: if staging or commit fails.
        """
        if task_id != workspace.task_id:
            raise CheckpointError(
                f"task_id mismatch: {task_id!r} != {workspace.task_id!r}"
            )

        wt_path = self._validated_autonomous_path(workspace)

        if not intended_paths:
            raise CheckpointError("intended_paths must not be empty")

        # Validate and resolve each intended path
        resolved_paths: list[Path] = []
        for raw_path in intended_paths:
            p = Path(raw_path)
            if not p.is_absolute():
                p = wt_path / p
            rp = _resolve_safe_nonexistent(p)
            _assert_path_inside_worktree(rp, wt_path, f"intended_path {raw_path!r}")
            resolved_paths.append(rp)

        runner = GitRunner(cwd=wt_path, timeout=self._git_timeout)

        # Pre-staging check: verify index is clean before staging intended paths
        pre_staged_res = runner.run(["diff", "--cached", "--name-only"], check=False)
        pre_staged = [l.strip() for l in pre_staged_res.stdout.splitlines() if l.strip()]
        if pre_staged:
            raise CheckpointError(
                f"Index already contains staged changes before checkpoint creation: {pre_staged}. "
                "Clear pre-existing staged changes before creating a checkpoint."
            )

        # Stage ONLY the intended paths — no `git add .`
        for rp in resolved_paths:
            rel = rp.relative_to(wt_path)
            try:
                runner.run(["add", "--", str(rel)])
            except GitCommandError as exc:
                raise CheckpointError(
                    f"Failed to stage {str(rp)!r}: {exc}"
                ) from exc

        # Verify something is actually staged
        staged_result = runner.run(
            ["diff", "--cached", "--name-only"],
            check=False,
        )
        staged_files = [
            l.strip() for l in staged_result.stdout.splitlines() if l.strip()
        ]
        if not staged_files:
            raise CheckpointError(
                "No files were staged — checkpoint would create an empty commit. "
                "Verify that intended_paths exist and contain changes."
            )

        # Verify staged files are a subset of intended_paths (no extras snuck in)
        intended_rel = {
            str(rp.relative_to(wt_path)).replace("\\", "/")
            for rp in resolved_paths
        }
        staged_rel = {f.replace("\\", "/") for f in staged_files}
        unexpected = staged_rel - intended_rel
        if unexpected:
            # Unstage everything and abort
            runner.run(["reset", "HEAD", "--"], check=False)
            raise CheckpointError(
                f"Unexpected files were staged: {unexpected}. "
                "Checkpoint aborted to prevent committing unintended changes."
            )

        # Commit (normal commit, without --no-verify so Git hooks can run)
        checkpoint_id = f"{task_id}-cp-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        full_message = f"[checkpoint] {message}"
        try:
            runner.run([
                "commit",
                "-m", full_message,
                "--author", "AI Operator <ai-operator@localhost>",
            ])
        except GitCommandError as exc:
            raise CheckpointError(f"git commit failed: {exc}") from exc

        # Read back the verified commit SHA
        sha_result = runner.run(["rev-parse", "HEAD"])
        commit_sha = sha_result.first_line()

        if len(commit_sha) != 40:
            raise CheckpointError(
                f"Unexpected commit SHA format after checkpoint: {commit_sha!r}"
            )

        return CheckpointInfo(
            checkpoint_id=checkpoint_id,
            task_id=task_id,
            commit_sha=commit_sha,
            commit_message=full_message,
            created_at=datetime.now(timezone.utc),
            branch_name=workspace.branch_name,
            autonomous_worktree_path=str(wt_path),
            intended_paths=[str(rp.relative_to(wt_path)) for rp in resolved_paths],
        )

    def rollback_to_checkpoint(
        self,
        task_id: str,
        workspace: WorkspaceInfo,
        checkpoint_sha: str,
    ) -> None:
        """
        Reset the autonomous worktree to a known checkpoint commit SHA.

        SAFETY: This is a hard reset on the AUTONOMOUS worktree only.
        It will refuse to operate if workspace targets the primary worktree.

        The checkpoint_sha must be verified as an ancestor of the current HEAD
        in the autonomous worktree before the reset is applied.

        Args:
            task_id: Must match workspace.task_id.
            workspace: Describes the autonomous worktree.
            checkpoint_sha: A commit SHA that must exist within the autonomous branch.

        Raises:
            PrimaryWorktreeViolationError: if workspace targets the primary wt.
            RollbackViolationError: if checkpoint_sha is invalid or not in the branch.
            GitCommandError: underlying git failure.
        """
        if task_id != workspace.task_id:
            raise RollbackViolationError(
                f"task_id mismatch: {task_id!r} != {workspace.task_id!r}"
            )

        wt_path = self._validated_autonomous_path(workspace)
        runner = GitRunner(cwd=wt_path, timeout=self._git_timeout)

        # Verify the SHA is a valid, full 40-char commit SHA
        if not re.match(r"^[0-9a-f]{40}$", checkpoint_sha):
            raise RollbackViolationError(
                f"checkpoint_sha must be a full 40-character hex SHA, "
                f"got: {checkpoint_sha!r}"
            )

        # Verify the SHA exists in the repository
        verify_result = runner.run(
            ["log", "--oneline", "-1", checkpoint_sha],
            check=False,
        )
        if verify_result.returncode != 0:
            raise RollbackViolationError(
                f"commit {checkpoint_sha!r} could not be found in the "
                "repository"
            )

        # Verify it is reachable from HEAD (ancestor-or-equal) using merge-base
        ancestry_result = runner.run(
            ["merge-base", "--is-ancestor", checkpoint_sha, "HEAD"],
            check=False,
        )
        if ancestry_result.returncode != 0:
            raise RollbackViolationError(
                f"commit {checkpoint_sha!r} is not reachable from HEAD in the "
                "autonomous branch history"
            )

        # Safe hard reset — ONLY inside the autonomous worktree
        runner.run(["reset", "--hard", checkpoint_sha])

    def list_registered_worktrees(self) -> list[dict[str, str]]:
        """
        Return a list of Git-registered worktrees for this repository.

        Each entry is a dict with keys: 'path', 'head', 'branch'.
        This calls `git worktree list --porcelain` for structured output.
        """
        runner = GitRunner(cwd=self._repo_root, timeout=self._git_timeout)
        result = runner.run(["worktree", "list", "--porcelain"])
        return self._parse_worktree_list(result.stdout)

    # ─── Private helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _verify_is_git_repo(runner: GitRunner, path: Path) -> None:
        """Raise NotAGitRepositoryError if path is not a Git repository root."""
        try:
            result = runner.run(["rev-parse", "--show-toplevel"])
            toplevel = _resolve_safe(Path(result.first_line()))
            if toplevel != _resolve_safe(path):
                raise NotAGitRepositoryError(str(path))
        except (GitCommandError, Exception) as exc:
            raise NotAGitRepositoryError(str(path)) from exc

    @staticmethod
    def _detect_primary_worktree(runner: GitRunner) -> Path:
        """Return the resolved absolute path of the primary (main) worktree."""
        result = runner.run(["rev-parse", "--show-toplevel"])
        raw = result.first_line()
        path = Path(raw)
        return _resolve_safe(path)

    @staticmethod
    def _verify_commit_sha(runner: GitRunner, sha: str) -> str:
        """
        Verify *sha* exists in the repository and return the full 40-char SHA.
        Raises GitCommandError if the commit does not exist.
        """
        result = runner.run(["rev-parse", "--verify", f"{sha}^{{commit}}"])
        full = result.first_line()
        if len(full) != 40:
            raise GitCommandError(
                f"Commit {sha!r} did not resolve to a 40-char SHA: {full!r}"
            )
        return full

    @staticmethod
    def _get_dirty_files(runner: GitRunner, _cwd: Path) -> list[str]:
        """
        Return a list of dirty (modified/untracked) file paths in the worktree.
        Uses `git status --porcelain` for reliable machine-readable output.
        """
        result = runner.run(["status", "--porcelain"], check=False)
        dirty: list[str] = []
        for line in result.stdout.splitlines():
            line = line.rstrip()
            if line:
                dirty.append(line)
        return dirty

    def _worktree_path_for(self, task_id: str) -> Path:
        """Return the intended (not yet created) worktree path for task_id."""
        return self._worktree_base / task_id

    @staticmethod
    def _check_branch_does_not_exist(
        runner: GitRunner, branch_name: str, task_id: str
    ) -> None:
        """Raise WorktreeAlreadyExistsError if the branch already exists."""
        result = runner.run(
            ["branch", "--list", branch_name],
            check=False,
        )
        if result.stdout.strip():
            raise WorktreeAlreadyExistsError(task_id, f"branch:{branch_name}")

    def _get_worktree_head(self, worktree_path: Path) -> str:
        """Return HEAD SHA for a worktree path."""
        wt_runner = GitRunner(cwd=worktree_path, timeout=self._git_timeout)
        result = wt_runner.run(["rev-parse", "HEAD"])
        return result.first_line()

    def _validated_autonomous_path(self, workspace: WorkspaceInfo) -> Path:
        """
        Resolve and validate the autonomous worktree path from a WorkspaceInfo.

        Raises:
            PrimaryWorktreeViolationError: if it matches or is inside the primary worktree.
            WorktreeNotFoundError: if the path is not a Git-registered worktree.
            UnsafePathError: if task_id format is invalid.
        """
        _validate_task_id(workspace.task_id)

        primary_wt = self._detect_primary_worktree(
            GitRunner(cwd=self._repo_root, timeout=self._git_timeout)
        )
        autonomous_path = _resolve_safe_nonexistent(
            Path(workspace.autonomous_worktree_path)
        )

        # Hard guard: autonomous path must NOT be or be inside the primary worktree
        if autonomous_path == primary_wt:
            raise PrimaryWorktreeViolationError(
                f"Operation refused: autonomous_worktree_path {str(autonomous_path)!r} "
                f"is the primary worktree {str(primary_wt)!r}"
            )
        _assert_not_inside(autonomous_path, primary_wt, "autonomous worktree")

        if not autonomous_path.exists():
            raise WorktreeNotFoundError(workspace.task_id)

        # Verify registration in Git's official worktree metadata
        registered = self.list_registered_worktrees()
        matching_wt = None
        for wt in registered:
            if "path" in wt:
                try:
                    wt_p = _resolve_safe(Path(wt["path"]))
                    if wt_p == autonomous_path:
                        matching_wt = wt
                        break
                except Exception:
                    continue

        if matching_wt is None:
            raise WorktreeNotFoundError(workspace.task_id)

        # Verify registered branch identity matches workspace.branch_name
        reg_branch = matching_wt.get("branch", "")
        expected_branch = workspace.branch_name
        if reg_branch and not (reg_branch == expected_branch or reg_branch.endswith(f"/{expected_branch}")):
            raise PrimaryWorktreeViolationError(
                f"Registered worktree branch {reg_branch!r} does not match expected {expected_branch!r}"
            )

        # Verify repository identity via rev-parse inside autonomous worktree
        wt_runner = GitRunner(cwd=autonomous_path, timeout=self._git_timeout)
        try:
            wt_toplevel_res = wt_runner.run(["rev-parse", "--show-toplevel"])
            wt_toplevel = _resolve_safe(Path(wt_toplevel_res.first_line()))
            if wt_toplevel != autonomous_path:
                raise PrimaryWorktreeViolationError(
                    f"Worktree top-level {str(wt_toplevel)!r} does not match autonomous path {str(autonomous_path)!r}"
                )

            common_dir_res = wt_runner.run(["rev-parse", "--git-common-dir"])
            common_dir = _resolve_safe(Path(common_dir_res.first_line()))
            primary_git_dir = _resolve_safe(self._repo_root / ".git")
            if common_dir != primary_git_dir:
                raise PrimaryWorktreeViolationError(
                    f"Worktree common git dir {str(common_dir)!r} does not match primary git dir {str(primary_git_dir)!r}"
                )
        except GitCommandError as exc:
            raise WorktreeNotFoundError(workspace.task_id) from exc

        return autonomous_path

    def verify_workspace_ownership(
        self,
        workspace: WorkspaceInfo,
        expected_execution_token: str,
    ) -> bool:
        """
        Verify that the workspace is still owned by the expected execution.

        Checks:
          1. Git config key `ai-operator.execution-token` matches expected_execution_token.
          2. Ownership marker file `.ai_operator_ownership` exists and matches.

        Returns True if ownership is verified. Raises WorkspaceOwnershipError otherwise.
        """
        _validate_task_id(workspace.task_id)
        autonomous_path = self._validated_autonomous_path(workspace)

        marker_path = autonomous_path / ".ai_operator_ownership"
        token_from_marker = None
        try:
            if marker_path.exists():
                token_from_marker = marker_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass

        if token_from_marker is not None:
            if token_from_marker == expected_execution_token:
                return True
            raise WorkspaceOwnershipError(
                str(autonomous_path),
                f"Expected execution token {expected_execution_token!r}, "
                f"but ownership marker contains {token_from_marker!r}",
            )

        token_from_config = None
        try:
            runner = GitRunner(cwd=autonomous_path, timeout=self._git_timeout)
            res = runner.run(["config", "ai-operator.execution-token"], check=False)
            if res.returncode == 0:
                token_from_config = res.stdout.strip()
        except Exception:
            pass

        if token_from_config == expected_execution_token:
            return True

        raise WorkspaceOwnershipError(
            str(autonomous_path),
            f"Expected execution token {expected_execution_token!r}, "
            f"but config={token_from_config!r} and no ownership marker found",
        )

    def remove_workspace(
        self,
        task_id: str,
        workspace: WorkspaceInfo,
        *,
        force: bool = False,
        expected_execution_token: str,
    ) -> None:
        """
        Safely remove the autonomous worktree and its task-specific branch.

        This never touches the primary worktree.

        Args:
            task_id: Must match workspace.task_id.
            workspace: The workspace to remove.
            force: If True, remove even if the worktree has uncommitted changes.
            expected_execution_token: REQUIRED. The execution token that created
                this workspace. The manager verifies the token against Git config
                / ownership marker before allowing removal. FAIL-CLOSED: if
                verification fails or evidence is missing, WorkspaceOwnershipError
                is raised.

        Raises:
            PrimaryWorktreeViolationError: if workspace targets primary wt.
            WorkspaceOwnershipError: if ownership verification fails.
            GitCommandError: underlying git failure.
        """
        if task_id != workspace.task_id:
            raise PrimaryWorktreeViolationError(
                f"task_id mismatch: {task_id!r} != {workspace.task_id!r}"
            )

        # FAIL-CLOSED ownership check before any destructive operation
        try:
            self.verify_workspace_ownership(workspace, expected_execution_token)
        except WorkspaceOwnershipError:
            raise
        except Exception as exc:
            raise WorkspaceOwnershipError(
                workspace.autonomous_worktree_path,
                f"Ownership verification raised unexpected exception: {exc}",
            ) from exc

        wt_path = self._validated_autonomous_path(workspace)
        runner = GitRunner(cwd=self._repo_root, timeout=self._git_timeout)

        # Remove the worktree via git (which also prunes the ref)
        wt_remove_args = ["worktree", "remove"]
        if force:
            wt_remove_args.append("--force")
        wt_remove_args.append(str(wt_path))

        try:
            runner.run(wt_remove_args)
        except GitCommandError:
            # If git worktree remove fails, try force-remove the directory
            # as a last resort, then prune
            self._try_cleanup_path(wt_path)
            runner.run(["worktree", "prune"], check=False)

        # Delete the task-specific branch (best-effort)
        runner.run(
            ["branch", "-D", workspace.branch_name],
            check=False,
        )

    @staticmethod
    def _try_cleanup_path(path: Path) -> None:
        """Best-effort removal of a directory — does not raise on failure."""
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass

    @staticmethod
    def _try_remove_worktree(runner: GitRunner, path: Path) -> None:
        """Best-effort `git worktree remove --force` — does not raise."""
        try:
            runner.run(["worktree", "remove", "--force", str(path)], check=False)
        except Exception:
            pass

    @staticmethod
    def _parse_worktree_list(porcelain_output: str) -> list[dict[str, str]]:
        """Parse `git worktree list --porcelain` output into structured dicts."""
        worktrees: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in porcelain_output.splitlines():
            line = line.strip()
            if not line:
                if current:
                    worktrees.append(current)
                    current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line[len("worktree "):]
            elif line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):]
            elif line.startswith("branch "):
                current["branch"] = line[len("branch "):]
            elif line == "bare":
                current["bare"] = "true"
        if current:
            worktrees.append(current)
        return worktrees
