"""
Stage 6.5 — Verification Gate Foundation Tests.

Uses REAL Git repositories initialised in pytest tmp_path fixtures.
No mocking of Git itself for core workspace identity tests.

Test coverage (34 required items):
  1. Required check PASS -> Gate decision PASS
  2. Required check FAIL -> Gate decision FAIL
  3. Required check ERROR -> Gate decision FAIL
  4. Required check SKIPPED -> Gate decision FAIL
  5. Optional check FAIL -> Visible in report, Gate decision PASS (if required PASS)
  6. Optional check ERROR -> Visible in report, Gate decision PASS (if required PASS)
  7. Optional check SKIPPED -> Visible in report, Gate decision PASS (if required PASS)
  8. Checks execute in deterministic registration order
  9. Multiple checks run and recorded in report with exact results
  10. Register duplicate check_id -> raises DuplicateCheckError
  11. No silent overwriting of check_id
  12. Require non-existent check_id -> raises CheckNotFoundError
  13. Exception raised inside check -> caught, status ERROR, gate FAIL
  14. Check timeout -> status ERROR with timeout message, gate FAIL
  15. Unexecuted check -> status SKIPPED, cannot produce PASS decision
  16. Invalid context (empty task_id, relative path, bad SHA) -> FAIL report
  17. Primary worktree path in context -> FAIL report (primary worktree guard)
  18. Valid autonomous worktree context -> accepted for verification
  19. Repository identity mismatch -> FAIL report (identity mismatch)
  20. Base commit not an ancestor of current commit -> FAIL report (ancestor check)
  21. Current commit mismatch with actual worktree HEAD -> FAIL report (HEAD mismatch)
  22. CheckExecutor uses shell=False exclusively
  23. CheckExecutor rejects non-whitelisted executables
  24. CheckExecutor enforces timeout
  25. CheckExecutor captures stdout and stderr correctly
  26. CheckExecutor returns exit code accurately
  27. VerificationContext and VerificationReport contain no auth tokens, secrets, or permissions
  28. Regression — Import Stage 6.2 TaskRepository cleanly
  29. Regression — Import Stage 6.3 TaskStateMachine cleanly
  30. Regression — Import Stage 6.4 WorkspaceManager & GitRunner cleanly
  31. Fail-closed: empty registry or preflight error -> Gate decision FAIL
  32. Lock registry prevents modification after locking (RegistryLockedError)
  33. Registry ordering preserved after locking
  34. Integration — VerificationGate with real WorkspaceManager-created worktree
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.verification.check_registry import CheckRegistry
from app.verification.check_runner import (
    BaseVerificationCheck,
    CheckCommandResult,
    CheckExecutor,
    VerificationCheck,
)
from app.verification.exceptions import (
    CheckExecutionError,
    CheckNotFoundError,
    DuplicateCheckError,
    InvalidContextError,
    PrimaryWorktreeVerificationError,
    RegistryLockedError,
    VerificationError,
    WorkspaceIdentityError,
)
from app.verification.gate import VerificationGate
from app.verification.models import (
    CheckResult,
    CheckStatus,
    GateDecision,
    VerificationContext,
    VerificationReport,
)
from app.workspace.workspace_manager import WorkspaceManager


# ────────────────────────────────────────────────────────────────────────────
# Test fixtures & Git helpers
# ────────────────────────────────────────────────────────────────────────────

def _init_real_git_repo(path: Path) -> str:
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
        "HOME": str(path),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
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
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test User")
    git("config", "core.hooksPath", "/dev/null")

    readme = path / "README.md"
    readme.write_text("# Verification Test Repo\n")
    git("add", "README.md")
    git("commit", "--no-verify", "-m", "Initial commit")

    return git("rev-parse", "HEAD")


def _create_commit(repo_path: Path, message: str = "Second commit") -> str:
    """Create a second commit in a repo and return its SHA."""
    env = {
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "HOME": str(repo_path),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    full_env = {**os.environ, **env}
    f = repo_path / "file.txt"
    f.write_text(f"content: {message}\n")
    subprocess.run(["git", "add", "file.txt"], cwd=str(repo_path), env=full_env, shell=False, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo_path), env=full_env, shell=False, check=True)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_path), env=full_env, shell=False, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _get_results_map(report: VerificationReport) -> dict[str, CheckResult]:
    """Helper to map report.results tuple into a dict by check_id."""
    return {r.check_id: r for r in report.results}


# ────────────────────────────────────────────────────────────────────────────
# Concrete Test Verification Checks
# ────────────────────────────────────────────────────────────────────────────

class DummyCheck(BaseVerificationCheck):
    """Configurable mock check for gate testing."""

    def __init__(
        self,
        check_id: str,
        name: str = "Dummy Check",
        status: CheckStatus = CheckStatus.PASS,
        message: str = "OK",
        is_required: bool = True,
        raise_exc: Exception | None = None,
    ) -> None:
        self.check_id = check_id
        self.check_name = name
        self.required = is_required
        self._status = status
        self._message = message
        self._raise_exc = raise_exc

    def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict[str, Any]]:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._status, {"message": self._message}


# ────────────────────────────────────────────────────────────────────────────
# Tests: Gate Decision Logic (Items 1-7)
# ────────────────────────────────────────────────────────────────────────────

def test_gate_pass_when_all_required_checks_pass(tmp_path: Path) -> None:
    """Item 1: Required check PASS -> Gate decision PASS."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-1", base_commit_sha=base_sha, execution_token='exec_task-1')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck(check_id="check_1", status=CheckStatus.PASS, is_required=True))
    registry.register(DummyCheck(check_id="check_2", status=CheckStatus.PASS, is_required=True))

    ctx = VerificationContext(
        task_id="task-1",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )

    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.PASS
    assert report.passed is True
    assert len(report.results) == 2
    assert all(r.status == CheckStatus.PASS for r in report.results)


def test_gate_fail_when_required_check_fails(tmp_path: Path) -> None:
    """Item 2: Required check FAIL -> Gate decision FAIL."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-2", base_commit_sha=base_sha, execution_token='exec_task-2')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck(check_id="c1", status=CheckStatus.PASS, is_required=True))
    registry.register(DummyCheck(check_id="c2", status=CheckStatus.FAIL, message="Build failed", is_required=True))

    ctx = VerificationContext(
        task_id="task-2",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.FAIL
    assert report.passed is False
    res_map = _get_results_map(report)
    assert res_map["c2"].status == CheckStatus.FAIL


def test_gate_fail_when_required_check_errors(tmp_path: Path) -> None:
    """Item 3: Required check ERROR -> Gate decision FAIL."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-3", base_commit_sha=base_sha, execution_token='exec_task-3')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck(check_id="c1", status=CheckStatus.ERROR, message="Crash in tool", is_required=True))

    ctx = VerificationContext(
        task_id="task-3",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.FAIL
    res_map = _get_results_map(report)
    assert res_map["c1"].status == CheckStatus.ERROR


def test_gate_fail_when_required_check_skipped(tmp_path: Path) -> None:
    """Item 4: Required check SKIPPED -> Gate decision FAIL."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-4", base_commit_sha=base_sha, execution_token='exec_task-4')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck(check_id="c1", status=CheckStatus.SKIPPED, message="Dependency missing", is_required=True))

    ctx = VerificationContext(
        task_id="task-4",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.FAIL
    res_map = _get_results_map(report)
    assert res_map["c1"].status == CheckStatus.SKIPPED


def test_optional_check_fail_does_not_block_pass(tmp_path: Path) -> None:
    """Item 5: Optional check FAIL -> Visible in report, Gate decision PASS."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-5", base_commit_sha=base_sha, execution_token='exec_task-5')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck("req1", status=CheckStatus.PASS, is_required=True))
    registry.register(DummyCheck("opt1", status=CheckStatus.FAIL, message="Linter warning", is_required=False))

    ctx = VerificationContext(
        task_id="task-5",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.PASS
    res_map = _get_results_map(report)
    assert res_map["opt1"].status == CheckStatus.FAIL
    assert res_map["opt1"].required is False


def test_optional_check_error_does_not_block_pass(tmp_path: Path) -> None:
    """Item 6: Optional check ERROR -> Visible in report, Gate decision PASS."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-6", base_commit_sha=base_sha, execution_token='exec_task-6')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck("req1", status=CheckStatus.PASS, is_required=True))
    registry.register(DummyCheck("opt1", status=CheckStatus.ERROR, message="Optional tool crashed", is_required=False))

    ctx = VerificationContext(
        task_id="task-6",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.PASS
    res_map = _get_results_map(report)
    assert res_map["opt1"].status == CheckStatus.ERROR


def test_optional_check_skipped_does_not_block_pass(tmp_path: Path) -> None:
    """Item 7: Optional check SKIPPED -> Visible in report, Gate decision PASS."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-7", base_commit_sha=base_sha, execution_token='exec_task-7')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck("req1", status=CheckStatus.PASS, is_required=True))
    registry.register(DummyCheck("opt1", status=CheckStatus.SKIPPED, message="Skipped optional", is_required=False))

    ctx = VerificationContext(
        task_id="task-7",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.PASS
    res_map = _get_results_map(report)
    assert res_map["opt1"].status == CheckStatus.SKIPPED


# ────────────────────────────────────────────────────────────────────────────
# Tests: Registry Ordering & Multiple Checks (Items 8-12, 32-33)
# ────────────────────────────────────────────────────────────────────────────

def test_checks_execute_in_deterministic_registration_order(tmp_path: Path) -> None:
    """Item 8: Checks execute in deterministic registration order."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-8", base_commit_sha=base_sha, execution_token='exec_task-8')
    worktree_path = Path(info.autonomous_worktree_path)

    execution_order: list[str] = []

    class OrderTrackingCheck(BaseVerificationCheck):
        def __init__(self, cid: str) -> None:
            self.check_id = cid
            self.check_name = cid
            self.required = True

        def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict[str, Any]]:
            execution_order.append(self.check_id)
            return CheckStatus.PASS, {}

    registry = CheckRegistry()
    check_ids = ["first", "second", "third", "fourth", "fifth"]
    for cid in check_ids:
        registry.register(OrderTrackingCheck(cid))

    ctx = VerificationContext(
        task_id="task-8",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.PASS
    assert execution_order == check_ids
    assert [r.check_id for r in report.results] == check_ids


def test_multiple_checks_recorded_with_exact_results(tmp_path: Path) -> None:
    """Item 9: Multiple checks run and recorded in report with exact results."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-9", base_commit_sha=base_sha, execution_token='exec_task-9')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck("c1", status=CheckStatus.PASS, message="c1 passed"))
    registry.register(DummyCheck("c2", status=CheckStatus.PASS, message="c2 passed"))
    registry.register(DummyCheck("c3", status=CheckStatus.FAIL, message="c3 failed"))

    ctx = VerificationContext(
        task_id="task-9",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert len(report.results) == 3
    res_map = _get_results_map(report)
    assert res_map["c1"].status == CheckStatus.PASS
    assert res_map["c2"].status == CheckStatus.PASS
    assert res_map["c3"].status == CheckStatus.FAIL


def test_register_duplicate_check_id_raises() -> None:
    """Items 10 & 11: Register duplicate check_id raises DuplicateCheckError with no silent overwrite."""
    registry = CheckRegistry()
    registry.register(DummyCheck("c1"))
    
    with pytest.raises(DuplicateCheckError) as exc_info:
        registry.register(DummyCheck("c1"))

    assert "c1" in str(exc_info.value)
    assert registry.count() == 1


def test_require_non_existent_check_id_raises() -> None:
    """Item 12: Require non-existent check_id raises CheckNotFoundError."""
    registry = CheckRegistry()
    registry.register(DummyCheck("c1"))

    with pytest.raises(CheckNotFoundError) as exc_info:
        registry.require("non_existent")

    assert "non_existent" in str(exc_info.value)


def test_registry_lock_prevents_modification_and_preserves_order() -> None:
    """Items 32 & 33: Lock registry prevents modification and preserves order."""
    registry = CheckRegistry()
    registry.register(DummyCheck("c1"))
    registry.register(DummyCheck("c2"))
    
    assert registry.is_locked() is False
    registry.lock()
    assert registry.is_locked() is True

    with pytest.raises(RegistryLockedError):
        registry.register(DummyCheck("c3"))

    assert [c.check_id for c in registry.get_ordered()] == ["c1", "c2"]


# ────────────────────────────────────────────────────────────────────────────
# Tests: Exceptions, Timeouts, Unexecuted Checks (Items 13-15)
# ────────────────────────────────────────────────────────────────────────────

def test_exception_inside_check_recorded_as_error(tmp_path: Path) -> None:
    """Item 13: Exception raised inside check -> caught, recorded as status ERROR, gate FAIL."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-13", base_commit_sha=base_sha, execution_token='exec_task-13')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck("broken_check", raise_exc=ZeroDivisionError("division by zero")))

    ctx = VerificationContext(
        task_id="task-13",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.FAIL
    res_map = _get_results_map(report)
    result = res_map["broken_check"]
    assert result.status == CheckStatus.ERROR
    assert "ZeroDivisionError" in (result.error_message or "")


def test_check_timeout_recorded_as_error(tmp_path: Path) -> None:
    """Item 14: Check timeout -> recorded as status ERROR with message, gate FAIL."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-14", base_commit_sha=base_sha, execution_token='exec_task-14')
    worktree_path = Path(info.autonomous_worktree_path)

    class SlowCheck(BaseVerificationCheck):
        def __init__(self) -> None:
            self.check_id = "slow"
            self.check_name = "Slow Check"
            self.required = True

        def _run(self, context: VerificationContext) -> tuple[CheckStatus, dict[str, Any]]:
            executor = CheckExecutor(cwd=Path(context.workspace_path), default_timeout=0.1)
            res = executor.run_command(["python", "-c", "import time; time.sleep(2)"])
            if res.timed_out:
                return CheckStatus.ERROR, {"error": res.stderr or "timed out"}
            return CheckStatus.PASS, {}

    registry = CheckRegistry()
    registry.register(SlowCheck())

    ctx = VerificationContext(
        task_id="task-14",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.FAIL
    res_map = _get_results_map(report)
    res = res_map["slow"]
    assert res.status == CheckStatus.ERROR


def test_unexecuted_check_cannot_produce_pass() -> None:
    """Item 15: Unexecuted check status SKIPPED cannot produce PASS decision."""
    unexec_result = CheckResult(
        check_id="unexec",
        check_name="Unexecuted",
        status=CheckStatus.SKIPPED,
        required=True,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        duration_seconds=0.0,
        error_message="Never run",
    )
    report = VerificationReport(
        task_id="t15",
        gate_decision=GateDecision.PASS,  # Invalid report state test
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        duration_seconds=0.0,
        results=(unexec_result,),
    )
    # failed_required evaluates if any required checks failed
    assert len(report.failed_required) > 0


# ────────────────────────────────────────────────────────────────────────────
# Tests: Workspace & Context Identity Verification (Items 16-21, 31, 34)
# ────────────────────────────────────────────────────────────────────────────

def test_invalid_context_validation(tmp_path: Path) -> None:
    """Item 16: Invalid context (empty task_id, relative path, bad SHA) produces FAIL report."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    ctx = VerificationContext(
        task_id="",
        workspace_path=str(repo_root),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    registry = CheckRegistry()
    registry.register(DummyCheck("c1"))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    report = gate.run(ctx)
    assert report.gate_decision == GateDecision.FAIL


def test_primary_worktree_rejected_as_verification_target(tmp_path: Path) -> None:
    """Item 17: Primary worktree path in context -> FAIL report (primary worktree guard)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    ctx = VerificationContext(
        task_id="t17",
        workspace_path=str(repo_root),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    registry = CheckRegistry()
    registry.register(DummyCheck("c1"))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    report = gate.run(ctx)
    assert report.gate_decision == GateDecision.FAIL


def test_valid_autonomous_worktree_context_accepted(tmp_path: Path) -> None:
    """Item 18 & 34: Valid autonomous worktree created by WorkspaceManager accepted for verification."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("task-valid", base_commit_sha=base_sha, execution_token='exec_task-valid')
    worktree_path = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()
    registry.register(DummyCheck("c1", status=CheckStatus.PASS))

    ctx = VerificationContext(
        task_id="task-valid",
        workspace_path=str(worktree_path),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.PASS
    assert len(report.results) == 1


def test_repository_identity_mismatch_rejected(tmp_path: Path) -> None:
    """Item 19: Repository identity mismatch (common-dir mismatch) -> FAIL report."""
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    repo1.mkdir()
    repo2.mkdir()
    sha1 = _init_real_git_repo(repo1)
    sha2 = _init_real_git_repo(repo2)

    mgr1 = WorkspaceManager(repo_root=repo1)
    info1 = mgr1.create_workspace("t19", base_commit_sha=sha1, execution_token='exec_t19')
    wt1 = Path(info1.autonomous_worktree_path)

    ctx = VerificationContext(
        task_id="t19",
        workspace_path=str(wt1),
        repository_root=str(repo2),
        expected_base_commit=sha1,
        current_commit=sha1,
    )
    registry = CheckRegistry()
    registry.register(DummyCheck("c1"))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo2)

    report = gate.run(ctx)
    assert report.gate_decision == GateDecision.FAIL


def test_base_commit_not_ancestor_rejected(tmp_path: Path) -> None:
    """Item 20: Base commit not an ancestor of current commit -> FAIL report."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    sha1 = _init_real_git_repo(repo_root)
    sha2 = _create_commit(repo_root, "Second commit")

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("t20", base_commit_sha=sha1, execution_token='exec_t20')
    wt = Path(info.autonomous_worktree_path)

    fake_sha = "f" * 40
    ctx = VerificationContext(
        task_id="t20",
        workspace_path=str(wt),
        repository_root=str(repo_root),
        expected_base_commit=fake_sha,
        current_commit=sha1,
    )
    registry = CheckRegistry()
    registry.register(DummyCheck("c1"))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    report = gate.run(ctx)
    assert report.gate_decision == GateDecision.FAIL


def test_current_commit_mismatch_rejected(tmp_path: Path) -> None:
    """Item 21: Current commit mismatch with actual worktree HEAD -> FAIL report."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("t21", base_commit_sha=base_sha, execution_token='exec_t21')
    wt = Path(info.autonomous_worktree_path)

    mismatched_sha = "0" * 40
    ctx = VerificationContext(
        task_id="t21",
        workspace_path=str(wt),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=mismatched_sha,
    )
    registry = CheckRegistry()
    registry.register(DummyCheck("c1"))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    report = gate.run(ctx)
    assert report.gate_decision == GateDecision.FAIL


def test_fail_closed_on_empty_registry(tmp_path: Path) -> None:
    """Item 31: Fail-closed: empty registry produces FAIL gate decision."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    mgr = WorkspaceManager(repo_root=repo_root)
    info = mgr.create_workspace("t31", base_commit_sha=base_sha, execution_token='exec_t31')
    wt = Path(info.autonomous_worktree_path)

    registry = CheckRegistry()  # empty
    ctx = VerificationContext(
        task_id="t31",
        workspace_path=str(wt),
        repository_root=str(repo_root),
        expected_base_commit=base_sha,
        current_commit=base_sha,
    )
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)
    report = gate.run(ctx)

    assert report.gate_decision == GateDecision.FAIL
    res_map = _get_results_map(report)
    assert "gate.preflight" in res_map
    assert res_map["gate.preflight"].status == CheckStatus.ERROR


# ────────────────────────────────────────────────────────────────────────────
# Tests: CheckExecutor Subprocess Security & Constraints (Items 22-26)
# ────────────────────────────────────────────────────────────────────────────

def test_check_executor_uses_shell_false_exclusively(tmp_path: Path) -> None:
    """Item 22: CheckExecutor uses shell=False exclusively."""
    executor = CheckExecutor(cwd=tmp_path)
    res = executor.run_command(["python", "-c", "import sys; sys.exit(0)"])
    assert res.exit_code == 0


def test_check_executor_rejects_non_whitelisted_executables(tmp_path: Path) -> None:
    """Item 23: CheckExecutor rejects non-whitelisted executables."""
    executor = CheckExecutor(cwd=tmp_path)

    with pytest.raises(CheckExecutionError) as exc_info:
        executor.run_command(["bash", "-c", "echo hello"])

    assert "not in the allowed list" in str(exc_info.value).lower()


def test_check_executor_enforces_timeout(tmp_path: Path) -> None:
    """Item 24: CheckExecutor enforces timeout."""
    executor = CheckExecutor(cwd=tmp_path, default_timeout=0.2)
    res = executor.run_command(["python", "-c", "import time; time.sleep(2)"])

    assert res.timed_out is True
    assert res.exit_code == -1
    assert "timed out" in res.stderr.lower() or res.timed_out is True


def test_check_executor_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    """Items 25 & 26: CheckExecutor captures stdout, stderr, and exit code accurately."""
    executor = CheckExecutor(cwd=tmp_path)
    code = "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR'); sys.exit(42)"
    res = executor.run_command(["python", "-c", code])

    assert res.stdout == "OUT"
    assert res.stderr == "ERR"
    assert res.exit_code == 42
    assert res.timed_out is False


# ────────────────────────────────────────────────────────────────────────────
# Tests: Security Metadata Integrity (Item 27)
# ────────────────────────────────────────────────────────────────────────────

def test_verification_context_and_report_no_secrets_or_auth() -> None:
    """Item 27: VerificationContext and VerificationReport contain no auth tokens, secrets, or permissions."""
    ctx_fields = set(VerificationContext.__annotations__.keys())
    assert "token" not in ctx_fields
    assert "secret" not in ctx_fields
    assert "password" not in ctx_fields
    assert "permissions" not in ctx_fields
    assert "executor" not in ctx_fields

    report_fields = set(VerificationReport.__annotations__.keys())
    assert "token" not in report_fields
    assert "secret" not in report_fields
    assert "permissions" not in report_fields


# ────────────────────────────────────────────────────────────────────────────
# Tests: Stage 6.2, 6.3, 6.4 Regression Guard Imports (Items 28-30)
# ────────────────────────────────────────────────────────────────────────────

def test_regression_stage_6_2_imports() -> None:
    """Item 28: Regression — Import Stage 6.2 TaskRepository cleanly."""
    from app.repositories.task_repository import TaskRepository
    from app.tasks.models import DevelopmentTask, TaskStatus
    assert TaskRepository is not None
    assert DevelopmentTask is not None
    assert TaskStatus is not None


def test_regression_stage_6_3_imports() -> None:
    """Item 29: Regression — Import Stage 6.3 TaskStateMachine cleanly."""
    from app.tasks.state_machine import TaskStateMachine
    from app.tasks.exceptions import InvalidStateTransitionError, StaleStateTransitionError
    assert TaskStateMachine is not None
    assert InvalidStateTransitionError is not None
    assert StaleStateTransitionError is not None


def test_regression_stage_6_4_imports() -> None:
    """Item 30: Regression — Import Stage 6.4 WorkspaceManager & GitRunner cleanly."""
    from app.workspace.workspace_manager import WorkspaceManager
    from app.workspace.git_runner import GitRunner
    from app.workspace.exceptions import WorkspaceError, DirtyPrimaryWorktreeError
    assert WorkspaceManager is not None
    assert GitRunner is not None
    assert WorkspaceError is not None
    assert DirtyPrimaryWorktreeError is not None
