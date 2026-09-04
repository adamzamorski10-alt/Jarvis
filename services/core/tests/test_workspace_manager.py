"""
Stage 6.4 — Workspace & Git Checkpoint Manager Tests.

Uses REAL Git repositories initialised in pytest tmp_path fixtures.
No mocking of Git itself for core behaviour tests.

Test coverage (30 required items):
  1.  Repository root detection
  2.  Primary worktree detection
  3.  Successful isolated worktree creation
  4.  Correct task-specific branch creation
  5.  Exact base commit SHA recorded
  6.  Primary worktree remains unchanged after workspace creation
  7.  Primary worktree files remain untouched
  8.  Autonomous worktree is a real Git worktree
  9.  Status inspection
  10. Checkpoint commit creation
  11. Checkpoint contains ONLY intended files
  12. Checkpoint SHA is verified (40-char hex)
  13. Multiple tasks receive isolated worktrees
  14. Worktree/branch collision handling
  15. Refuse unsafe primary-worktree paths
  16. Refuse path traversal / invalid workspace paths
  17. Dirty-primary-worktree behaviour (fail-closed)
  18. Safe rollback inside autonomous worktree
  19. Rollback cannot target primary worktree
  20. Worktree removal
  21. Failed creation cleanup (simulated)
  22. Git command timeout/error handling (GitRunner)
  23. No shell=True for Git execution
  24. No arbitrary Git command API (blocked sub-commands)
  25. Regression — Stage 6.2 TaskRepository tests (import only guard)
  26. Regression — Stage 6.3 TaskStateMachine tests (import only guard)
  27-30. Full backend regression suite is run separately via pytest
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app.workspace.exceptions import (
    CheckpointError,
    DirtyPrimaryWorktreeError,
    GitCommandError,
    PrimaryWorktreeViolationError,
    RollbackViolationError,
    UnsafePathError,
    WorktreeAlreadyExistsError,
    WorktreeNotFoundError,
)
from app.workspace.git_runner import GitResult, GitRunner
from app.workspace.models import WorkspaceStatus
from app.workspace.workspace_manager import WorkspaceManager


# ────────────────────────────────────────────────────────────────────────────
# Test fixtures — real temporary Git repositories
# ────────────────────────────────────────────────────────────────────────────

def _init_real_git_repo(path: Path, *, author: str = "Test User <test@example.com>") -> str:
    """
    Initialise a real Git repository with one committed file.

    Returns the full 40-char SHA of the initial commit.
    """
    env = {
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
        "HOME": str(path),          # prevent global gitconfig interference
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    import os
    full_env = {**os.environ, **env}

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            text=True,
            env=full_env,
            shell=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git {args[0]} failed: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test User")
    # Disable hooks for tests
    git("config", "core.hooksPath", "/dev/null")

    readme = path / "README.md"
    readme.write_text("# Test repo\n")
    git("add", "README.md")
    git("commit", "--no-verify", "-m", "Initial commit",
        "--author", "Test User <test@example.com>")

    return git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """
    A real temporary Git repository.
    Returns (repo_root, initial_commit_sha).
    """
    sha = _init_real_git_repo(tmp_path)
    return tmp_path, sha


@pytest.fixture
def worktree_base(tmp_path: Path) -> Path:
    """
    A base directory for autonomous worktrees that is a SIBLING of the repo
    root, thus guaranteed to be outside the primary worktree.
    """
    base = tmp_path.parent / f".ai_operator_wt_{tmp_path.name}"
    base.mkdir(parents=True, exist_ok=True)
    return base


@pytest.fixture
def manager(repo: tuple[Path, str], worktree_base: Path) -> WorkspaceManager:
    repo_root, _ = repo
    return WorkspaceManager(repo_root=repo_root, worktree_base_dir=worktree_base)


@pytest.fixture
def base_sha(repo: tuple[Path, str]) -> str:
    _, sha = repo
    return sha


# ────────────────────────────────────────────────────────────────────────────
# 1. Repository root detection
# ────────────────────────────────────────────────────────────────────────────

def test_repository_root_detection(repo: tuple[Path, str]) -> None:
    """WorkspaceManager correctly identifies the repository root."""
    repo_root, _ = repo
    wm = WorkspaceManager(repo_root=repo_root)
    assert wm.get_repository_root() == repo_root.resolve()


def test_non_git_directory_raises(tmp_path: Path) -> None:
    """Initialising WorkspaceManager on a non-git directory raises NotAGitRepositoryError."""
    from app.workspace.exceptions import NotAGitRepositoryError
    import tempfile
    with tempfile.TemporaryDirectory() as isolated:
        non_git = Path(isolated) / "not_a_repo"
        non_git.mkdir()
        with pytest.raises(NotAGitRepositoryError):
            WorkspaceManager(repo_root=non_git, worktree_base_dir=non_git.parent)


# ────────────────────────────────────────────────────────────────────────────
# 2. Primary worktree detection
# ────────────────────────────────────────────────────────────────────────────

def test_primary_worktree_detection(manager: WorkspaceManager, repo: tuple[Path, str]) -> None:
    """Primary worktree path matches the repo root."""
    repo_root, _ = repo
    primary = manager.get_primary_worktree_path()
    assert primary == repo_root.resolve()


# ────────────────────────────────────────────────────────────────────────────
# 3. Successful isolated worktree creation
# ────────────────────────────────────────────────────────────────────────────

def test_workspace_creation_succeeds(manager: WorkspaceManager, base_sha: str) -> None:
    """Create workspace returns valid WorkspaceInfo."""
    info = manager.create_workspace(
        task_id="task_abc123",
        base_commit_sha=base_sha,
    )
    assert info.task_id == "task_abc123"
    assert info.base_commit_sha == base_sha
    assert info.current_commit_sha == base_sha
    assert Path(info.autonomous_worktree_path).exists()
    assert info.status == WorkspaceStatus.ACTIVE


# ────────────────────────────────────────────────────────────────────────────
# 4. Correct task-specific branch creation
# ────────────────────────────────────────────────────────────────────────────

def test_branch_name_is_task_specific(manager: WorkspaceManager, base_sha: str) -> None:
    """Created branch name follows the ai-operator/task/<task_id> scheme."""
    info = manager.create_workspace(
        task_id="task_br001",
        base_commit_sha=base_sha,
    )
    assert info.branch_name == "ai-operator/task/task_br001"

    # Verify the branch actually exists in Git
    runner = GitRunner(cwd=Path(info.repository_root))
    result = runner.run(["branch", "--list", info.branch_name], check=False)
    assert info.branch_name in result.stdout


# ────────────────────────────────────────────────────────────────────────────
# 5. Exact base commit SHA recorded
# ────────────────────────────────────────────────────────────────────────────

def test_base_commit_sha_exact(manager: WorkspaceManager, base_sha: str) -> None:
    """WorkspaceInfo records the exact, full 40-char SHA used as base."""
    info = manager.create_workspace(
        task_id="task_sha001",
        base_commit_sha=base_sha,
    )
    assert len(info.base_commit_sha) == 40
    assert info.base_commit_sha == base_sha

    # Verify via Git in the autonomous worktree
    runner = GitRunner(cwd=Path(info.autonomous_worktree_path))
    result = runner.run(["rev-parse", "HEAD"])
    assert result.first_line() == base_sha


# ────────────────────────────────────────────────────────────────────────────
# 6. Primary worktree remains unchanged after workspace creation
# ────────────────────────────────────────────────────────────────────────────

def test_primary_worktree_head_unchanged(manager: WorkspaceManager, repo: tuple[Path, str], base_sha: str) -> None:
    """After creating an autonomous workspace, the primary worktree HEAD is unchanged."""
    repo_root, _ = repo
    primary_head_before = manager.get_current_head()

    manager.create_workspace(
        task_id="task_primary001",
        base_commit_sha=base_sha,
    )

    primary_head_after = manager.get_current_head()
    assert primary_head_before == primary_head_after


# ────────────────────────────────────────────────────────────────────────────
# 7. Primary worktree files remain untouched
# ────────────────────────────────────────────────────────────────────────────

def test_primary_worktree_files_untouched(manager: WorkspaceManager, repo: tuple[Path, str], base_sha: str) -> None:
    """Files in the primary worktree are unchanged after workspace creation."""
    repo_root, _ = repo
    readme = repo_root / "README.md"
    original_content = readme.read_text()

    info = manager.create_workspace(
        task_id="task_files001",
        base_commit_sha=base_sha,
    )

    assert readme.read_text() == original_content

    # Also verify the autonomous worktree has its own copy
    autonomous_readme = Path(info.autonomous_worktree_path) / "README.md"
    assert autonomous_readme.exists()
    # Content is the same (same base commit)
    assert autonomous_readme.read_text() == original_content


# ────────────────────────────────────────────────────────────────────────────
# 8. Autonomous worktree is a real Git worktree
# ────────────────────────────────────────────────────────────────────────────

def test_autonomous_worktree_is_real_git_worktree(manager: WorkspaceManager, base_sha: str) -> None:
    """The autonomous path appears in `git worktree list --porcelain`."""
    info = manager.create_workspace(
        task_id="task_real001",
        base_commit_sha=base_sha,
    )
    registered = manager.list_registered_worktrees()
    paths = [wt.get("path", "") for wt in registered]
    # Normalise separators for comparison
    norm_auto = str(Path(info.autonomous_worktree_path)).replace("\\", "/")
    norm_paths = [p.replace("\\", "/") for p in paths]
    assert any(norm_auto in p or p in norm_auto for p in norm_paths), (
        f"Autonomous worktree {norm_auto!r} not found in registered worktrees: {norm_paths}"
    )


# ────────────────────────────────────────────────────────────────────────────
# 9. Status inspection
# ────────────────────────────────────────────────────────────────────────────

def test_status_clean_on_fresh_workspace(manager: WorkspaceManager, base_sha: str) -> None:
    """A freshly created autonomous worktree reports CLEAN status."""
    info = manager.create_workspace(
        task_id="task_status001",
        base_commit_sha=base_sha,
    )
    status = manager.inspect_workspace_status(info)
    assert status == WorkspaceStatus.CLEAN


def test_status_dirty_after_modification(manager: WorkspaceManager, base_sha: str) -> None:
    """After writing an unstaged file, status becomes DIRTY."""
    info = manager.create_workspace(
        task_id="task_status002",
        base_commit_sha=base_sha,
    )
    # Write a new file in the autonomous worktree
    new_file = Path(info.autonomous_worktree_path) / "new_feature.py"
    new_file.write_text("# new feature\n")

    status = manager.inspect_workspace_status(info)
    assert status == WorkspaceStatus.DIRTY


# ────────────────────────────────────────────────────────────────────────────
# 10. Checkpoint commit creation
# ────────────────────────────────────────────────────────────────────────────

def test_checkpoint_creation(manager: WorkspaceManager, base_sha: str) -> None:
    """create_checkpoint produces a CheckpointInfo with a verified SHA."""
    info = manager.create_workspace(
        task_id="task_cp001",
        base_commit_sha=base_sha,
    )
    target_file = Path(info.autonomous_worktree_path) / "feature.py"
    target_file.write_text("def hello(): pass\n")

    cp = manager.create_checkpoint(
        task_id="task_cp001",
        workspace=info,
        intended_paths=["feature.py"],
        message="Add hello function",
    )
    assert cp.task_id == "task_cp001"
    assert len(cp.commit_sha) == 40
    assert re.match(r"^[0-9a-f]{40}$", cp.commit_sha)
    assert "checkpoint" in cp.commit_message.lower()


# ────────────────────────────────────────────────────────────────────────────
# 11. Checkpoint contains ONLY intended files
# ────────────────────────────────────────────────────────────────────────────

def test_checkpoint_contains_only_intended_files(manager: WorkspaceManager, base_sha: str) -> None:
    """Checkpoint commit only stages the explicitly supplied file paths."""
    info = manager.create_workspace(
        task_id="task_cp002",
        base_commit_sha=base_sha,
    )
    wt = Path(info.autonomous_worktree_path)

    # Create two files — only stage one
    intended = wt / "intended.py"
    intended.write_text("# intended\n")
    unintended = wt / "unintended.py"
    unintended.write_text("# NOT for this checkpoint\n")

    cp = manager.create_checkpoint(
        task_id="task_cp002",
        workspace=info,
        intended_paths=["intended.py"],
        message="Only intended file",
    )

    # Inspect the commit's tree — must contain only intended.py as a new file
    runner = GitRunner(cwd=wt)
    diff_result = runner.run(
        ["show", "--name-only", "--format=", cp.commit_sha]
    )
    committed_files = [
        l.strip() for l in diff_result.stdout.splitlines() if l.strip()
    ]
    assert "intended.py" in committed_files
    assert "unintended.py" not in committed_files


# ────────────────────────────────────────────────────────────────────────────
# 12. Checkpoint SHA is verified (40-char hex)
# ────────────────────────────────────────────────────────────────────────────

def test_checkpoint_sha_is_verified_40char(manager: WorkspaceManager, base_sha: str) -> None:
    """CheckpointInfo.commit_sha is always a full 40-char hex SHA."""
    info = manager.create_workspace(
        task_id="task_cp003",
        base_commit_sha=base_sha,
    )
    target = Path(info.autonomous_worktree_path) / "file.txt"
    target.write_text("content\n")

    cp = manager.create_checkpoint(
        task_id="task_cp003",
        workspace=info,
        intended_paths=["file.txt"],
        message="Verify SHA",
    )
    assert re.match(r"^[0-9a-f]{40}$", cp.commit_sha), (
        f"Expected 40-char hex SHA, got: {cp.commit_sha!r}"
    )

    # Cross-check against Git
    runner = GitRunner(cwd=Path(info.autonomous_worktree_path))
    result = runner.run(["rev-parse", "HEAD"])
    assert result.first_line() == cp.commit_sha


# ────────────────────────────────────────────────────────────────────────────
# 13. Multiple tasks receive isolated worktrees
# ────────────────────────────────────────────────────────────────────────────

def test_multiple_tasks_isolated_worktrees(manager: WorkspaceManager, base_sha: str) -> None:
    """Two tasks get distinct, non-overlapping worktrees."""
    info_a = manager.create_workspace(
        task_id="task_iso_a",
        base_commit_sha=base_sha,
    )
    info_b = manager.create_workspace(
        task_id="task_iso_b",
        base_commit_sha=base_sha,
    )

    assert info_a.autonomous_worktree_path != info_b.autonomous_worktree_path
    assert info_a.branch_name != info_b.branch_name
    assert Path(info_a.autonomous_worktree_path).exists()
    assert Path(info_b.autonomous_worktree_path).exists()

    # Write a file in A and verify it does NOT appear in B
    (Path(info_a.autonomous_worktree_path) / "task_a_only.py").write_text("# A\n")
    assert not (Path(info_b.autonomous_worktree_path) / "task_a_only.py").exists()


# ────────────────────────────────────────────────────────────────────────────
# 14. Worktree/branch collision handling
# ────────────────────────────────────────────────────────────────────────────

def test_duplicate_workspace_raises(manager: WorkspaceManager, base_sha: str) -> None:
    """Attempting to create a workspace for the same task_id twice raises WorktreeAlreadyExistsError."""
    manager.create_workspace(
        task_id="task_dup001",
        base_commit_sha=base_sha,
    )
    with pytest.raises(WorktreeAlreadyExistsError):
        manager.create_workspace(
            task_id="task_dup001",
            base_commit_sha=base_sha,
        )


# ────────────────────────────────────────────────────────────────────────────
# 15. Refuse unsafe primary-worktree paths
# ────────────────────────────────────────────────────────────────────────────

def test_rollback_refuses_primary_worktree_path(manager: WorkspaceManager, repo: tuple[Path, str], base_sha: str) -> None:
    """rollback_to_checkpoint refuses if workspace path equals primary worktree."""
    repo_root, _ = repo
    primary_wt = manager.get_primary_worktree_path()

    # Construct a fake workspace that points to the primary worktree
    from app.workspace.models import WorkspaceInfo, WorkspaceStatus
    fake_workspace = WorkspaceInfo(
        task_id="task_prim001",
        repository_root=str(repo_root),
        primary_worktree_path=str(primary_wt),
        autonomous_worktree_path=str(primary_wt),  # ← this is the trap
        branch_name="ai-operator/task/task_prim001",
        base_commit_sha=base_sha,
        current_commit_sha=base_sha,
        status=WorkspaceStatus.ACTIVE,
    )
    with pytest.raises(PrimaryWorktreeViolationError):
        manager.rollback_to_checkpoint(
            task_id="task_prim001",
            workspace=fake_workspace,
            checkpoint_sha=base_sha,
        )


def test_inspect_refuses_primary_worktree_path(manager: WorkspaceManager, repo: tuple[Path, str], base_sha: str) -> None:
    """inspect_workspace_status refuses if workspace path equals primary worktree."""
    repo_root, _ = repo
    primary_wt = manager.get_primary_worktree_path()

    from app.workspace.models import WorkspaceInfo, WorkspaceStatus
    fake_workspace = WorkspaceInfo(
        task_id="task_prim002",
        repository_root=str(repo_root),
        primary_worktree_path=str(primary_wt),
        autonomous_worktree_path=str(primary_wt),
        branch_name="ai-operator/task/task_prim002",
        base_commit_sha=base_sha,
        current_commit_sha=base_sha,
        status=WorkspaceStatus.ACTIVE,
    )
    with pytest.raises(PrimaryWorktreeViolationError):
        manager.inspect_workspace_status(fake_workspace)


# ────────────────────────────────────────────────────────────────────────────
# 16. Refuse path traversal / invalid workspace paths
# ────────────────────────────────────────────────────────────────────────────

def test_invalid_task_id_path_traversal() -> None:
    """task_id containing path separators is rejected by _validate_task_id."""
    from app.workspace.workspace_manager import _validate_task_id
    with pytest.raises(UnsafePathError):
        _validate_task_id("../../../etc/passwd")


def test_invalid_task_id_empty() -> None:
    from app.workspace.workspace_manager import _validate_task_id
    with pytest.raises(UnsafePathError):
        _validate_task_id("")


def test_invalid_task_id_too_long() -> None:
    from app.workspace.workspace_manager import _validate_task_id
    with pytest.raises(UnsafePathError):
        _validate_task_id("a" * 129)


def test_checkpoint_path_traversal_rejected(manager: WorkspaceManager, base_sha: str) -> None:
    """intended_paths cannot escape the autonomous worktree."""
    info = manager.create_workspace(
        task_id="task_trav001",
        base_commit_sha=base_sha,
    )
    with pytest.raises(UnsafePathError):
        manager.create_checkpoint(
            task_id="task_trav001",
            workspace=info,
            intended_paths=["../../README.md"],
            message="Traversal attempt",
        )


def test_checkpoint_absolute_path_outside_worktree_rejected(manager: WorkspaceManager, repo: tuple[Path, str], base_sha: str) -> None:
    """Absolute paths pointing outside the worktree are rejected."""
    repo_root, _ = repo
    info = manager.create_workspace(
        task_id="task_trav002",
        base_commit_sha=base_sha,
    )
    outside_path = str(repo_root / "README.md")
    with pytest.raises(UnsafePathError):
        manager.create_checkpoint(
            task_id="task_trav002",
            workspace=info,
            intended_paths=[outside_path],
            message="Outside path attempt",
        )


# ────────────────────────────────────────────────────────────────────────────
# 17. Dirty-primary-worktree behaviour (fail-closed by default)
# ────────────────────────────────────────────────────────────────────────────

def test_dirty_primary_worktree_refuses_by_default(repo: tuple[Path, str]) -> None:
    """When primary worktree has uncommitted changes, create_workspace fails by default."""
    repo_root, sha = repo
    wm = WorkspaceManager(repo_root=repo_root)

    # Dirty the primary worktree
    (repo_root / "dirty_file.txt").write_text("uncommitted change\n")

    with pytest.raises(DirtyPrimaryWorktreeError) as exc_info:
        wm.create_workspace(
            task_id="task_dirty001",
            base_commit_sha=sha,
            # allow_dirty_primary defaults to False
        )
    assert len(exc_info.value.dirty_files) >= 1


def test_dirty_primary_worktree_always_refuses(repo: tuple[Path, str]) -> None:
    """create_workspace always refuses when the primary worktree is dirty (fail-closed)."""
    repo_root, sha = repo
    wm = WorkspaceManager(repo_root=repo_root)

    (repo_root / "dirty_file.txt").write_text("uncommitted change\n")

    with pytest.raises(DirtyPrimaryWorktreeError):
        wm.create_workspace(
            task_id="task_dirty002",
            base_commit_sha=sha,
        )


# ────────────────────────────────────────────────────────────────────────────
# 18. Safe rollback inside autonomous worktree
# ────────────────────────────────────────────────────────────────────────────

def test_rollback_to_checkpoint(manager: WorkspaceManager, base_sha: str) -> None:
    """rollback_to_checkpoint resets the autonomous worktree to a prior SHA."""
    info = manager.create_workspace(
        task_id="task_roll001",
        base_commit_sha=base_sha,
    )
    wt = Path(info.autonomous_worktree_path)

    # Create a checkpoint
    (wt / "step1.py").write_text("# step 1\n")
    cp1 = manager.create_checkpoint(
        task_id="task_roll001",
        workspace=info,
        intended_paths=["step1.py"],
        message="Step 1",
    )
    checkpoint_sha = cp1.commit_sha

    # Make another commit to advance HEAD
    (wt / "step2.py").write_text("# step 2\n")
    cp2 = manager.create_checkpoint(
        task_id="task_roll001",
        workspace=info,
        intended_paths=["step2.py"],
        message="Step 2",
    )
    assert cp2.commit_sha != checkpoint_sha

    # Rollback to cp1
    manager.rollback_to_checkpoint(
        task_id="task_roll001",
        workspace=info,
        checkpoint_sha=checkpoint_sha,
    )

    runner = GitRunner(cwd=wt)
    head_after = runner.run(["rev-parse", "HEAD"]).first_line()
    assert head_after == checkpoint_sha

    # step2.py should not be present after rollback
    assert not (wt / "step2.py").exists()
    # Primary worktree is unaffected
    primary_head = manager.get_current_head()
    assert primary_head == base_sha


# ────────────────────────────────────────────────────────────────────────────
# 19. Rollback cannot target primary worktree
# ────────────────────────────────────────────────────────────────────────────
# (covered in test 15 — see test_rollback_refuses_primary_worktree_path)


# ────────────────────────────────────────────────────────────────────────────
# 20. Worktree removal
# ────────────────────────────────────────────────────────────────────────────

def test_remove_workspace(manager: WorkspaceManager, base_sha: str) -> None:
    """remove_workspace deletes the autonomous worktree and its branch."""
    info = manager.create_workspace(
        task_id="task_rm001",
        base_commit_sha=base_sha,
    )
    wt_path = Path(info.autonomous_worktree_path)
    assert wt_path.exists()

    manager.remove_workspace(task_id="task_rm001", workspace=info)

    assert not wt_path.exists()

    # Branch should be deleted
    runner = GitRunner(cwd=manager.get_repository_root())
    result = runner.run(["branch", "--list", info.branch_name], check=False)
    assert info.branch_name not in result.stdout


# ────────────────────────────────────────────────────────────────────────────
# 21. Failed creation cleanup (simulated)
# ────────────────────────────────────────────────────────────────────────────

def test_workspace_nonexistent_raises_worktreetnotfound(manager: WorkspaceManager, repo: tuple[Path, str], base_sha: str) -> None:
    """Inspecting a workspace whose path has been deleted raises WorktreeNotFoundError."""
    info = manager.create_workspace(
        task_id="task_clean001",
        base_commit_sha=base_sha,
    )
    # Simulate partial failure: delete directory without going through manager
    import shutil
    shutil.rmtree(info.autonomous_worktree_path, ignore_errors=True)

    with pytest.raises(WorktreeNotFoundError):
        manager.inspect_workspace_status(info)


def test_invalid_base_sha_raises(manager: WorkspaceManager) -> None:
    """Creating workspace with a non-existent base SHA raises GitCommandError."""
    with pytest.raises(GitCommandError):
        manager.create_workspace(
            task_id="task_badsha",
            base_commit_sha="0" * 40,
        )


# ────────────────────────────────────────────────────────────────────────────
# 22. Git command timeout/error handling (GitRunner)
# ────────────────────────────────────────────────────────────────────────────

def test_git_runner_fails_on_bad_command(repo: tuple[Path, str]) -> None:
    """GitRunner raises GitCommandError on non-zero exit."""
    repo_root, _ = repo
    runner = GitRunner(cwd=repo_root)
    with pytest.raises(GitCommandError):
        runner.run(["rev-parse", "refs/heads/branch_that_does_not_exist_xyz"])


def test_git_timeout_raises_git_timeout_error(repo: tuple[Path, str]) -> None:
    """GitRunner raises GitTimeoutError when timeout is exceeded."""
    from app.workspace.exceptions import GitTimeoutError
    repo_root, _ = repo
    runner = GitRunner(cwd=repo_root, timeout=0.0001)  # absurdly small timeout
    with pytest.raises(GitTimeoutError):
        runner.run(["status", "--porcelain"])


def test_git_runner_check_false_returns_result(repo: tuple[Path, str]) -> None:
    """With check=False, GitRunner returns a GitResult even on failure."""
    repo_root, _ = repo
    runner = GitRunner(cwd=repo_root)
    result = runner.run(
        ["rev-parse", "refs/heads/nonexistent_xyz"],
        check=False,
    )
    assert isinstance(result, GitResult)
    assert result.returncode != 0


# ────────────────────────────────────────────────────────────────────────────
# 23. No shell=True for Git execution
# ────────────────────────────────────────────────────────────────────────────

def test_git_runner_never_uses_shell_true(repo: tuple[Path, str]) -> None:
    """
    Verify via source inspection that GitRunner never passes shell=True.
    This is a code-level regression guard.

    We check that no *assignment* of shell=True appears in the source.
    Comments referencing 'shell=True' are acceptable; assignments are not.
    """
    import ast
    import inspect
    from app.workspace import git_runner as gr_module
    source = inspect.getsource(gr_module)

    # Parse the source into an AST and find all keyword arguments
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    # Check if the value is the literal True
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        raise AssertionError(
                            f"SECURITY: git_runner.py passes shell=True at line {node.lineno}. "
                            "This is forbidden."
                        )

    # Confirm shell=False is explicitly set (defence-in-depth check)
    found_shell_false = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                    found_shell_false = True
    assert found_shell_false, "Expected explicit shell=False keyword in git_runner.py"


# ────────────────────────────────────────────────────────────────────────────
# 24. No arbitrary Git command API
# ────────────────────────────────────────────────────────────────────────────

def test_disallowed_git_subcommand_raises_value_error(repo: tuple[Path, str]) -> None:
    """GitRunner rejects sub-commands not in the allowlist."""
    repo_root, _ = repo
    runner = GitRunner(cwd=repo_root)
    with pytest.raises(ValueError, match="not permitted"):
        runner.run(["push", "--force", "origin", "main"])


def test_disallowed_subcommand_fetch_rejected(repo: tuple[Path, str]) -> None:
    """'fetch' is not in the allowlist and should be rejected."""
    repo_root, _ = repo
    runner = GitRunner(cwd=repo_root)
    with pytest.raises(ValueError, match="not permitted"):
        runner.run(["fetch", "--all"])


# ────────────────────────────────────────────────────────────────────────────
# 25 & 26. Stage 6.2 / 6.3 regression — import guards
# ────────────────────────────────────────────────────────────────────────────

def test_stage_62_imports_intact() -> None:
    """Stage 6.2 domain types are still importable."""
    from app.tasks.models import DevelopmentTask, TaskStatus  # noqa: F401
    from app.repositories.task_repository import TaskRepository  # noqa: F401


def test_stage_63_imports_intact() -> None:
    """Stage 6.3 state machine types are still importable."""
    from app.tasks.state_machine import TaskStateMachine  # noqa: F401
    from app.tasks.exceptions import (  # noqa: F401
        InvalidStateTransitionError,
        StaleStateTransitionError,
        TaskNotFoundError,
    )


# ────────────────────────────────────────────────────────────────────────────
# Additional edge cases
# ────────────────────────────────────────────────────────────────────────────

def test_checkpoint_empty_intended_paths_raises(manager: WorkspaceManager, base_sha: str) -> None:
    """Empty intended_paths raises CheckpointError immediately."""
    info = manager.create_workspace(
        task_id="task_empty001",
        base_commit_sha=base_sha,
    )
    with pytest.raises(CheckpointError):
        manager.create_checkpoint(
            task_id="task_empty001",
            workspace=info,
            intended_paths=[],
            message="Empty checkpoint",
        )


def test_checkpoint_task_id_mismatch_raises(manager: WorkspaceManager, base_sha: str) -> None:
    """task_id mismatch between argument and workspace raises CheckpointError."""
    info = manager.create_workspace(
        task_id="task_mm001",
        base_commit_sha=base_sha,
    )
    with pytest.raises(CheckpointError):
        manager.create_checkpoint(
            task_id="task_WRONG",
            workspace=info,
            intended_paths=["file.py"],
            message="Should fail",
        )


def test_rollback_invalid_sha_format_raises(manager: WorkspaceManager, base_sha: str) -> None:
    """Rollback with a non-40-char SHA raises RollbackViolationError."""
    info = manager.create_workspace(
        task_id="task_roll_bad",
        base_commit_sha=base_sha,
    )
    with pytest.raises(RollbackViolationError, match="40-character"):
        manager.rollback_to_checkpoint(
            task_id="task_roll_bad",
            workspace=info,
            checkpoint_sha="abc123",  # not 40 chars
        )


def test_remove_workspace_task_id_mismatch_raises(manager: WorkspaceManager, base_sha: str) -> None:
    """remove_workspace raises PrimaryWorktreeViolationError on task_id mismatch."""
    info = manager.create_workspace(
        task_id="task_rmmm001",
        base_commit_sha=base_sha,
    )
    with pytest.raises(PrimaryWorktreeViolationError):
        manager.remove_workspace(task_id="WRONG_ID", workspace=info)


def test_workspace_info_is_immutable(manager: WorkspaceManager, base_sha: str) -> None:
    """WorkspaceInfo is frozen (Pydantic) and cannot be mutated."""
    info = manager.create_workspace(
        task_id="task_frozen001",
        base_commit_sha=base_sha,
    )
    with pytest.raises(Exception):  # ValidationError or TypeError
        info.task_id = "mutated"  # type: ignore[misc]


def test_git_result_first_line(repo: tuple[Path, str]) -> None:
    """GitResult.first_line() returns the first non-empty stripped line."""
    repo_root, sha = repo
    runner = GitRunner(cwd=repo_root)
    result = runner.run(["rev-parse", "HEAD"])
    first = result.first_line()
    assert len(first) == 40
    assert first == sha


def test_primary_worktree_status_clean(repo: tuple[Path, str]) -> None:
    """inspect_primary_worktree_status returns empty list on clean repo."""
    repo_root, _ = repo
    wm = WorkspaceManager(repo_root=repo_root)
    dirty = wm.inspect_primary_worktree_status()
    assert dirty == []


def test_checkpoint_fails_if_index_already_dirty(manager: WorkspaceManager, base_sha: str) -> None:
    """create_checkpoint raises CheckpointError if index contains pre-existing staged changes."""
    info = manager.create_workspace(
        task_id="task_dirty_idx001",
        base_commit_sha=base_sha,
    )
    wt = Path(info.autonomous_worktree_path)
    # Manually stage a file behind the manager's back
    (wt / "dirty_staged.py").write_text("# pre-staged\n")
    runner = GitRunner(cwd=wt)
    runner.run(["add", "dirty_staged.py"])

    # Now create another file intended for checkpoint
    (wt / "intended.py").write_text("# intended\n")
    with pytest.raises(CheckpointError, match="pre-existing staged changes"):
        manager.create_checkpoint(
            task_id="task_dirty_idx001",
            workspace=info,
            intended_paths=["intended.py"],
            message="Should fail due to dirty index",
        )


def test_rollback_refuses_unregistered_worktree_path(manager: WorkspaceManager, repo: tuple[Path, str], base_sha: str) -> None:
    """rollback_to_checkpoint raises WorktreeNotFoundError for an unregistered directory path."""
    repo_root, _ = repo
    unregistered_dir = repo_root.parent / f".unregistered_wt_{repo_root.name}"
    unregistered_dir.mkdir(parents=True, exist_ok=True)
    try:
        from app.workspace.models import WorkspaceInfo, WorkspaceStatus
        fake_info = WorkspaceInfo(
            task_id="task_unreg001",
            repository_root=str(repo_root),
            primary_worktree_path=str(repo_root),
            autonomous_worktree_path=str(unregistered_dir),
            branch_name="ai-operator/task/task_unreg001",
            base_commit_sha=base_sha,
            current_commit_sha=base_sha,
            status=WorkspaceStatus.ACTIVE,
        )
        with pytest.raises(WorktreeNotFoundError):
            manager.rollback_to_checkpoint(
                task_id="task_unreg001",
                workspace=fake_info,
                checkpoint_sha=base_sha,
            )
    finally:
        import shutil
        shutil.rmtree(unregistered_dir, ignore_errors=True)


def test_rollback_refuses_branch_mismatch(manager: WorkspaceManager, base_sha: str) -> None:
    """rollback_to_checkpoint raises PrimaryWorktreeViolationError if registered branch does not match."""
    info = manager.create_workspace(
        task_id="task_bm001",
        base_commit_sha=base_sha,
    )
    from app.workspace.models import WorkspaceInfo
    fake_info = WorkspaceInfo(
        task_id=info.task_id,
        repository_root=info.repository_root,
        primary_worktree_path=info.primary_worktree_path,
        autonomous_worktree_path=info.autonomous_worktree_path,
        branch_name="ai-operator/task/TAMPERED_BRANCH",
        base_commit_sha=info.base_commit_sha,
        current_commit_sha=info.current_commit_sha,
        status=info.status,
    )
    with pytest.raises(PrimaryWorktreeViolationError):
        manager.rollback_to_checkpoint(
            task_id="task_bm001",
            workspace=fake_info,
            checkpoint_sha=base_sha,
        )
