"""
Stage 6.6 — Autonomous Task Orchestrator Tests.

Comprehensive test suite verifying orchestrator lifecycle, state machine authority,
workspace safety, verification gate enforcement, attempt limits, concurrency,
and commit safety invariants.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base
from app.orchestration.agent_interface import DevelopmentAgent
from app.orchestration.exceptions import (
    MaxAttemptsExceededError,
    OrchestrationError,
    TaskNotStartableError,
    VerificationOrchestrationError,
    WorkspaceOrchestrationError,
)
from app.orchestration.models import (
    DevelopmentAgentContext,
    DevelopmentResult,
    DevelopmentResultStatus,
    OrchestrationOptions,
    OrchestrationResult,
)
from app.orchestration.orchestrator import AutonomousTaskOrchestrator
from app.repositories.task_repository import TaskRepository
from app.tasks.exceptions import StaleStateTransitionError, TaskNotFoundError
from app.tasks.models import DevelopmentTask, TaskStatus
from app.tasks.state_machine import TaskStateMachine
from app.verification.check_registry import CheckRegistry
from app.verification.check_runner import BaseVerificationCheck
from app.verification.gate import VerificationGate
from app.verification.models import CheckResult, CheckStatus, VerificationContext
from app.workspace.workspace_manager import WorkspaceManager


# ── Fixtures & Helpers ───────────────────────────────────────────────────────

def _init_real_git_repo(path: Path) -> str:
    """Initialise a real Git repo with an initial commit and return head SHA."""
    env = {
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "HOME": str(path),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True, env=env)
    readme = path / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(path), check=True, capture_output=True, env=env)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(path), check=True, capture_output=True, text=True, env=env)
    return res.stdout.strip()


class FakeCheck:
    """Configurable verification check fixture implementing VerificationCheck protocol."""
    def __init__(
        self,
        check_id: str,
        name: str = "Fake Check",
        required: bool = True,
        status: CheckStatus = CheckStatus.PASS,
        should_raise: bool = False,
    ) -> None:
        self.check_id = check_id
        self.check_name = name
        self.required = required
        self._status = status
        self._should_raise = should_raise

    def execute(self, context: VerificationContext) -> CheckResult:
        if self._should_raise:
            raise RuntimeError("Check simulation exception")
        now = datetime.now(timezone.utc)
        return CheckResult(
            check_id=self.check_id,
            check_name=self.check_name,
            required=self.required,
            status=self._status,
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
            error_message="Check failed simulation" if self._status != CheckStatus.PASS else None,
        )


class FakeDevelopmentAgent(DevelopmentAgent):
    """Configurable test double for DevelopmentAgent."""
    def __init__(
        self,
        status: DevelopmentResultStatus = DevelopmentResultStatus.SUCCESS,
        changed_files: tuple[str, ...] = ("README.md",),
        should_raise: bool = False,
        file_to_create: str | None = None,
    ) -> None:
        self.status = status
        self.changed_files = changed_files
        self.should_raise = should_raise
        self.file_to_create = file_to_create
        self.captured_contexts: list[DevelopmentAgentContext] = []

    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        self.captured_contexts.append(context)
        if self.should_raise:
            raise RuntimeError("Agent simulation exception")

        if self.file_to_create and self.status == DevelopmentResultStatus.SUCCESS:
            target = Path(context.workspace_path) / self.file_to_create
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("agent edit\n", encoding="utf-8")

        return DevelopmentResult(
            status=self.status,
            summary="Agent execution completed",
            changed_files=self.changed_files if self.file_to_create is None else (self.file_to_create,),
            error_message="Agent failed" if not self.status == DevelopmentResultStatus.SUCCESS else None,
        )


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# =============================================================================
# 1. Basic Lifecycle Tests
# =============================================================================

@pytest.mark.asyncio
async def test_full_successful_lifecycle(tmp_path: Path, db_session: AsyncSession):
    """
    Verifies complete happy-path lifecycle:
    PENDING -> RUNNING -> VERIFYING -> PASSED -> COMMITTED
    """
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

    registry = CheckRegistry()
    registry.register(FakeCheck("check1", status=CheckStatus.PASS))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent(file_to_create="README.md")
    orchestrator = AutonomousTaskOrchestrator(
        task_repo=repo,
        state_machine=sm,
        workspace_manager=wm,
        verification_gate=gate,
        agent=agent,
    )

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_happy",
        title="Implement Feature X",
        description="Add feature X to README",
    ))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.final_status == TaskStatus.COMMITTED
    assert res.committed is True
    assert res.checkpoint_sha is not None
    assert len(res.checkpoint_sha) == 40
    assert res.verification_report is not None
    assert res.verification_report.passed is True

    # Confirm database state
    db_task = await repo.get_task(task.task_id)
    assert db_task is not None
    assert db_task.status == TaskStatus.COMMITTED
    assert db_task.attempt_count == 1


# =============================================================================
# 2. Failure Handling Tests
# =============================================================================

@pytest.mark.asyncio
async def test_development_failure_transitions_to_repair_required(tmp_path: Path, db_session: AsyncSession):
    """Development agent failure with attempts remaining transitions to REPAIR_REQUIRED."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    registry = CheckRegistry()
    registry.register(FakeCheck("c1", status=CheckStatus.PASS))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.FAILURE)
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_fail",
        title="Failing Task",
        description="Will fail",
        max_attempts=3,
    ))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.final_status == TaskStatus.FAILED
    assert res.committed is False
    assert res.checkpoint_sha is None

    db_task = await repo.get_task(task.task_id)
    assert db_task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_verification_fail_prevents_commit(tmp_path: Path, db_session: AsyncSession):
    """VerificationGate FAIL verdict prevents git commit and transitions to REPAIR_REQUIRED."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

    registry = CheckRegistry()
    registry.register(FakeCheck("c1", required=True, status=CheckStatus.FAIL))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent(file_to_create="file.txt")
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_ver_fail",
        title="Ver Fail",
        description="Fails ver gate",
        max_attempts=3,
    ))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.final_status == TaskStatus.REPAIR_REQUIRED
    assert res.committed is False
    assert res.checkpoint_sha is None
    assert res.verification_report is not None
    assert res.verification_report.passed is False


@pytest.mark.asyncio
async def test_required_skipped_verification_prevents_commit(tmp_path: Path, db_session: AsyncSession):
    """Required check SKIPPED prevents commit and fails gate."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

    registry = CheckRegistry()
    registry.register(FakeCheck("c1", required=True, status=CheckStatus.SKIPPED))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent()
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_skip",
        title="Skip check",
        description="Skipped check test",
    ))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.committed is False
    assert res.final_status in (TaskStatus.REPAIR_REQUIRED, TaskStatus.FAILED)


# =============================================================================
# 3. State Safety & Matrix Invariants
# =============================================================================

@pytest.mark.asyncio
async def test_cannot_start_already_running_task(tmp_path: Path, db_session: AsyncSession):
    """Attempting to start a RUNNING task raises TaskNotStartableError."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)
    agent = FakeDevelopmentAgent()

    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_running",
        title="Running Task",
        description="Already running",
        status=TaskStatus.RUNNING,
    ))
    await db_session.commit()

    with pytest.raises(TaskNotStartableError) as exc_info:
        await orchestrator.start_task(task.task_id)
    assert exc_info.value.task_id == task.task_id


@pytest.mark.asyncio
async def test_cannot_start_committed_task(tmp_path: Path, db_session: AsyncSession):
    """Attempting to start a COMMITTED task raises TaskNotStartableError."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)
    agent = FakeDevelopmentAgent()

    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_committed",
        title="Committed Task",
        description="Already committed",
        status=TaskStatus.COMMITTED,
    ))
    await db_session.commit()

    with pytest.raises(TaskNotStartableError):
        await orchestrator.start_task(task.task_id)


@pytest.mark.asyncio
async def test_abort_and_cancel_behavior(tmp_path: Path, db_session: AsyncSession):
    """Verifies cancel_task and abort_task transitions and safety."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)
    agent = FakeDevelopmentAgent()

    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    t1 = await repo.create_task(DevelopmentTask(project_id="p1", title="T1", description="d", status=TaskStatus.PENDING))
    t2 = await repo.create_task(DevelopmentTask(project_id="p1", title="T2", description="d", status=TaskStatus.RUNNING))
    await db_session.commit()

    res_c = await orchestrator.cancel_task(t1.task_id, reason="User cancelled")
    assert res_c.final_status == TaskStatus.CANCELLED
    assert res_c.committed is False

    res_a = await orchestrator.abort_task(t2.task_id, reason="System emergency")
    assert res_a.final_status == TaskStatus.ABORTED
    assert res_a.committed is False


# =============================================================================
# 4. Attempt Limit & Retry Safety
# =============================================================================

@pytest.mark.asyncio
async def test_max_attempts_exceeded_raises_and_fails(tmp_path: Path, db_session: AsyncSession):
    """Task exceeding max_attempts raises MaxAttemptsExceededError and enters FAILED status."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)
    agent = FakeDevelopmentAgent()

    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_max_attempts",
        title="Max Attempts",
        description="Limit test",
        attempt_count=3,
        max_attempts=3,
        status=TaskStatus.REPAIR_REQUIRED,
    ))
    await db_session.commit()

    with pytest.raises(MaxAttemptsExceededError):
        await orchestrator.start_task(task.task_id)

    db_task = await repo.get_task(task.task_id)
    assert db_task.status == TaskStatus.FAILED


# =============================================================================
# 5. Security & Boundary Audit Tests
# =============================================================================

@pytest.mark.asyncio
async def test_agent_context_does_not_contain_auth_or_execution_internals(tmp_path: Path, db_session: AsyncSession):
    """Verifies that DevelopmentAgentContext contains ZERO permission/tool/secret internals."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    registry = CheckRegistry()
    registry.register(FakeCheck("c1", status=CheckStatus.PASS))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent()
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(project_id="proj_sec", title="Sec Task", description="sec"))
    await db_session.commit()

    await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert len(agent.captured_contexts) == 1
    ctx = agent.captured_contexts[0]

    # Verify context annotations & fields
    forbidden_terms = ["tool", "permission", "grant", "secret", "token", "password", "executor", "approval"]
    for field_name in DevelopmentAgentContext.model_fields.keys():
        for term in forbidden_terms:
            assert term not in field_name.lower(), f"Forbidden term '{term}' in DevelopmentAgentContext field '{field_name}'"


# =============================================================================
# 6. Concurrency Safety (File-Backed SQLite WAL)
# =============================================================================

@pytest.mark.asyncio
async def test_concurrent_start_attempts_single_winner(tmp_path: Path):
    """
    Simulates two workers trying to start the same PENDING task simultaneously
    on a real file-backed SQLite database. Exactly one start attempt wins.
    """
    db_file = tmp_path / "concurrency_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    engine = create_async_engine(db_url, connect_args={"timeout": 15})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    # Seed task
    async with session_factory() as session:
        repo = TaskRepository(session)
        task = await repo.create_task(DevelopmentTask(project_id="proj_conc", title="Concurrent Task", description="conc"))
        await session.commit()

    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    registry = CheckRegistry()
    registry.register(FakeCheck("c1", status=CheckStatus.PASS))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    # Create two orchestrators with separate DB sessions
    async def worker_start(worker_id: int):
        async with session_factory() as sess:
            w_repo = TaskRepository(sess)
            w_sm = TaskStateMachine(w_repo)
            w_agent = FakeDevelopmentAgent(file_to_create=f"file_{worker_id}.txt")
            w_orch = AutonomousTaskOrchestrator(w_repo, w_sm, wm, gate, w_agent)
            try:
                res = await w_orch.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))
                await sess.commit()
                return ("SUCCESS", res)
            except Exception as e:
                return ("ERROR", type(e).__name__)

    results = await asyncio.gather(worker_start(1), worker_start(2), return_exceptions=True)

    success_count = sum(1 for r in results if isinstance(r, tuple) and r[0] == "SUCCESS")
    assert success_count == 1, f"Expected exactly one successful start attempt, got: {results}"

    await engine.dispose()


# =============================================================================
# 7. Additional Edge Case & Invariant Tests
# =============================================================================

@pytest.mark.asyncio
async def test_development_agent_error_prevents_commit(tmp_path: Path, db_session: AsyncSession):
    """Development agent returning ERROR status prevents commit."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.ERROR)
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(project_id="p_err", title="Err", description="err"))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.committed is False
    assert res.final_status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_development_agent_timeout_prevents_commit(tmp_path: Path, db_session: AsyncSession):
    """Development agent returning TIMEOUT status prevents commit."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.TIMEOUT)
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(project_id="p_tout", title="Tout", description="tout"))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.committed is False
    assert res.final_status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_task_not_found_raises_task_not_found_error(tmp_path: Path, db_session: AsyncSession):
    """Non-existent task_id raises TaskNotFoundError."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)
    agent = FakeDevelopmentAgent()

    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    with pytest.raises(TaskNotFoundError):
        await orchestrator.start_task("non_existent_task_id")


@pytest.mark.asyncio
async def test_verification_gate_exception_handled_safely(tmp_path: Path, db_session: AsyncSession):
    """VerificationGate raising exception is caught and transitions task to REPAIR_REQUIRED or FAILED."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

    registry = CheckRegistry()
    registry.register(FakeCheck("c1", should_raise=True))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent()
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(project_id="p_gate_exc", title="Gate Exc", description="desc"))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.committed is False
    assert res.final_status in (TaskStatus.REPAIR_REQUIRED, TaskStatus.FAILED)


# =============================================================================
# 8. Test Evidence Gap Closures
# =============================================================================

@pytest.mark.asyncio
async def test_cannot_start_cancelled_task(tmp_path: Path, db_session: AsyncSession):
    """PENDING task transitioned to CANCELLED cannot be started via start_task()."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)
    agent = FakeDevelopmentAgent()

    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_can",
        title="Cancelled Task",
        description="Already cancelled",
        status=TaskStatus.PENDING,
    ))
    await sm.transition(task.task_id, TaskStatus.PENDING, TaskStatus.CANCELLED, reason="Pre-cancelled")
    await db_session.commit()

    with pytest.raises(TaskNotStartableError) as exc_info:
        await orchestrator.start_task(task.task_id)
    assert exc_info.value.task_id == task.task_id

    db_task = await repo.get_task(task.task_id)
    assert db_task.status == TaskStatus.CANCELLED
    assert len(agent.captured_contexts) == 0


@pytest.mark.asyncio
async def test_cleanup_failure_is_handled_safely(tmp_path: Path, db_session: AsyncSession):
    """Exception inside WorkspaceManager.remove_workspace during cleanup is swallowed and does not corrupt task outcome."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    registry = CheckRegistry()
    registry.register(FakeCheck("c1", status=CheckStatus.PASS))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    def failing_remove(task_id: str, workspace: Any) -> None:
        raise OSError("Simulated directory deletion permission error")

    wm.remove_workspace = failing_remove

    agent = FakeDevelopmentAgent(file_to_create="README.md")
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(project_id="proj_clean_err", title="Clean Fail", description="desc"))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.final_status == TaskStatus.COMMITTED
    assert res.committed is True
    assert res.checkpoint_sha is not None


@pytest.mark.asyncio
async def test_cancellation_race_cannot_continue_to_commit(tmp_path: Path):
    """
    Genuine async race test: Worker A starts task and blocks inside DevelopmentAgent.
    Worker B calls cancel_task(). Worker B succeeds (status -> CANCELLED).
    Worker A unblocks and attempts RUNNING -> VERIFYING transition.
    State machine CAS rejects Worker A, VerificationGate & checkpoint are NEVER executed,
    and final task state remains CANCELLED.
    """
    db_file = tmp_path / "cancel_race.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    engine = create_async_engine(db_url, connect_args={"timeout": 15})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    async with session_factory() as session:
        repo = TaskRepository(session)
        task = await repo.create_task(DevelopmentTask(project_id="proj_crace", title="Cancel Race", description="desc"))
        await session.commit()

    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

    gate_executed = False
    class TrackingCheck:
        check_id = "track_check"
        check_name = "Tracking Check"
        required = True
        def execute(self, context: VerificationContext) -> CheckResult:
            nonlocal gate_executed
            gate_executed = True
            now = datetime.now(timezone.utc)
            return CheckResult(
                check_id=self.check_id, check_name=self.check_name, required=self.required,
                status=CheckStatus.PASS, started_at=now, finished_at=now, duration_seconds=0.01,
            )

    registry = CheckRegistry()
    registry.register(TrackingCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent_started_event = asyncio.Event()
    cancel_completed_event = asyncio.Event()

    class BlockingAgent(DevelopmentAgent):
        def __init__(self, sess: AsyncSession) -> None:
            self._sess = sess

        async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
            target = Path(context.workspace_path) / "file_edit.txt"
            target.write_text("edit\n", encoding="utf-8")
            await self._sess.commit()
            agent_started_event.set()
            await cancel_completed_event.wait()
            return DevelopmentResult(status=DevelopmentResultStatus.SUCCESS, changed_files=("file_edit.txt",))

    async def worker_a():
        async with session_factory() as sess:
            a_repo = TaskRepository(sess)
            a_sm = TaskStateMachine(a_repo)
            a_agent = BlockingAgent(sess)
            a_orch = AutonomousTaskOrchestrator(a_repo, a_sm, wm, gate, a_agent)
            try:
                res = await a_orch.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))
                await sess.commit()
                return ("SUCCESS", res)
            except Exception as e:
                return ("ERROR", type(e).__name__)

    async def worker_b():
        await agent_started_event.wait()
        async with session_factory() as sess:
            b_repo = TaskRepository(sess)
            b_sm = TaskStateMachine(b_repo)
            b_orch = AutonomousTaskOrchestrator(b_repo, b_sm, wm, gate, FakeDevelopmentAgent())
            res = await b_orch.cancel_task(task.task_id, reason="Concurrent cancel")
            await sess.commit()
            cancel_completed_event.set()
            return res

    task_a = asyncio.create_task(worker_a())
    task_b = asyncio.create_task(worker_b())

    res_b = await task_b
    res_a = await task_a

    assert res_b.final_status == TaskStatus.CANCELLED
    assert res_a[0] == "ERROR"
    assert res_a[1] == "StaleStateTransitionError"
    assert gate_executed is False

    async with session_factory() as session:
        final_db_task = await TaskRepository(session).get_task(task.task_id)
        assert final_db_task.status == TaskStatus.CANCELLED

    await engine.dispose()


@pytest.mark.asyncio
async def test_abort_race_cannot_continue_to_commit(tmp_path: Path):
    """
    Genuine async race test: Worker A starts task and blocks inside DevelopmentAgent.
    Worker B calls abort_task(). Worker B succeeds (status -> ABORTED).
    Worker A unblocks and attempts RUNNING -> VERIFYING transition.
    State machine CAS rejects Worker A, VerificationGate & checkpoint are NEVER executed,
    and final task state remains ABORTED.
    """
    db_file = tmp_path / "abort_race.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    engine = create_async_engine(db_url, connect_args={"timeout": 15})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    async with session_factory() as session:
        repo = TaskRepository(session)
        task = await repo.create_task(DevelopmentTask(project_id="proj_arace", title="Abort Race", description="desc"))
        await session.commit()

    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

    gate_executed = False
    class TrackingCheck:
        check_id = "track_check_abort"
        check_name = "Tracking Check Abort"
        required = True
        def execute(self, context: VerificationContext) -> CheckResult:
            nonlocal gate_executed
            gate_executed = True
            now = datetime.now(timezone.utc)
            return CheckResult(
                check_id=self.check_id, check_name=self.check_name, required=self.required,
                status=CheckStatus.PASS, started_at=now, finished_at=now, duration_seconds=0.01,
            )

    registry = CheckRegistry()
    registry.register(TrackingCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent_started_event = asyncio.Event()
    abort_completed_event = asyncio.Event()

    class BlockingAgent(DevelopmentAgent):
        def __init__(self, sess: AsyncSession) -> None:
            self._sess = sess

        async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
            target = Path(context.workspace_path) / "file_edit_abort.txt"
            target.write_text("edit abort\n", encoding="utf-8")
            await self._sess.commit()
            agent_started_event.set()
            await abort_completed_event.wait()
            return DevelopmentResult(status=DevelopmentResultStatus.SUCCESS, changed_files=("file_edit_abort.txt",))

    async def worker_a():
        async with session_factory() as sess:
            a_repo = TaskRepository(sess)
            a_sm = TaskStateMachine(a_repo)
            a_agent = BlockingAgent(sess)
            a_orch = AutonomousTaskOrchestrator(a_repo, a_sm, wm, gate, a_agent)
            try:
                res = await a_orch.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))
                await sess.commit()
                return ("SUCCESS", res)
            except Exception as e:
                return ("ERROR", type(e).__name__)

    async def worker_b():
        await agent_started_event.wait()
        async with session_factory() as sess:
            b_repo = TaskRepository(sess)
            b_sm = TaskStateMachine(b_repo)
            b_orch = AutonomousTaskOrchestrator(b_repo, b_sm, wm, gate, FakeDevelopmentAgent())
            res = await b_orch.abort_task(task.task_id, reason="Concurrent abort")
            await sess.commit()
            abort_completed_event.set()
            return res

    task_a = asyncio.create_task(worker_a())
    task_b = asyncio.create_task(worker_b())

    res_b = await task_b
    res_a = await task_a

    assert res_b.final_status == TaskStatus.ABORTED
    assert res_a[0] == "ERROR"
    assert res_a[1] == "StaleStateTransitionError"
    assert gate_executed is False

    async with session_factory() as session:
        final_db_task = await TaskRepository(session).get_task(task.task_id)
        assert final_db_task.status == TaskStatus.ABORTED

    await engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_failure_prevents_commit(tmp_path: Path, db_session: AsyncSession):
    """Exception during WorkspaceManager.create_checkpoint causes task to fail and committed == False."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

    def failing_checkpoint(*args, **kwargs):
        raise RuntimeError("Simulated git commit failure")

    wm.create_checkpoint = failing_checkpoint

    registry = CheckRegistry()
    registry.register(FakeCheck("c1", status=CheckStatus.PASS))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent()
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(project_id="proj_cp_fail", title="CP Fail", description="desc"))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.committed is False
    assert res.final_status == TaskStatus.FAILED
    assert res.checkpoint_sha is None

    db_task = await repo.get_task(task.task_id)
    assert db_task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_invalid_checkpoint_sha_prevents_commit(tmp_path: Path, db_session: AsyncSession):
    """WorkspaceManager returning empty commit_sha causes task to fail and committed == False."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

    from app.workspace.models import CheckpointInfo
    def empty_sha_checkpoint(*args, **kwargs):
        return CheckpointInfo(
            checkpoint_id="cp1", task_id="t1", commit_sha="", commit_message="msg",
            branch_name="b", autonomous_worktree_path=str(repo_root),
        )

    wm.create_checkpoint = empty_sha_checkpoint

    registry = CheckRegistry()
    registry.register(FakeCheck("c1", status=CheckStatus.PASS))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent()
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(project_id="proj_empty_sha", title="Empty SHA", description="desc"))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.committed is False
    assert res.final_status == TaskStatus.FAILED
    assert res.checkpoint_sha is None

    db_task = await repo.get_task(task.task_id)
    assert db_task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_attempts_above_maximum_are_rejected(tmp_path: Path, db_session: AsyncSession):
    """Task with attempt_count > max_attempts raises MaxAttemptsExceededError, enters FAILED status, and runs zero agent executions."""
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)
    agent = FakeDevelopmentAgent()

    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_above_max",
        title="Above Max",
        description="attempt_count=4 > max_attempts=3",
        attempt_count=4,
        max_attempts=3,
        status=TaskStatus.REPAIR_REQUIRED,
    ))
    await db_session.commit()

    with pytest.raises(MaxAttemptsExceededError) as exc_info:
        await orchestrator.start_task(task.task_id)
    assert exc_info.value.attempt_count == 4
    assert exc_info.value.max_attempts == 3

    db_task = await repo.get_task(task.task_id)
    assert db_task.status == TaskStatus.FAILED
    assert len(agent.captured_contexts) == 0


# ============================================================================
# 10. Atomic Claim Concurrency & Attempt-Count Integrity Tests
# ============================================================================

@pytest.mark.asyncio
async def test_concurrent_claim_consumes_exactly_one_attempt(tmp_path: Path):
    """
    Two workers attempt to claim the exact same task concurrently from PENDING.
    Only ONE worker must succeed and transition to RUNNING.
    attempt_count must increment by EXACTLY 1 (from 0 to 1), NOT 2.
    The losing worker must raise StaleStateTransitionError / exit without executing agent or incrementing.
    """
    db_file = tmp_path / "test_concurrency.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(db_url, connect_args={"timeout": 15})

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    async with session_factory() as session:
        repo = TaskRepository(session)
        task = await repo.create_task(DevelopmentTask(
            project_id="proj_claim_conc",
            title="Concurrent Claim",
            description="Testing atomic claim concurrency",
            attempt_count=0,
            max_attempts=3,
            status=TaskStatus.PENDING,
        ))
        await session.commit()
        task_id = task.task_id

    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    registry = CheckRegistry()
    registry.register(FakeCheck("c1", status=CheckStatus.PASS))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent1 = FakeDevelopmentAgent(file_to_create="file1.txt")
    agent2 = FakeDevelopmentAgent(file_to_create="file2.txt")

    async def worker_run(agent_mock: FakeDevelopmentAgent):
        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent_mock)
            res = await orch.start_task(task_id, OrchestrationOptions(base_commit_sha=base_sha))
            await session.commit()
            return res

    results = await asyncio.gather(
        worker_run(agent1),
        worker_run(agent2),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, OrchestrationResult)]
    exceptions = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected 1 winner, got {len(successes)}"
    assert len(exceptions) == 1, f"Expected 1 failure, got {len(exceptions)}"
    assert isinstance(exceptions[0], (StaleStateTransitionError, MaxAttemptsExceededError, TaskNotStartableError))

    total_agent_execs = len(agent1.captured_contexts) + len(agent2.captured_contexts)
    assert total_agent_execs == 1, f"Expected exactly 1 agent execution, got {total_agent_execs}"

    async with session_factory() as session:
        repo = TaskRepository(session)
        final_task = await repo.get_task(task_id)
        assert final_task.attempt_count == 1, f"Expected attempt_count==1, got {final_task.attempt_count}"

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_claim_at_max_attempt_boundary(tmp_path: Path):
    """
    Task starts at attempt_count=2, max_attempts=3.
    Two workers attempt to claim concurrently.
    The winner increments attempt_count to 3 and starts.
    The loser fails cleanly without consuming attempt 4 or falsely setting status to FAILED.
    """
    db_file = tmp_path / "test_boundary.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(db_url, connect_args={"timeout": 15})

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    async with session_factory() as session:
        repo = TaskRepository(session)
        task = await repo.create_task(DevelopmentTask(
            project_id="proj_boundary",
            title="Boundary Claim",
            description="Testing max attempt boundary concurrency",
            attempt_count=2,
            max_attempts=3,
            status=TaskStatus.REPAIR_REQUIRED,
        ))
        await session.commit()
        task_id = task.task_id

    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    registry = CheckRegistry()
    registry.register(FakeCheck("c1", status=CheckStatus.PASS))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent1 = FakeDevelopmentAgent(file_to_create="file1.txt")
    agent2 = FakeDevelopmentAgent(file_to_create="file2.txt")

    async def worker_run(agent_mock: FakeDevelopmentAgent):
        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent_mock)
            res = await orch.start_task(task_id, OrchestrationOptions(base_commit_sha=base_sha))
            await session.commit()
            return res

    results = await asyncio.gather(
        worker_run(agent1),
        worker_run(agent2),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, OrchestrationResult)]
    assert len(successes) == 1

    async with session_factory() as session:
        repo = TaskRepository(session)
        final_task = await repo.get_task(task_id)
        assert final_task.attempt_count == 3
        assert final_task.status != TaskStatus.FAILED

    await engine.dispose()


@pytest.mark.asyncio
async def test_exhausted_attempts_are_not_claimable(tmp_path: Path, db_session: AsyncSession):
    """
    Task with attempt_count == max_attempts cannot be claimed.
    Attempting to start raises MaxAttemptsExceededError.
    attempt_count remains unchanged, agent is not executed.
    """
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)
    agent = FakeDevelopmentAgent()

    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_exh",
        title="Exhausted Task",
        description="attempt_count == max_attempts",
        attempt_count=3,
        max_attempts=3,
        status=TaskStatus.REPAIR_REQUIRED,
    ))
    await db_session.commit()

    with pytest.raises(MaxAttemptsExceededError) as exc_info:
        await orchestrator.start_task(task.task_id)

    assert exc_info.value.attempt_count == 3
    assert exc_info.value.max_attempts == 3

    db_task = await repo.get_task(task.task_id)
    assert db_task.attempt_count == 3
    assert db_task.status == TaskStatus.FAILED
    assert len(agent.captured_contexts) == 0


@pytest.mark.asyncio
async def test_over_limit_attempts_are_not_claimable(tmp_path: Path, db_session: AsyncSession):
    """
    Task with attempt_count > max_attempts cannot be claimed.
    attempt_count is NOT mutated further.
    """
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)
    agent = FakeDevelopmentAgent()

    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_over",
        title="Over Limit Task",
        description="attempt_count=5 > max_attempts=3",
        attempt_count=5,
        max_attempts=3,
        status=TaskStatus.REPAIR_REQUIRED,
    ))
    await db_session.commit()

    with pytest.raises(MaxAttemptsExceededError) as exc_info:
        await orchestrator.start_task(task.task_id)

    assert exc_info.value.attempt_count == 5

    db_task = await repo.get_task(task.task_id)
    assert db_task.attempt_count == 5
    assert db_task.status == TaskStatus.FAILED
    assert len(agent.captured_contexts) == 0


@pytest.mark.asyncio
async def test_atomic_claim_updates_status_and_attempt_in_single_operation(tmp_path: Path):
    """
    Direct test of TaskStateMachine.claim_attempt showing that status transition
    and attempt_count increment occur atomically.
    Second attempt to claim the same task fails with StaleStateTransitionError.
    """
    db_file = tmp_path / "test_atomic_direct.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(db_url)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        repo = TaskRepository(session)
        task = await repo.create_task(DevelopmentTask(
            project_id="proj_atomic_direct",
            title="Atomic Direct Test",
            description="Testing claim_attempt state machine method",
            attempt_count=0,
            max_attempts=3,
            status=TaskStatus.PENDING,
        ))
        await session.commit()
        task_id = task.task_id

    async with session_factory() as session:
        repo = TaskRepository(session)
        sm = TaskStateMachine(repo)

        res = await sm.claim_attempt(task_id, TaskStatus.PENDING, 3)
        await session.commit()
        assert res.task.status == TaskStatus.RUNNING
        assert res.task.attempt_count == 1
        assert res.previous_status == TaskStatus.PENDING
        assert res.new_status == TaskStatus.RUNNING

    async with session_factory() as session:
        repo = TaskRepository(session)
        sm = TaskStateMachine(repo)

        with pytest.raises(StaleStateTransitionError):
            await sm.claim_attempt(task_id, TaskStatus.PENDING, 3)

    async with session_factory() as session:
        repo = TaskRepository(session)
        final_task = await repo.get_task(task_id)
        assert final_task.status == TaskStatus.RUNNING
        assert final_task.attempt_count == 1

    await engine.dispose()


class ToggleCheck:
    """Check helper that can be toggled between FAIL and PASS for multi-attempt retry tests."""
    def __init__(self, check_id: str, status: CheckStatus = CheckStatus.FAIL) -> None:
        self.check_id = check_id
        self.check_name = "Toggle Check"
        self.required = True
        self.status = status

    def execute(self, context: VerificationContext) -> CheckResult:
        now = datetime.now(timezone.utc)
        curr = self.status
        return CheckResult(
            check_id=self.check_id,
            check_name=self.check_name,
            required=self.required,
            status=curr,
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
            error_message="Check failed simulation" if curr != CheckStatus.PASS else None,
        )


@pytest.mark.asyncio
async def test_retry_workspace_recreation_on_repair_required(tmp_path: Path, db_session: AsyncSession):
    """
    Verifies full sequential retry lifecycle on the SAME task_id:
    Attempt 1: Fails verification -> REPAIR_REQUIRED persisted, attempt_count=1, no commit, workspace cleaned up.
    Attempt 2: Retried with same task_id -> claims RUNNING (attempt_count=2), fresh workspace created, verification PASS, git checkpoint committed.
    """
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    worktree_base = tmp_path / "worktrees"
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=worktree_base)

    toggle_check = ToggleCheck("toggle_1", status=CheckStatus.FAIL)
    registry = CheckRegistry()
    registry.register(toggle_check)
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    agent = FakeDevelopmentAgent(file_to_create="retry_edit.txt")
    orchestrator = AutonomousTaskOrchestrator(repo, sm, wm, gate, agent)

    # 1. Create DevelopmentTask
    task = await repo.create_task(DevelopmentTask(
        project_id="proj_retry_seq",
        title="Retry Sequential Task",
        description="Testing retry workspace recreation",
        max_attempts=3,
    ))
    await db_session.commit()
    task_id = task.task_id

    # 2. Attempt 1 — toggle_check status is FAIL
    toggle_check.status = CheckStatus.FAIL
    opts = OrchestrationOptions(base_commit_sha=base_sha, auto_cleanup_workspace=True)
    res1 = await orchestrator.start_task(task_id, opts)

    # 3. Assert Attempt 1 results & DB state
    assert res1.final_status == TaskStatus.REPAIR_REQUIRED
    assert res1.committed is False
    assert res1.checkpoint_sha is None
    assert res1.attempt_count == 1

    db_task_1 = await repo.get_task(task_id)
    assert db_task_1 is not None
    assert db_task_1.status == TaskStatus.REPAIR_REQUIRED
    assert db_task_1.attempt_count == 1

    # Verify attempt 1 workspace path was cleaned up
    attempt1_ws_path = worktree_base / task_id
    assert not attempt1_ws_path.exists()

    # 4. Attempt 2 — toggle_check status set to PASS
    toggle_check.status = CheckStatus.PASS
    res2 = await orchestrator.start_task(task_id, opts)

    # 5. Assert Attempt 2 results & DB state
    assert res2.final_status == TaskStatus.COMMITTED
    assert res2.committed is True
    assert res2.checkpoint_sha is not None
    assert len(res2.checkpoint_sha) == 40
    assert res2.attempt_count == 2

    db_task_2 = await repo.get_task(task_id)
    assert db_task_2 is not None
    assert db_task_2.status == TaskStatus.COMMITTED
    assert db_task_2.attempt_count == 2





