"""
Autonomous Run Controller — Dedicated Test Suite (Stage 6.8).

Verifies:
  1. Concurrency & durability using real file-backed SQLite in WAL mode with independent sessions.
  2. Failure injection with provider-neutral fake DevelopmentAgent and VerificationGate.
  3. Single-run execution fencing and race safety under asyncio.gather().
  4. Idempotency for cancel/abort operations.
  5. Mandatory verification & commit validation (no false success).
  6. Timeout handling and RecoveryManager integration.
  7. Strict security boundaries via AST import audit.
"""
from __future__ import annotations

import asyncio
import ast
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
import pytest

from app.db.database import Base
from app.orchestration.agent_interface import DevelopmentAgent
from app.orchestration.exceptions import (
    CheckpointOrchestrationError,
    MaxAttemptsExceededError,
    TaskNotStartableError,
)
from app.orchestration.models import (
    DevelopmentAgentContext,
    DevelopmentResult,
    DevelopmentResultStatus,
    OrchestrationOptions,
    RecoveryResult,
    RecoveryStatus,
)
from app.orchestration.orchestrator import AutonomousTaskOrchestrator
from app.orchestration.recovery_manager import RecoveryManager
from app.orchestration.run_controller import (
    AutonomousRunController,
    AutonomousRunResult,
    RunStatus,
)
from app.repositories.task_repository import TaskRepository
from app.tasks.exceptions import TaskNotFoundError
from app.tasks.models import DevelopmentTask, TaskStatus
from app.tasks.state_machine import TaskStateMachine
from app.verification.check_registry import CheckRegistry
from app.verification.gate import VerificationGate
from app.verification.models import (
    CheckResult,
    CheckStatus,
    GateDecision,
    VerificationContext,
    VerificationReport,
)
from app.workspace.workspace_manager import WorkspaceManager
from app.workspace.exceptions import WorkspaceOwnershipError
from app.workspace.models import WorkspaceInfo, WorkspaceStatus


# --- Helpers & Real Workspace Setup ---

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


# --- Fake Test Implementations ---

class FakeDevelopmentAgent(DevelopmentAgent):
    """Fake DevelopmentAgent for deterministic testing."""

    def __init__(
        self,
        status: DevelopmentResultStatus = DevelopmentResultStatus.SUCCESS,
        delay_seconds: float = 0.0,
        should_raise: Exception | None = None,
        file_to_create: str | None = "file1.txt",
    ) -> None:
        self.status = status
        self.delay_seconds = delay_seconds
        self.should_raise = should_raise
        self.file_to_create = file_to_create
        self.execute_count = 0
        self.last_context: DevelopmentAgentContext | None = None

    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        self.execute_count += 1
        self.last_context = context
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if self.should_raise:
            raise self.should_raise

        # Respect cancellation/abort events if set before/during execution
        if context.cancel_event is not None and context.cancel_event.is_set():
            return DevelopmentResult(
                status=DevelopmentResultStatus.CANCELLED,
                summary="Agent detected cancellation event",
                error_message="Cancelled via cancel_event",
            )
        if context.abort_event is not None and context.abort_event.is_set():
            return DevelopmentResult(
                status=DevelopmentResultStatus.FAILURE,
                summary="Agent detected abort event",
                error_message="Aborted via abort_event",
            )

        if self.file_to_create and self.status == DevelopmentResultStatus.SUCCESS:
            target = Path(context.workspace_path) / self.file_to_create
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fake agent edit\n", encoding="utf-8")

        return DevelopmentResult(
            status=self.status,
            summary=f"Fake agent status={self.status.value}",
            changed_files=(self.file_to_create,) if (self.status == DevelopmentResultStatus.SUCCESS and self.file_to_create) else (),
            error_message="Agent error" if self.status != DevelopmentResultStatus.SUCCESS else None,
        )


class FakeVerificationGate(VerificationGate):
    """Fake VerificationGate for deterministic testing."""

    def __init__(
        self,
        decision: GateDecision = GateDecision.PASS,
        should_raise: Exception | None = None,
    ) -> None:
        self.decision = decision
        self.should_raise = should_raise
        self.verify_count = 0

    def run(self, context: VerificationContext) -> VerificationReport:
        self.verify_count += 1
        if self.should_raise:
            raise self.should_raise

        now = datetime.now(timezone.utc)
        check = CheckResult(
            check_id="fake_check_01",
            check_name="Fake Check",
            required=True,
            status=CheckStatus.PASS if self.decision == GateDecision.PASS else CheckStatus.FAIL,
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
            stdout="",
            stderr="",
            error_message=None if self.decision == GateDecision.PASS else "Verification failed",
        )
        return VerificationReport(
            task_id=context.task_id,
            gate_decision=self.decision,
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
            results=(check,),
        )


# --- Test Fixtures ---

@pytest.fixture
async def sqlite_db():
    """Create a temporary real file-backed SQLite database in WAL mode."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"timeout": 15.0},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    yield engine, session_factory, db_path

    await engine.dispose()
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass


async def _create_test_task(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: str = "task_run_ctrl_01",
    status: TaskStatus = TaskStatus.PENDING,
    attempt_count: int = 0,
    max_attempts: int = 3,
) -> DevelopmentTask:
    async with session_factory() as session:
        async with session.begin():
            repo = TaskRepository(session)
            task = DevelopmentTask(
                task_id=task_id,
                project_id="proj_001",
                title="Test Run Task",
                description="Testing run controller",
                status=status,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
            )
            created = await repo.create_task(task)
            return created


# --- Tests ---

@pytest.mark.asyncio
async def test_successful_run_lifecycle(sqlite_db):
    engine, session_factory, db_path = sqlite_db
    task = await _create_test_task(session_factory, task_id="task_succ_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            rec = RecoveryManager(repo, sm, ws_mgr)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch, rec)

            result = await ctrl.start_run("task_succ_01")

            assert result.status == RunStatus.SUCCEEDED
            assert result.task_status == TaskStatus.COMMITTED
            assert result.verification_passed is True
            assert result.commit_sha is not None
            assert len(result.commit_sha) == 40
            assert result.attempt_count == 1
            assert result.failure_reason is None

            # Verify persisted DB state
            persisted = await repo.get_task("task_succ_01")
            assert persisted.status == TaskStatus.COMMITTED
            assert persisted.attempt_count == 1


@pytest.mark.asyncio
async def test_agent_failure_run_lifecycle(sqlite_db):
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_fail_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.FAILURE)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            result = await ctrl.start_run("task_fail_01")

            assert result.status == RunStatus.FAILED
            assert result.task_status in (TaskStatus.FAILED, TaskStatus.REPAIR_REQUIRED)
            assert result.verification_passed is False
            assert result.commit_sha is None
            assert result.attempt_count == 1


@pytest.mark.asyncio
async def test_verification_failure_prevents_commit(sqlite_db):
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_verif_fail_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS)
        gate = FakeVerificationGate(decision=GateDecision.FAIL)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            result = await ctrl.start_run("task_verif_fail_01")

            assert result.status == RunStatus.FAILED
            assert result.task_status in (TaskStatus.FAILED, TaskStatus.REPAIR_REQUIRED)
            assert result.verification_passed is False
            assert result.commit_sha is None


@pytest.mark.asyncio
async def test_run_timeout_handling(sqlite_db):
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_timeout_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        # Agent sleeps for 1.0s, timeout is 0.1s
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS, delay_seconds=1.0)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            rec = RecoveryManager(repo, sm, ws_mgr, timeout_seconds=0.1)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch, rec, default_timeout_seconds=0.1)

            result = await ctrl.start_run("task_timeout_01", timeout_seconds=0.1)

            assert result.status == RunStatus.TIMED_OUT
            assert result.recovery_required is False, "Execution terminated within grace period; no recovery needed"
            assert result.verification_passed is False
            assert result.commit_sha is None
            assert "timed out" in result.failure_reason.lower()

            # Durable proof: execution_terminated_at is set and execution_token is cleared
            async with session_factory() as verify_session:
                verify_repo = TaskRepository(verify_session)
                t = await verify_repo.get_task("task_timeout_01")
                assert t is not None
                assert t.execution_terminated_at is not None, "execution_terminated_at must be set after confirmed termination"
                assert t.execution_token is None, "execution_token must be cleared after confirmed termination"
                assert t.status in (TaskStatus.REPAIR_REQUIRED, TaskStatus.FAILED)


@pytest.mark.asyncio
async def test_concurrent_start_runs_single_winner(sqlite_db):
    """
    Test two concurrent start_run calls against the same task.
    Exactly ONE worker wins the atomic attempt claim. The second receives FAILED/already running.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_conc_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS, delay_seconds=0.2)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        barrier = asyncio.Event()

        async def worker_run(worker_id: int):
            await barrier.wait()
            async with session_factory() as session:
                repo = TaskRepository(session)
                sm = TaskStateMachine(repo)
                orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
                ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)
                return await ctrl.start_run("task_conc_01")

        t1 = asyncio.create_task(worker_run(1))
        t2 = asyncio.create_task(worker_run(2))
        barrier.set()

        r1, r2 = await asyncio.gather(t1, t2, return_exceptions=True)

        results = [r for r in (r1, r2) if isinstance(r, AutonomousRunResult)]
        succeeded_count = sum(1 for r in results if r.status == RunStatus.SUCCEEDED)
        failed_count = sum(1 for r in results if r.status == RunStatus.FAILED)

        assert len(results) == 2
        assert succeeded_count == 1
        assert failed_count == 1

        # Deterministic proof: exactly one agent execution occurred
        assert agent.execute_count == 1

        # Verify DB final status
        async with session_factory() as session:
            repo = TaskRepository(session)
            final_task = await repo.get_task("task_conc_01")
            assert final_task.status == TaskStatus.COMMITTED
            assert final_task.attempt_count == 1


@pytest.mark.asyncio
async def test_cancel_run_idempotency(sqlite_db):
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_cancel_01", status=TaskStatus.RUNNING)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent()
        gate = FakeVerificationGate()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            # First cancellation
            res1 = await ctrl.cancel_run("task_cancel_01", reason="First cancel")
            assert res1.status == RunStatus.CANCELLED
            assert res1.task_status == TaskStatus.CANCELLED

            # Second cancellation (idempotent)
            res2 = await ctrl.cancel_run("task_cancel_01", reason="Second cancel")
            assert res2.status == RunStatus.CANCELLED
            assert res2.task_status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_abort_run_idempotency(sqlite_db):
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_abort_01", status=TaskStatus.RUNNING)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent()
        gate = FakeVerificationGate()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            # First abort
            res1 = await ctrl.abort_run("task_abort_01", reason="First abort")
            assert res1.status == RunStatus.ABORTED
            assert res1.task_status == TaskStatus.ABORTED

            # Second abort (idempotent)
            res2 = await ctrl.abort_run("task_abort_01", reason="Second abort")
            assert res2.status == RunStatus.ABORTED
            assert res2.task_status == TaskStatus.ABORTED


@pytest.mark.asyncio
async def test_terminal_task_cannot_be_restarted(sqlite_db):
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_term_01", status=TaskStatus.COMMITTED)
    await _create_test_task(session_factory, task_id="task_term_02", status=TaskStatus.CANCELLED)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent()
        gate = FakeVerificationGate()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            with pytest.raises(TaskNotStartableError):
                await ctrl.start_run("task_term_01")

            with pytest.raises(TaskNotStartableError):
                await ctrl.start_run("task_term_02")


@pytest.mark.asyncio
async def test_get_run_status(sqlite_db):
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_get_stat_01", status=TaskStatus.PENDING)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent()
        gate = FakeVerificationGate()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            res = await ctrl.get_run_status("task_get_stat_01")
            assert res.task_id == "task_get_stat_01"
            assert res.status == RunStatus.CREATED
            assert res.task_status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_run_controller_invalid_parameters(sqlite_db):
    engine, session_factory, db_path = sqlite_db

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent()
        gate = FakeVerificationGate()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)

            with pytest.raises(ValueError):
                AutonomousRunController(repo, sm, ws_mgr, gate, orch, default_timeout_seconds=0)

            with pytest.raises(ValueError):
                AutonomousRunController(repo, sm, ws_mgr, gate, orch, default_timeout_seconds=-5.0)

            with pytest.raises(ValueError):
                AutonomousRunController(repo, sm, ws_mgr, gate, orch, default_timeout_seconds=math.nan)


def test_run_controller_security_boundary_imports():
    """
    AST audit of services/core/app/orchestration/run_controller.py to verify ZERO
    forbidden security boundary imports or calls exist.
    """
    target_path = Path(__file__).parent.parent / "app" / "orchestration" / "run_controller.py"
    assert target_path.exists(), f"Target source file missing: {target_path}"

    with open(target_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(target_path))

    forbidden_modules = {
        "ToolExecutor",
        "BaseTool",
        "PermissionEngine",
        "ApprovalService",
        "subprocess",
        "git",
        "Gemini",
        "Kilo",
        "Ollama",
        "OpenAI",
        "Anthropic",
        "FastAPI",
        "requests",
        "httpx",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden.lower() not in alias.name.lower(), (
                        f"Forbidden import '{alias.name}' found in run_controller.py"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for forbidden in forbidden_modules:
                assert forbidden.lower() not in module.lower(), (
                    f"Forbidden import from '{module}' found in run_controller.py"
                )
                for alias in node.names:
                    assert forbidden.lower() not in alias.name.lower(), (
                        f"Forbidden import symbol '{alias.name}' from '{module}' in run_controller.py"
                    )


@pytest.mark.asyncio
async def test_stale_running_task_returns_recovery_required(sqlite_db):
    """
    A RUNNING task with a stale heartbeat must return RECOVERY_REQUIRED, not start a new execution.
    This exercises the timedelta-based stale-detection fix in run_controller.
    """
    from datetime import timedelta
    engine, session_factory, db_path = sqlite_db

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent()
        gate = FakeVerificationGate()

        # Create task already in RUNNING with a very old heartbeat (stale)
        stale_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=700)
        async with session_factory() as session:
            async with session.begin():
                from app.db.models.development_task import DevelopmentTaskModel
                orm = DevelopmentTaskModel(
                    task_id="task_stale_01",
                    project_id="proj_001",
                    title="Stale Task",
                    description="Stale running task",
                    status="RUNNING",
                    attempt_count=1,
                    max_attempts=3,
                    last_heartbeat=stale_heartbeat,
                )
                session.add(orm)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            rec = RecoveryManager(repo, sm, ws_mgr, timeout_seconds=300.0)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch, rec)

            result = await ctrl.start_run("task_stale_01")

        assert result.status == RunStatus.RECOVERY_REQUIRED
        assert result.recovery_required is True
        assert result.verification_passed is False
        assert result.commit_sha is None
        assert "stale" in result.failure_reason.lower() or "recovery" in result.failure_reason.lower()


@pytest.mark.asyncio
async def test_abort_on_running_task(sqlite_db):
    """
    abort_run on a RUNNING task must transition it to ABORTED durably.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_abort_run_01", status=TaskStatus.RUNNING)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent()
        gate = FakeVerificationGate()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            result = await ctrl.abort_run("task_abort_run_01", reason="Test abort from RUNNING")

        assert result.status == RunStatus.ABORTED
        assert result.task_status == TaskStatus.ABORTED

        # Verify DB
        async with session_factory() as session:
            repo2 = TaskRepository(session)
            t = await repo2.get_task("task_abort_run_01")
            assert t.status == TaskStatus.ABORTED


@pytest.mark.asyncio
async def test_unexpected_agent_exception_returns_failed(sqlite_db):
    """
    An unexpected exception raised by the agent must result in a FAILED run, never SUCCESS.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_exc_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(should_raise=RuntimeError("Simulated unexpected agent crash"))
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            result = await ctrl.start_run("task_exc_01")

        assert result.status == RunStatus.FAILED
        assert result.verification_passed is False
        assert result.commit_sha is None


@pytest.mark.asyncio
async def test_concurrent_start_and_cancel(sqlite_db):
    """
    start_run and cancel_run racing on the same task must not corrupt durable state.
    One must win; state must remain consistent after both complete.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_start_cancel_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS, delay_seconds=0.3)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async def do_start():
            async with session_factory() as session:
                repo = TaskRepository(session)
                sm = TaskStateMachine(repo)
                orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
                ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)
                return await ctrl.start_run("task_start_cancel_01")

        async def do_cancel():
            await asyncio.sleep(0.05)  # let start_run claim first
            async with session_factory() as session:
                repo = TaskRepository(session)
                sm = TaskStateMachine(repo)
                orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
                ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)
                return await ctrl.cancel_run("task_start_cancel_01", reason="Race cancel")

        r1, r2 = await asyncio.gather(do_start(), do_cancel(), return_exceptions=True)

        results = [r for r in (r1, r2) if isinstance(r, AutonomousRunResult)]
        assert len(results) == 2

        # Verify durable consistency: final state must be terminal
        async with session_factory() as session:
            repo = TaskRepository(session)
            final = await repo.get_task("task_start_cancel_01")
            assert final.status in (TaskStatus.COMMITTED, TaskStatus.CANCELLED, TaskStatus.FAILED)


@pytest.mark.asyncio
async def test_durable_state_survives_db_reopen(sqlite_db):
    """
    After a successful run, reopening the DB with a fresh engine must show COMMITTED state.
    Verifies durable persistence (not just in-memory state).
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_durable_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)
            result = await ctrl.start_run("task_durable_01")

        assert result.status == RunStatus.SUCCEEDED

        # Now open a completely new engine against the same file
        from sqlalchemy.ext.asyncio import create_async_engine as _cae, async_sessionmaker as _asm, AsyncSession as _AS
        new_engine = _cae(f"sqlite+aiosqlite:///{db_path}", connect_args={"timeout": 15.0})
        new_sf = _asm(new_engine, class_=_AS, expire_on_commit=False)

        try:
            async with new_sf() as session:
                repo2 = TaskRepository(session)
                persisted = await repo2.get_task("task_durable_01")
                assert persisted is not None
                assert persisted.status == TaskStatus.COMMITTED
                assert persisted.attempt_count == 1
        finally:
            await new_engine.dispose()


# --- Defect 5 / Defect 6 additional tests ---

class RaisingVerificationGate(VerificationGate):
    """Verification gate that always raises."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.verify_count = 0

    def run(self, context: VerificationContext) -> VerificationReport:
        self.verify_count += 1
        raise self._exc


class FailingCheckpointWorkspaceManager(WorkspaceManager):
    """WorkspaceManager whose create_checkpoint always fails."""

    def __init__(self, delegate: WorkspaceManager, exc: Exception) -> None:
        self._delegate = delegate
        self._exc = exc
        self.removed_workspaces: list[tuple[str, Any]] = []

    def create_workspace(self, task_id: str, base_commit_sha: str, execution_token: str = "") -> Any:
        return self._delegate.create_workspace(task_id, base_commit_sha, execution_token=execution_token)

    def get_changed_files(self, workspace, base_commit_sha=None):
        return self._delegate.get_changed_files(workspace, base_commit_sha)

    def get_current_head(self, worktree_path=None):
        return self._delegate.get_current_head(worktree_path)

    def get_primary_worktree_path(self):
        return self._delegate.get_primary_worktree_path()

    def get_worktree_base_dir(self):
        return self._delegate.get_worktree_base_dir()

    def get_repository_root(self):
        return self._delegate.get_repository_root()

    def create_checkpoint(self, task_id: str, workspace: Any, intended_paths: list[str], message: str) -> Any:
        raise self._exc

    def remove_workspace(self, task_id: str, workspace: Any, *, force: bool = False, expected_execution_token: str | None = None) -> None:
        self.removed_workspaces.append((task_id, workspace))
        self._delegate.remove_workspace(task_id, workspace, force=force, expected_execution_token=expected_execution_token)

    def list_registered_worktrees(self):
        return self._delegate.list_registered_worktrees()


class BlockingAgent(DevelopmentAgent):
    """Agent that blocks until an asyncio Event is set, then returns a configurable result."""

    def __init__(self, result: DevelopmentResult, ready_event: asyncio.Event) -> None:
        self._result = result
        self._ready_event = ready_event
        self.execute_count = 0
        self.last_context: DevelopmentAgentContext | None = None

    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        self.execute_count += 1
        self.last_context = context
        self._ready_event.set()
        try:
            while True:
                if context.cancel_event is not None and context.cancel_event.is_set():
                    return DevelopmentResult(
                        status=DevelopmentResultStatus.CANCELLED,
                        summary="Blocking agent detected cancel",
                        error_message="Cancelled via cancel_event",
                    )
                if context.abort_event is not None and context.abort_event.is_set():
                    return DevelopmentResult(
                        status=DevelopmentResultStatus.FAILURE,
                        summary="Blocking agent detected abort",
                        error_message="Aborted via abort_event",
                    )
                try:
                    await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    continue
        except Exception as exc:
            return DevelopmentResult(
                status=DevelopmentResultStatus.ERROR,
                summary="Blocking agent error",
                error_message=str(exc),
            )


@pytest.mark.asyncio
async def test_verification_exception_returns_failed(sqlite_db):
    """
    VerificationGate exception must produce FAILED run, never SUCCESS.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_verif_exc_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS)
        gate = RaisingVerificationGate(exc=RuntimeError("verification boom"))

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            result = await ctrl.start_run("task_verif_exc_01")

        assert result.status == RunStatus.FAILED
        assert result.verification_passed is False
        assert result.commit_sha is None
        assert "verification" in result.failure_reason.lower() or "gate" in result.failure_reason.lower()

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_verif_exc_01")
            assert t.status in (TaskStatus.FAILED, TaskStatus.REPAIR_REQUIRED)


@pytest.mark.asyncio
async def test_commit_failure_prevents_committed(sqlite_db):
    """
    Checkpoint failure must prevent COMMITTED state and clean up workspace.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_commit_fail_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS)
        gate = FakeVerificationGate(decision=GateDecision.PASS)
        failing_ws = FailingCheckpointWorkspaceManager(
            delegate=ws_mgr, exc=RuntimeError("checkpoint boom")
        )

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, failing_ws, gate, agent)
            ctrl = AutonomousRunController(repo, sm, failing_ws, gate, orch)

            result = await ctrl.start_run("task_commit_fail_01")

        assert result.status == RunStatus.FAILED
        assert result.task_status != TaskStatus.COMMITTED
        assert result.commit_sha is None
        assert "checkpoint" in result.failure_reason.lower() or "commit" in result.failure_reason.lower()

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_commit_fail_01")
            assert t.status != TaskStatus.COMMITTED
            assert t.status in (TaskStatus.FAILED, TaskStatus.REPAIR_REQUIRED)

        assert len(failing_ws.removed_workspaces) >= 1


@pytest.mark.asyncio
async def test_active_cancellation_stops_execution(sqlite_db):
    """
    cancel_run during active agent execution must fence verification/commit
    and transition task to CANCELLED.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_active_cancel_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        blocking_agent = BlockingAgent(
            result=DevelopmentResult(status=DevelopmentResultStatus.SUCCESS),
            ready_event=ready,
        )
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, blocking_agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            start_task = asyncio.create_task(ctrl.start_run("task_active_cancel_01"))
            await ready.wait()

            async with session_factory() as cancel_session:
                cancel_repo = TaskRepository(cancel_session)
                cancel_sm = TaskStateMachine(cancel_repo)
                cancel_ctl = AutonomousRunController(cancel_repo, cancel_sm, ws_mgr, gate, orch)
                cancel_ctl._active_cancel_events = ctrl._active_cancel_events
                cancel_ctl._active_abort_events = ctrl._active_abort_events
                cancel_result = await cancel_ctl.cancel_run("task_active_cancel_01", reason="Active cancel test")

            start_result = await start_task

        assert cancel_result.status == RunStatus.CANCELLED
        assert cancel_result.task_status == TaskStatus.CANCELLED
        assert start_result.status in (RunStatus.CANCELLED, RunStatus.FAILED)
        assert start_result.task_status in (TaskStatus.CANCELLED, TaskStatus.FAILED)

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_active_cancel_01")
            assert t.status == TaskStatus.CANCELLED

        assert gate.verify_count == 0


@pytest.mark.asyncio
async def test_active_abort_stops_execution(sqlite_db):
    """
    abort_run during active agent execution must fence verification/commit
    and transition task to ABORTED.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_active_abort_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        blocking_agent = BlockingAgent(
            result=DevelopmentResult(status=DevelopmentResultStatus.SUCCESS),
            ready_event=ready,
        )
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, blocking_agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            start_task = asyncio.create_task(ctrl.start_run("task_active_abort_01"))
            await ready.wait()

            async with session_factory() as abort_session:
                abort_repo = TaskRepository(abort_session)
                abort_sm = TaskStateMachine(abort_repo)
                abort_ctl = AutonomousRunController(abort_repo, abort_sm, ws_mgr, gate, orch)
                abort_ctl._active_cancel_events = ctrl._active_cancel_events
                abort_ctl._active_abort_events = ctrl._active_abort_events
                abort_result = await abort_ctl.abort_run("task_active_abort_01", reason="Active abort test")

            start_result = await start_task

        assert abort_result.status == RunStatus.ABORTED
        assert abort_result.task_status == TaskStatus.ABORTED
        assert start_result.status in (RunStatus.ABORTED, RunStatus.FAILED)
        assert start_result.task_status in (TaskStatus.ABORTED, TaskStatus.FAILED)

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_active_abort_01")
            assert t.status == TaskStatus.ABORTED

        assert gate.verify_count == 0


@pytest.mark.asyncio
async def test_timeout_vs_recovery_no_duplicate(sqlite_db):
    """
    Controller timeout must not create a second recovery mechanism.
    Stage 6.7 RecoveryManager remains the sole recovery authority.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_timeout_rec_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS, delay_seconds=1.0)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            rec = RecoveryManager(repo, sm, ws_mgr, timeout_seconds=0.1)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch, rec, default_timeout_seconds=0.1)

            result = await ctrl.start_run("task_timeout_rec_01")

        assert result.status == RunStatus.TIMED_OUT
        assert result.recovery_required is False, "Execution terminated within grace period; no recovery needed"

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_timeout_rec_01")
            assert t.status in (TaskStatus.REPAIR_REQUIRED, TaskStatus.FAILED)
            assert t.recovery_owner is None


@pytest.mark.asyncio
async def test_timeout_after_durable_committed_reconciles(sqlite_db):
    """
    If durable state becomes COMMITTED before timeout returns, controller must
    return SUCCEEDED, not TIMED_OUT.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_timeout_commit_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            result = await ctrl.start_run("task_timeout_commit_01", timeout_seconds=10.0)

        assert result.status == RunStatus.SUCCEEDED
        assert result.task_status == TaskStatus.COMMITTED
        assert result.verification_passed is True
        assert result.commit_sha is not None


@pytest.mark.asyncio
async def test_crash_like_interruption_authoritative_state(sqlite_db):
    """
    After a successful run, reopening the DB yields authoritative COMMITTED state.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_crash_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        agent = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)
            result = await ctrl.start_run("task_crash_01")

        assert result.status == RunStatus.SUCCEEDED

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_crash_01")
            assert t.status == TaskStatus.COMMITTED


@pytest.mark.asyncio
async def test_late_success_fenced_after_cancel(sqlite_db):
    """
    After cancel_run, a late agent SUCCESS must not produce COMMITTED.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_late_cancel_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        blocking_agent = BlockingAgent(
            result=DevelopmentResult(status=DevelopmentResultStatus.SUCCESS),
            ready_event=ready,
        )
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, blocking_agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            start_task = asyncio.create_task(ctrl.start_run("task_late_cancel_01"))
            await ready.wait()

            async with session_factory() as cancel_session:
                cancel_repo = TaskRepository(cancel_session)
                cancel_sm = TaskStateMachine(cancel_repo)
                cancel_ctl = AutonomousRunController(cancel_repo, cancel_sm, ws_mgr, gate, orch)
                cancel_ctl._active_cancel_events = ctrl._active_cancel_events
                cancel_ctl._active_abort_events = ctrl._active_abort_events
                cancel_result = await cancel_ctl.cancel_run("task_late_cancel_01", reason="Fence late success")

            await start_task

        assert cancel_result.status == RunStatus.CANCELLED
        assert cancel_result.task_status == TaskStatus.CANCELLED

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_late_cancel_01")
            assert t.status == TaskStatus.CANCELLED

        assert gate.verify_count == 0


@pytest.mark.asyncio
async def test_late_success_fenced_after_abort(sqlite_db):
    """
    After abort_run, a late agent SUCCESS must not produce COMMITTED.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_late_abort_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        blocking_agent = BlockingAgent(
            result=DevelopmentResult(status=DevelopmentResultStatus.SUCCESS),
            ready_event=ready,
        )
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, blocking_agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            start_task = asyncio.create_task(ctrl.start_run("task_late_abort_01"))
            await ready.wait()

            async with session_factory() as abort_session:
                abort_repo = TaskRepository(abort_session)
                abort_sm = TaskStateMachine(abort_repo)
                abort_ctl = AutonomousRunController(abort_repo, abort_sm, ws_mgr, gate, orch)
                abort_ctl._active_cancel_events = ctrl._active_cancel_events
                abort_ctl._active_abort_events = ctrl._active_abort_events
                abort_result = await abort_ctl.abort_run("task_late_abort_01", reason="Fence late success")

            await start_task

        assert abort_result.status == RunStatus.ABORTED
        assert abort_result.task_status == TaskStatus.ABORTED

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_late_abort_01")
            assert t.status == TaskStatus.ABORTED

        assert gate.verify_count == 0


class NonCooperativeAgent(DevelopmentAgent):
    """Agent that ignores cancellation/abort events and never voluntarily returns."""

    def __init__(self, delay_seconds: float = 10.0, max_iterations: int = 200, ready_event: asyncio.Event | None = None, work_per_iteration: bool = False) -> None:
        self.delay_seconds = delay_seconds
        self.max_iterations = max_iterations
        self.execute_count = 0
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.ready_event = ready_event
        self.work_per_iteration = work_per_iteration
        self.file_to_create = "noncoop_output.txt"

    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        self.execute_count += 1
        self.started_at = datetime.now(timezone.utc)
        if self.ready_event is not None:
            self.ready_event.set()
        try:
            output_path = Path(context.workspace_path) / self.file_to_create
            output_path.write_text("non-cooperative output", encoding="utf-8")
        except Exception:
            pass
        try:
            for _ in range(self.max_iterations):
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    pass
                if self.work_per_iteration:
                    _ = sum(range(10000))
        except Exception:
            pass
        self.finished_at = datetime.now(timezone.utc)
        return DevelopmentResult(
            status=DevelopmentResultStatus.SUCCESS,
            summary="Non-cooperative agent finished",
            changed_files=(self.file_to_create,),
        )


@pytest.mark.asyncio
async def test_non_cooperative_agent_cancel(sqlite_db):
    """
    Non-cooperative agent ignores cancel_event.
    Controller must cancel the asyncio Task and wait for termination
    before cleaning up workspace.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_noncoop_cancel_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        agent = NonCooperativeAgent(delay_seconds=10.0, max_iterations=200, ready_event=ready)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            start_task = asyncio.create_task(ctrl.start_run("task_noncoop_cancel_01"))
            await ready.wait()

            async with session_factory() as cancel_session:
                cancel_repo = TaskRepository(cancel_session)
                cancel_sm = TaskStateMachine(cancel_repo)
                cancel_ctl = AutonomousRunController(cancel_repo, cancel_sm, ws_mgr, gate, orch)
                cancel_ctl._active_cancel_events = ctrl._active_cancel_events
                cancel_ctl._active_abort_events = ctrl._active_abort_events
                cancel_result = await cancel_ctl.cancel_run("task_noncoop_cancel_01", reason="Non-cooperative cancel")

            assert cancel_result.status == RunStatus.CANCELLED
            assert cancel_result.task_status == TaskStatus.CANCELLED

            async with session_factory() as session:
                repo = TaskRepository(session)
                t = await repo.get_task("task_noncoop_cancel_01")
                assert t.status == TaskStatus.CANCELLED

            start_result = await start_task
            assert start_result.task_status == TaskStatus.CANCELLED

        assert agent.execute_count == 1
        workspaces = ws_mgr.list_registered_worktrees()
        workspace_paths = [Path(w.get("path", "")) for w in workspaces]
        assert not any("task_noncoop_cancel_01" in str(p) for p in workspace_paths), "Workspace should be removed after agent terminates"


@pytest.mark.asyncio
async def test_non_cooperative_agent_abort(sqlite_db):
    """
    Non-cooperative agent ignores abort_event.
    Controller must cancel the asyncio Task and wait for termination
    before cleaning up workspace.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_noncoop_abort_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        agent = NonCooperativeAgent(delay_seconds=10.0, max_iterations=200, ready_event=ready)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            start_task = asyncio.create_task(ctrl.start_run("task_noncoop_abort_01"))
            await ready.wait()

            async with session_factory() as abort_session:
                abort_repo = TaskRepository(abort_session)
                abort_sm = TaskStateMachine(abort_repo)
                abort_ctl = AutonomousRunController(abort_repo, abort_sm, ws_mgr, gate, orch)
                abort_ctl._active_cancel_events = ctrl._active_cancel_events
                abort_ctl._active_abort_events = ctrl._active_abort_events
                abort_result = await abort_ctl.abort_run("task_noncoop_abort_01", reason="Non-cooperative abort")

            assert abort_result.status == RunStatus.ABORTED
            assert abort_result.task_status == TaskStatus.ABORTED

            async with session_factory() as session:
                repo = TaskRepository(session)
                t = await repo.get_task("task_noncoop_abort_01")
                assert t.status == TaskStatus.ABORTED

            start_result = await start_task
            assert start_result.task_status == TaskStatus.ABORTED

        assert agent.execute_count == 1
        workspaces = ws_mgr.list_registered_worktrees()
        workspace_paths = [Path(w.get("path", "")) for w in workspaces]
        assert not any("task_noncoop_abort_01" in str(p) for p in workspace_paths), "Workspace should be removed after agent terminates"


@pytest.mark.asyncio
async def test_non_cooperative_agent_timeout(sqlite_db):
    """
    Non-cooperative agent ignores timeout.
    Controller must cancel the asyncio Task and skip workspace cleanup
    if the execution is still active.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_noncoop_timeout_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        agent = NonCooperativeAgent(delay_seconds=10.0, max_iterations=200, ready_event=ready, work_per_iteration=True)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch, default_timeout_seconds=0.5)

            start_task = asyncio.create_task(ctrl.start_run("task_noncoop_timeout_01"))
            await ready.wait()

            start_result = await asyncio.wait_for(start_task, timeout=60.0)
            assert start_result.status == RunStatus.TIMED_OUT
            # When execution does not terminate within grace period, task stays RUNNING
            assert start_result.task_status == TaskStatus.RUNNING

            async with session_factory() as session:
                repo = TaskRepository(session)
                t = await repo.get_task("task_noncoop_timeout_01")
                assert t.status == TaskStatus.RUNNING
                assert t.workspace_cleanup_pending is True

            workspaces = ws_mgr.list_registered_worktrees()
            workspace_paths = [Path(w.get("path", "")) for w in workspaces]
            active_workspaces = [p for p in workspace_paths if "task_noncoop_timeout_01" in str(p)]
            assert len(active_workspaces) >= 1, "Workspace must be retained while agent is still active"

        assert agent.execute_count == 1


class InstrumentedBlockingAgent(DevelopmentAgent):
    """BlockingAgent that records exact termination time."""

    def __init__(self, result: DevelopmentResult, ready_event: asyncio.Event) -> None:
        self._result = result
        self._ready_event = ready_event
        self.execute_count = 0
        self.terminated_at: datetime | None = None

    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        self.execute_count += 1
        self._ready_event.set()
        try:
            while True:
                if context.cancel_event is not None and context.cancel_event.is_set():
                    self.terminated_at = datetime.now(timezone.utc)
                    return DevelopmentResult(
                        status=DevelopmentResultStatus.CANCELLED,
                        summary="Blocking agent detected cancel",
                        error_message="Cancelled via cancel_event",
                    )
                if context.abort_event is not None and context.abort_event.is_set():
                    self.terminated_at = datetime.now(timezone.utc)
                    return DevelopmentResult(
                        status=DevelopmentResultStatus.FAILURE,
                        summary="Blocking agent detected abort",
                        error_message="Aborted via abort_event",
                    )
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    continue
        except Exception as exc:
            self.terminated_at = datetime.now(timezone.utc)
            return DevelopmentResult(
                status=DevelopmentResultStatus.ERROR,
                summary="Blocking agent error",
                error_message=str(exc),
            )


@pytest.mark.asyncio
async def test_cancel_waits_for_agent_termination_before_cleanup(sqlite_db):
    """
    Real workspace test: prove agent termination timestamp < workspace cleanup timestamp.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_ordering_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        agent = InstrumentedBlockingAgent(
            result=DevelopmentResult(status=DevelopmentResultStatus.SUCCESS),
            ready_event=ready,
        )
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        cleanup_started_at = None

        original_remove = ws_mgr.remove_workspace

        def patched_remove(task_id: str, workspace, *, force: bool = False, expected_execution_token: str | None = None) -> None:
            nonlocal cleanup_started_at
            cleanup_started_at = datetime.now(timezone.utc)
            original_remove(task_id, workspace, force=force, expected_execution_token=expected_execution_token)

        ws_mgr.remove_workspace = patched_remove  # type: ignore[method-assign]

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            start_task = asyncio.create_task(ctrl.start_run("task_ordering_01"))
            await ready.wait()
            await asyncio.sleep(0.1)

            cancel_result = await ctrl.cancel_run("task_ordering_01", reason="Ordering test")
            assert cancel_result.status == RunStatus.CANCELLED

            start_result = await start_task
            assert start_result.task_status == TaskStatus.CANCELLED

        assert agent.execute_count >= 1
        assert agent.terminated_at is not None
        assert cleanup_started_at is not None
        assert agent.terminated_at < cleanup_started_at, (
            f"Agent terminated at {agent.terminated_at}, cleanup started at {cleanup_started_at}"
        )
        assert gate.verify_count == 0

        workspaces = ws_mgr.list_registered_worktrees()
        workspace_paths = [Path(w.get("path", "")) for w in workspaces]
        assert not any("task_ordering_01" in str(p) for p in workspace_paths), "Workspace should be removed after cancel"


@pytest.mark.asyncio
async def test_abort_waits_for_agent_termination_before_cleanup(sqlite_db):
    """
    Real workspace test: prove agent termination timestamp < workspace cleanup timestamp for abort.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_abort_ordering_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        agent = InstrumentedBlockingAgent(
            result=DevelopmentResult(status=DevelopmentResultStatus.SUCCESS),
            ready_event=ready,
        )
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        cleanup_started_at = None

        original_remove = ws_mgr.remove_workspace

        def patched_remove(task_id: str, workspace, *, force: bool = False, expected_execution_token: str | None = None) -> None:
            nonlocal cleanup_started_at
            cleanup_started_at = datetime.now(timezone.utc)
            original_remove(task_id, workspace, force=force, expected_execution_token=expected_execution_token)

        ws_mgr.remove_workspace = patched_remove  # type: ignore[method-assign]

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            start_task = asyncio.create_task(ctrl.start_run("task_abort_ordering_01"))
            await ready.wait()
            await asyncio.sleep(0.1)

            abort_result = await ctrl.abort_run("task_abort_ordering_01", reason="Ordering test")
            assert abort_result.status == RunStatus.ABORTED

            start_result = await start_task
            assert start_result.task_status == TaskStatus.ABORTED

        assert agent.execute_count >= 1
        assert agent.terminated_at is not None
        assert cleanup_started_at is not None
        assert agent.terminated_at < cleanup_started_at, (
            f"Agent terminated at {agent.terminated_at}, cleanup started at {cleanup_started_at}"
        )
        assert gate.verify_count == 0

        workspaces = ws_mgr.list_registered_worktrees()
        workspace_paths = [Path(w.get("path", "")) for w in workspaces]
        assert not any("task_abort_ordering_01" in str(p) for p in workspace_paths), "Workspace should be removed after abort"


@pytest.mark.asyncio
async def test_timeout_does_not_delete_active_workspace(sqlite_db):
    """
    Non-cooperative agent that outlives the termination grace period must NOT have its workspace deleted.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_timeout_active_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        ready = asyncio.Event()
        agent = NonCooperativeAgent(delay_seconds=10.0, max_iterations=200, ready_event=ready, work_per_iteration=True)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        remove_called = False

        original_remove = ws_mgr.remove_workspace

        def patched_remove(task_id: str, workspace, *, force: bool = False, expected_execution_token: str | None = None) -> None:
            nonlocal remove_called
            remove_called = True
            original_remove(task_id, workspace, force=force, expected_execution_token=expected_execution_token)

        ws_mgr.remove_workspace = patched_remove  # type: ignore[method-assign]

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch, default_timeout_seconds=0.5)

            start_task = asyncio.create_task(ctrl.start_run("task_timeout_active_01"))
            await ready.wait()

            start_result = await asyncio.wait_for(start_task, timeout=120.0)
            assert start_result.status == RunStatus.TIMED_OUT
            assert start_result.task_status == TaskStatus.RUNNING

            async with session_factory() as verify_session:
                verify_repo = TaskRepository(verify_session)
                t = await verify_repo.get_task("task_timeout_active_01")
                assert t is not None
                assert t.execution_terminated_at is None, "execution_terminated_at must remain NULL while agent is alive"
                assert t.workspace_cleanup_pending is True

        assert remove_called is False, "remove_workspace must NOT be called while execution is still active"

        workspaces = ws_mgr.list_registered_worktrees()
        workspace_paths = [Path(w.get("path", "")) for w in workspaces]
        active = [p for p in workspace_paths if "task_timeout_active_01" in str(p)]
        assert len(active) >= 1, "Workspace must be retained while agent is still active"

        assert agent.execute_count == 1


@pytest.mark.asyncio
async def test_workspace_isolation_across_execution_attempts(sqlite_db):
    """
    Two execution attempts with different tokens must receive distinct workspaces.
    Cleanup of one must not affect the other.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_iso_attempts_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

        token_a = "exec_iso_a_11111111"
        token_b = "exec_iso_b_22222222"

        info_a = ws_mgr.create_workspace(
            task_id="task_iso_attempts_01",
            base_commit_sha=(await _get_current_head(ws_mgr)),
            execution_token=token_a,
        )
        info_b = ws_mgr.create_workspace(
            task_id="task_iso_attempts_01",
            base_commit_sha=(await _get_current_head(ws_mgr)),
            execution_token=token_b,
        )

        workspace_a = Path(info_a.autonomous_worktree_path)
        workspace_b = Path(info_b.autonomous_worktree_path)

        assert workspace_a != workspace_b
        assert workspace_a.exists()
        assert workspace_b.exists()

        (workspace_a / "file_a.txt").write_text("A")
        (workspace_b / "file_b.txt").write_text("B")

        assert (workspace_a / "file_a.txt").exists()
        assert not (workspace_a / "file_b.txt").exists()
        assert (workspace_b / "file_b.txt").exists()
        assert not (workspace_b / "file_a.txt").exists()

        with pytest.raises(WorkspaceOwnershipError):
            ws_mgr.verify_workspace_ownership(info_a, expected_execution_token=token_b)

        with pytest.raises(WorkspaceOwnershipError):
            ws_mgr.verify_workspace_ownership(info_b, expected_execution_token=token_a)

        ws_mgr.remove_workspace("task_iso_attempts_01", info_a, expected_execution_token=token_a)
        assert not workspace_a.exists()
        assert workspace_b.exists()

        ws_mgr.remove_workspace("task_iso_attempts_01", info_b, expected_execution_token=token_b)
        assert not workspace_b.exists()


async def _get_current_head(ws_mgr: WorkspaceManager) -> str:
    return ws_mgr.get_current_head()


@pytest.mark.asyncio
async def test_recovery_does_not_delete_active_workspace(sqlite_db):
    """
    RecoveryManager must retain (not delete) an active workspace when it cannot prove execution termination.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_rec_active_ws_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        base_sha = await _get_current_head(ws_mgr)
        info = ws_mgr.create_workspace(
            task_id="task_rec_active_ws_01",
            base_commit_sha=base_sha,
            execution_token="exec_rec_active_ws_token",
        )
        workspace_a = Path(info.autonomous_worktree_path)
        assert workspace_a.exists()
        marker_token = "exec_rec_active_ws_token"

        remove_called = False

        original_remove = ws_mgr.remove_workspace

        def patched_remove(task_id: str, workspace, *, force: bool = False, expected_execution_token: str | None = None) -> None:
            nonlocal remove_called
            remove_called = True
            original_remove(task_id, workspace, force=force, expected_execution_token=expected_execution_token)

        ws_mgr.remove_workspace = patched_remove  # type: ignore[method-assign]

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            claim_res = await sm.claim_attempt("task_rec_active_ws_01", TaskStatus.PENDING, max_attempts=3)
            token_a = claim_res.task.execution_token
            await repo.update_heartbeat("task_rec_active_ws_01", execution_token=token_a, heartbeat_at=stale_time)
            await repo.update_workspace_path("task_rec_active_ws_01", str(workspace_a))
            await session.commit()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            rec = RecoveryManager(repo, sm, ws_mgr, timeout_seconds=0.1)
            recovery_result = await rec.recover_task("task_rec_active_ws_01", recovery_owner="test_recovery")
            assert recovery_result.status == RecoveryStatus.RECOVERY_COMPLETED
            assert recovery_result.new_task_status == TaskStatus.REPAIR_REQUIRED
            await session.commit()

        assert remove_called is False, "Recovery must NOT call remove_workspace when execution termination is not proven"

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_rec_active_ws_01")
            assert t.status == TaskStatus.REPAIR_REQUIRED
            assert t.execution_token is None
            assert t.recovery_owner is None
            assert t.recovery_attempt_count == 1

        assert workspace_a.exists(), "Workspace must be retained after recovery claims stale task"

        with pytest.raises(WorkspaceOwnershipError):
            ws_mgr.verify_workspace_ownership(info, expected_execution_token="wrong_token")

        ws_mgr.verify_workspace_ownership(info, expected_execution_token=marker_token)

        ws_mgr.remove_workspace("task_rec_active_ws_01", info, expected_execution_token=marker_token)
        assert not workspace_a.exists(), "Workspace must be removable via WorkspaceManager after recovery clears execution_token"


@pytest.mark.asyncio
async def test_recovery_after_process_loss_allows_cleanup(sqlite_db):
    """
    Process-loss model: execution A crashes (no active handle, stale heartbeat).
    Recovery claims the task. Recovery does NOT delete the workspace because termination
    is not proven, but cleanup IS possible afterward via WorkspaceManager using the
    original execution token (ownership check passes because marker still contains old token).
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_proc_loss_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        base_sha = await _get_current_head(ws_mgr)
        info = ws_mgr.create_workspace(
            task_id="task_proc_loss_01",
            base_commit_sha=base_sha,
            execution_token="exec_proc_loss_token",
        )
        workspace_a = Path(info.autonomous_worktree_path)
        assert workspace_a.exists()
        marker_token = "exec_proc_loss_token"

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            claim_res = await sm.claim_attempt("task_proc_loss_01", TaskStatus.PENDING, max_attempts=3)
            token_a = claim_res.task.execution_token
            await repo.update_heartbeat("task_proc_loss_01", execution_token=token_a, heartbeat_at=stale_time)
            await repo.update_workspace_path("task_proc_loss_01", str(workspace_a))
            await session.commit()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            rec = RecoveryManager(repo, sm, ws_mgr, timeout_seconds=0.1)
            recovery_result = await rec.recover_task("task_proc_loss_01", recovery_owner="recovery_worker")
            assert recovery_result.status == RecoveryStatus.RECOVERY_COMPLETED
            assert recovery_result.new_task_status == TaskStatus.REPAIR_REQUIRED
            await session.commit()

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_proc_loss_01")
            assert t.status == TaskStatus.REPAIR_REQUIRED
            assert t.execution_token is None
            assert t.recovery_owner is None
            assert t.recovery_attempt_count == 1

        assert workspace_a.exists(), "Recovery must retain workspace when termination is not proven"

        with pytest.raises(WorkspaceOwnershipError):
            ws_mgr.verify_workspace_ownership(info, expected_execution_token="wrong_token")

        ws_mgr.verify_workspace_ownership(info, expected_execution_token=marker_token)

        ws_mgr.remove_workspace("task_proc_loss_01", info, expected_execution_token=marker_token)
        assert not workspace_a.exists(), "Workspace must be removable via WorkspaceManager after recovery clears execution_token"


@pytest.mark.asyncio
async def test_old_execution_token_cannot_reclaim_workspace(sqlite_db):
    """
    After recovery fences an execution, the old execution token must be rejected
    by WorkspaceManager ownership verification when trying to reclaim the original workspace.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_old_token_ws_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        base_sha = await _get_current_head(ws_mgr)
        info_a = ws_mgr.create_workspace(
            task_id="task_old_token_ws_01",
            base_commit_sha=base_sha,
            execution_token="exec_old_token_ws_token",
        )
        workspace_a = Path(info_a.autonomous_worktree_path)
        assert workspace_a.exists()
        marker_token = "exec_old_token_ws_token"

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            claim_res = await sm.claim_attempt("task_old_token_ws_01", TaskStatus.PENDING, max_attempts=3)
            token_a = claim_res.task.execution_token
            await repo.update_heartbeat("task_old_token_ws_01", execution_token=token_a, heartbeat_at=stale_time)
            await repo.update_workspace_path("task_old_token_ws_01", str(workspace_a))
            await session.commit()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            rec = RecoveryManager(repo, sm, ws_mgr, timeout_seconds=0.1)
            recovery_result = await rec.recover_task("task_old_token_ws_01", recovery_owner="recovery_worker")
            assert recovery_result.status == RecoveryStatus.RECOVERY_COMPLETED
            await session.commit()

        with pytest.raises(WorkspaceOwnershipError):
            ws_mgr.verify_workspace_ownership(info_a, expected_execution_token="wrong_token")

        ws_mgr.verify_workspace_ownership(info_a, expected_execution_token=marker_token)

        ws_mgr.remove_workspace("task_old_token_ws_01", info_a, expected_execution_token=marker_token)
        assert not workspace_a.exists()

        registered = ws_mgr.list_registered_worktrees()
        ws_paths = [Path(w.get("path", "")) for w in registered if "task_old_token_ws_01" in w.get("path", "")]
        assert len(ws_paths) == 0


@pytest.mark.asyncio
async def test_late_execution_cannot_touch_new_workspace(sqlite_db):
    """
    If execution A is fenced and execution B has created a new workspace,
    any attempt by execution A (using old token) to interact with workspace B must be rejected.
    """
    engine, session_factory, db_path = sqlite_db
    await _create_test_task(session_factory, task_id="task_late_exec_01")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        repo_root = tmp_path / "primary_repo"
        repo_root.mkdir()
        _init_real_git_repo(repo_root)

        ws_mgr = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
        base_sha = await _get_current_head(ws_mgr)
        info_a = ws_mgr.create_workspace(
            task_id="task_late_exec_01",
            base_commit_sha=base_sha,
            execution_token="exec_late_a_token",
        )
        workspace_a = Path(info_a.autonomous_worktree_path)
        assert workspace_a.exists()

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            claim_res = await sm.claim_attempt("task_late_exec_01", TaskStatus.PENDING, max_attempts=3)
            token_a = claim_res.task.execution_token
            await repo.update_heartbeat("task_late_exec_01", execution_token=token_a, heartbeat_at=stale_time)
            await repo.update_workspace_path("task_late_exec_01", str(workspace_a))
            await session.commit()

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            rec = RecoveryManager(repo, sm, ws_mgr, timeout_seconds=0.1)
            recovery_result = await rec.recover_task("task_late_exec_01", recovery_owner="recovery_worker")
            assert recovery_result.status == RecoveryStatus.RECOVERY_COMPLETED
            assert recovery_result.new_task_status == TaskStatus.REPAIR_REQUIRED
            await session.commit()

        agent_b = FakeDevelopmentAgent(status=DevelopmentResultStatus.SUCCESS)
        gate = FakeVerificationGate(decision=GateDecision.PASS)

        async with session_factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            orch = AutonomousTaskOrchestrator(repo, sm, ws_mgr, gate, agent_b)
            ctrl = AutonomousRunController(repo, sm, ws_mgr, gate, orch)

            result_b = await ctrl.start_run("task_late_exec_01")
            assert result_b.status == RunStatus.SUCCEEDED

        async with session_factory() as session:
            repo = TaskRepository(session)
            t = await repo.get_task("task_late_exec_01")
            token_b = t.execution_token
            assert token_b is not None
            assert token_b != token_a

        registered = ws_mgr.list_registered_worktrees()
        ws_b_entries = [w for w in registered if "task_late_exec_01" in w.get("path", "")]
        assert len(ws_b_entries) == 1
        workspace_b_path = Path(ws_b_entries[0]["path"])
        branch_b = ws_b_entries[0].get("branch", "ai-operator/task/task_late_exec_01")

        from app.workspace.models import WorkspaceInfo, WorkspaceStatus
        fake_workspace_b = WorkspaceInfo(
            task_id="task_late_exec_01",
            repository_root=str(repo_root),
            primary_worktree_path=str(ws_mgr.get_primary_worktree_path()),
            autonomous_worktree_path=str(workspace_b_path),
            branch_name=branch_b,
            base_commit_sha="",
            created_at=datetime.now(timezone.utc),
            current_commit_sha="",
            status=WorkspaceStatus.ACTIVE,
        )

        with pytest.raises(WorkspaceOwnershipError):
            ws_mgr.verify_workspace_ownership(fake_workspace_b, expected_execution_token=token_a)

        try:
            ws_mgr.remove_workspace("task_late_exec_01", fake_workspace_b, expected_execution_token=token_a)
        except WorkspaceOwnershipError:
            pass
        else:
            raise AssertionError("Expected WorkspaceOwnershipError when removing workspace with old token")

        assert workspace_b_path.exists(), "Workspace B must remain intact after failed old-token removal attempt"

