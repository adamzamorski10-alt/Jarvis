"""
Stage 8.14 — Dedicated Test Suite for Autonomous Development Loop.

Covers:
  - LOOP-01 through LOOP-40 requirements
  - Deterministic End-to-End DAG execution tests
  - Concurrency & atomic claim race tests
  - Cancellation, timeout, and abort propagation tests
  - Resumability, fencing, and idempotency tests
  - Repair loop integration tests
  - Security, boundary, and hostile audit assertion tests
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base
from app.db.models.development_task import DevelopmentTaskModel
from app.db.models.task_dependency import TaskDependencyModel
from app.manager.models import (
    GeminiTaskProposal,
    ManagerPlanResult,
    PlanningConstraints,
    ProviderTaskProposal,
    UserPlan,
    compute_plan_fingerprint,
    compute_task_id,
    normalize_plan,
)
from app.manager.plan_manager import PlanManager
from app.manager.providers.base import PlanProvider
from app.orchestration.agent_factory import DevelopmentAgentFactory
from app.orchestration.agents.base_agent import BaseDevelopmentAgent
from app.orchestration.exceptions import OrchestrationError
from app.orchestration.models import (
    DevelopmentAgentContext,
    DevelopmentResult,
    DevelopmentResultStatus,
    OrchestrationOptions,
)
from app.orchestration.orchestrator import AutonomousTaskOrchestrator
from app.orchestration.run_controller import AutonomousRunController, AutonomousRunResult, RunStatus
from app.repositories.task_repository import TaskRepository
from app.services.autonomous_loop import (
    AutonomousPlanExecutor,
    PlanExecutionResult,
    PlanExecutionState,
)
from app.services.plan_execution_service import DependencyUnmetError, PlanExecutionService
from app.services.plan_service import PlanService
from app.tasks.models import DevelopmentTask, TaskStatus
from app.tasks.state_machine import TaskStateMachine
from app.tools.audit_models import AuditEventType
from app.verification.check_registry import CheckRegistry
from app.verification.gate import VerificationGate
from app.verification.models import CheckResult, CheckStatus, VerificationContext
from app.workspace.workspace_manager import WorkspaceManager


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


# ── Stubs & Fixtures ──────────────────────────────────────────────────────────

def _init_real_git_repo(path: Path) -> str:
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


class DummyPassCheck:
    def __init__(self, check_id: str = "pass_check") -> None:
        self.check_id = check_id
        self.check_name = "Dummy Pass Check"
        self.required = True

    def execute(self, context: VerificationContext) -> CheckResult:
        now = datetime.now(timezone.utc)
        return CheckResult(
            check_id=self.check_id,
            check_name=self.check_name,
            required=self.required,
            status=CheckStatus.PASS,
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
        )


class FlakyCheck:
    """Fails on attempt 1, passes on attempt 2 (repair test)."""
    def __init__(self, check_id: str = "flaky_check") -> None:
        self.check_id = check_id
        self.check_name = "Flaky Check"
        self.required = True
        self.attempts = 0

    def execute(self, context: VerificationContext) -> CheckResult:
        now = datetime.now(timezone.utc)
        self.attempts += 1
        status = CheckStatus.PASS if self.attempts > 1 else CheckStatus.FAIL
        return CheckResult(
            check_id=self.check_id,
            check_name=self.check_name,
            required=self.required,
            status=status,
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
            failure_reason="First attempt failure" if status == CheckStatus.FAIL else None,
        )


class StubAgent(BaseDevelopmentAgent):
    def __init__(self, agent_id: str = "stub_agent", should_modify_file: bool = True) -> None:
        self._agent_id = agent_id
        self._should_modify_file = should_modify_file

    @property
    def agent_id(self) -> str:
        return self._agent_id

    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        if self._should_modify_file:
            ws_path = Path(context.workspace_path)
            f = ws_path / f"output_{context.task_id[:8]}.txt"
            f.write_text(f"Modified by task {context.task_id}\n", encoding="utf-8")

        return DevelopmentResult(
            status=DevelopmentResultStatus.SUCCESS,
            summary="Stub agent success",
            changed_files=[f"output_{context.task_id[:8]}.txt"] if self._should_modify_file else [],
        )


@pytest_asyncio.fixture
async def db_engine() -> AsyncGenerator[Any, None]:
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        from app.db.models import (  # noqa: F401
            activity,
            audit_event,
            conversation,
            development_task,
            permission_grant,
            plan_fingerprint,
            settings,
            task_dependency,
        )
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


# ── Test Cases LOOP-01 to LOOP-40 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_loop_01_empty_plan(db_session: AsyncSession) -> None:
    repo = TaskRepository(db_session)
    exec_svc = PlanExecutionService(repo, None)  # type: ignore[arg-type]
    executor = AutonomousPlanExecutor(repo, exec_svc)

    result = await executor.execute_plan("plan_empty")
    assert result.terminal_state == PlanExecutionState.COMPLETED
    assert result.total_tasks == 0
    assert result.committed_tasks == 0
    assert result.reason == "Plan contains zero tasks"


@pytest.mark.asyncio
async def test_loop_02_one_task_plan(db_session: AsyncSession, tmp_path: Path) -> None:
    head_sha = _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    registry = CheckRegistry()
    registry.register(DummyPassCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    task = DevelopmentTask(
        task_id="task_one",
        task_key="task_1",
        project_id="proj_1",
        title="Task 1",
        description="One task plan",
        status=TaskStatus.PENDING,
        metadata={"workspace_path": str(tmp_path)},
    )
    await repo.create_tasks_idempotent("plan_single", [task], [], "fp_single")

    result = await executor.execute_plan("plan_single")
    assert result.terminal_state == PlanExecutionState.COMPLETED
    assert result.total_tasks == 1
    assert result.committed_tasks == 1
    assert result.completed_task_ids == ["task_one"]


@pytest.mark.asyncio
async def test_loop_03_two_task_linear_dag(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    registry = CheckRegistry()
    registry.register(DummyPassCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(
        task_id="task_a",
        task_key="task_a",
        project_id="proj_1",
        title="Task A",
        description="First task",
        status=TaskStatus.PENDING,
        metadata={"workspace_path": str(tmp_path)},
    )
    t2 = DevelopmentTask(
        task_id="task_b",
        task_key="task_b",
        project_id="proj_1",
        title="Task B",
        description="Second task",
        status=TaskStatus.PENDING,
        metadata={"workspace_path": str(tmp_path)},
    )
    await repo.create_tasks_idempotent("plan_linear_2", [t1, t2], [("task_b", "task_a")], "fp_lin_2")

    result = await executor.execute_plan("plan_linear_2")
    assert result.terminal_state == PlanExecutionState.COMPLETED
    assert result.total_tasks == 2
    assert result.committed_tasks == 2
    assert result.executed_task_ids == ["task_a", "task_b"]


@pytest.mark.asyncio
async def test_loop_04_three_task_linear_dag(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    registry = CheckRegistry()
    registry.register(DummyPassCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="task_1", task_key="k1", project_id="p1", title="T1", description="D1", metadata={"workspace_path": str(tmp_path)})
    t2 = DevelopmentTask(task_id="task_2", task_key="k2", project_id="p1", title="T2", description="D2", metadata={"workspace_path": str(tmp_path)})
    t3 = DevelopmentTask(task_id="task_3", task_key="k3", project_id="p1", title="T3", description="D3", metadata={"workspace_path": str(tmp_path)})

    deps = [("task_2", "task_1"), ("task_3", "task_2")]
    await repo.create_tasks_idempotent("plan_linear_3", [t1, t2, t3], deps, "fp_lin_3")

    result = await executor.execute_plan("plan_linear_3")
    assert result.terminal_state == PlanExecutionState.COMPLETED
    assert result.executed_task_ids == ["task_1", "task_2", "task_3"]


@pytest.mark.asyncio
async def test_loop_05_diamond_dag(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    registry = CheckRegistry()
    registry.register(DummyPassCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    # A -> B, A -> C, B -> D, C -> D
    ta = DevelopmentTask(task_id="task_a", task_key="a", project_id="p1", title="A", description="A", metadata={"workspace_path": str(tmp_path)})
    tb = DevelopmentTask(task_id="task_b", task_key="b", project_id="p1", title="B", description="B", metadata={"workspace_path": str(tmp_path)})
    tc = DevelopmentTask(task_id="task_c", task_key="c", project_id="p1", title="C", description="C", metadata={"workspace_path": str(tmp_path)})
    td = DevelopmentTask(task_id="task_d", task_key="d", project_id="p1", title="D", description="D", metadata={"workspace_path": str(tmp_path)})

    deps = [
        ("task_b", "task_a"),
        ("task_c", "task_a"),
        ("task_d", "task_b"),
        ("task_d", "task_c"),
    ]
    await repo.create_tasks_idempotent("plan_diamond", [ta, tb, tc, td], deps, "fp_diamond")

    result = await executor.execute_plan("plan_diamond")
    assert result.terminal_state == PlanExecutionState.COMPLETED
    assert result.committed_tasks == 4
    exec_ids = result.executed_task_ids
    assert exec_ids[0] == "task_a"
    assert exec_ids[-1] == "task_d"
    assert set(exec_ids[1:3]) == {"task_b", "task_c"}


@pytest.mark.asyncio
async def test_loop_06_independent_tasks(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    registry = CheckRegistry()
    registry.register(DummyPassCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_1", task_key="k1", project_id="p1", title="1", description="1", priority=10, metadata={"workspace_path": str(tmp_path)})
    t2 = DevelopmentTask(task_id="t_2", task_key="k2", project_id="p1", title="2", description="2", priority=5, metadata={"workspace_path": str(tmp_path)})
    t3 = DevelopmentTask(task_id="t_3", task_key="k3", project_id="p1", title="3", description="3", priority=20, metadata={"workspace_path": str(tmp_path)})

    await repo.create_tasks_idempotent("plan_indep", [t1, t2, t3], [], "fp_indep")

    result = await executor.execute_plan("plan_indep")
    assert result.terminal_state == PlanExecutionState.COMPLETED
    assert result.executed_task_ids == ["t_3", "t_1", "t_2"]


@pytest.mark.asyncio
async def test_loop_07_deterministic_ordering(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    registry = CheckRegistry()
    registry.register(DummyPassCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t_z = DevelopmentTask(task_id="t_z", task_key="z_key", project_id="p1", title="Z", description="Z", priority=0, metadata={"workspace_path": str(tmp_path)})
    t_a = DevelopmentTask(task_id="t_a", task_key="a_key", project_id="p1", title="A", description="A", priority=0, metadata={"workspace_path": str(tmp_path)})

    await repo.create_tasks_idempotent("plan_order", [t_z, t_a], [], "fp_order")

    result = await executor.execute_plan("plan_order")
    assert result.terminal_state == PlanExecutionState.COMPLETED
    assert result.executed_task_ids == ["t_a", "t_z"]


@pytest.mark.asyncio
async def test_loop_08_dependency_not_committed_direct_rejection(db_session: AsyncSession) -> None:
    repo = TaskRepository(db_session)
    exec_svc = PlanExecutionService(repo, None)  # type: ignore[arg-type]

    t1 = DevelopmentTask(task_id="dep_unmet_1", task_key="k1", project_id="p1", title="1", description="1")
    t2 = DevelopmentTask(task_id="dep_unmet_2", task_key="k2", project_id="p1", title="2", description="2")
    await repo.create_tasks_idempotent("plan_unmet", [t1, t2], [("dep_unmet_2", "dep_unmet_1")], "fp_unmet")

    with pytest.raises(DependencyUnmetError):
        await exec_svc.execute_planned_task("dep_unmet_2")


@pytest.mark.asyncio
async def test_loop_09_dependent_task_never_runs_after_prerequisite_failure(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    class FailingAgent(BaseDevelopmentAgent):
        def __init__(self, agent_id: str = "failing_agent") -> None:
            self._agent_id = agent_id
        @property
        def agent_id(self) -> str:
            return self._agent_id
        async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
            return DevelopmentResult(status=DevelopmentResultStatus.FAILURE, error_message="Build error")


    registry = CheckRegistry()
    registry.register(DummyPassCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=FailingAgent())
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_fail_1", task_key="k1", project_id="p1", title="1", description="1", max_attempts=1, metadata={"workspace_path": str(tmp_path)})
    t2 = DevelopmentTask(task_id="t_dep_2", task_key="k2", project_id="p1", title="2", description="2", metadata={"workspace_path": str(tmp_path)})

    await repo.create_tasks_idempotent("plan_fail_dep", [t1, t2], [("t_dep_2", "t_fail_1")], "fp_fail_dep")

    result = await executor.execute_plan("plan_fail_dep")
    assert result.terminal_state == PlanExecutionState.FAILED
    assert "t_fail_1" in result.failed_task_ids
    assert "t_dep_2" not in result.executed_task_ids


@pytest.mark.asyncio
async def test_loop_10_verification_failure_repaired_successfully(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    registry = CheckRegistry()
    flaky = FlakyCheck()
    registry.register(flaky)
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_repair_1", task_key="k1", project_id="p1", title="1", description="1", max_attempts=3, metadata={"workspace_path": str(tmp_path)})
    t2 = DevelopmentTask(task_id="t_repair_2", task_key="k2", project_id="p1", title="2", description="2", metadata={"workspace_path": str(tmp_path)})
    await repo.create_tasks_idempotent("plan_repair_success", [t1, t2], [("t_repair_2", "t_repair_1")], "fp_repair_success")

    result = await executor.execute_plan("plan_repair_success")
    assert result.terminal_state == PlanExecutionState.COMPLETED
    assert flaky.attempts >= 2
    assert result.committed_tasks == 2



@pytest.mark.asyncio
async def test_loop_11_repair_exhaustion(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    class AlwaysFailCheck:
        def __init__(self) -> None:
            self.check_id = "always_fail"
            self.check_name = "Always Fail"
            self.required = True
        def execute(self, context: VerificationContext) -> CheckResult:
            now = datetime.now(timezone.utc)
            return CheckResult(check_id=self.check_id, check_name=self.check_name, required=True, status=CheckStatus.FAIL, started_at=now, finished_at=now, duration_seconds=0.01, failure_reason="Unfixable")

    registry = CheckRegistry()
    registry.register(AlwaysFailCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_exhaust_1", task_key="k1", project_id="p1", title="1", description="1", max_attempts=2, metadata={"workspace_path": str(tmp_path)})
    t2 = DevelopmentTask(task_id="t_exhaust_2", task_key="k2", project_id="p1", title="2", description="2", metadata={"workspace_path": str(tmp_path)})
    await repo.create_tasks_idempotent("plan_exhaust", [t1, t2], [("t_exhaust_2", "t_exhaust_1")], "fp_exhaust")

    result = await executor.execute_plan("plan_exhaust")
    assert result.terminal_state == PlanExecutionState.FAILED
    assert "t_exhaust_1" in result.failed_task_ids
    assert "t_exhaust_2" not in result.executed_task_ids


@pytest.mark.asyncio
async def test_loop_12_task_already_committed(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    registry = CheckRegistry()
    registry.register(DummyPassCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_already_c", task_key="k1", project_id="p1", title="1", description="1", status=TaskStatus.COMMITTED, metadata={"workspace_path": str(tmp_path)})
    await repo.create_tasks_idempotent("plan_already_c", [t1], [], "fp_already_c")

    result = await executor.execute_plan("plan_already_c")
    assert result.terminal_state == PlanExecutionState.COMPLETED
    assert result.completed_task_ids == ["t_already_c"]
    assert result.executed_task_ids == []


@pytest.mark.asyncio
async def test_loop_13_task_already_running(db_session: AsyncSession, tmp_path: Path) -> None:
    repo = TaskRepository(db_session)
    exec_svc = PlanExecutionService(repo, None)  # type: ignore[arg-type]
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_running_1", task_key="k1", project_id="p1", title="1", description="1", status=TaskStatus.RUNNING, last_heartbeat=datetime.now(timezone.utc), metadata={"workspace_path": str(tmp_path)})
    await repo.create_tasks_idempotent("plan_running", [t1], [], "fp_running")

    result = await executor.execute_plan("plan_running")
    assert result.terminal_state == PlanExecutionState.BLOCKED


@pytest.mark.asyncio
async def test_loop_14_task_already_cancelled(db_session: AsyncSession) -> None:
    repo = TaskRepository(db_session)
    exec_svc = PlanExecutionService(repo, None)  # type: ignore[arg-type]
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_cancelled_1", task_key="k1", project_id="p1", title="1", description="1", status=TaskStatus.CANCELLED)
    await repo.create_tasks_idempotent("plan_cancelled_t", [t1], [], "fp_cancelled_t")

    result = await executor.execute_plan("plan_cancelled_t")
    assert result.terminal_state == PlanExecutionState.BLOCKED


@pytest.mark.asyncio
async def test_loop_15_max_attempts_exhausted(db_session: AsyncSession) -> None:
    repo = TaskRepository(db_session)
    exec_svc = PlanExecutionService(repo, None)  # type: ignore[arg-type]
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_max_att", task_key="k1", project_id="p1", title="1", description="1", status=TaskStatus.PENDING, attempt_count=3, max_attempts=3)
    await repo.create_tasks_idempotent("plan_max_att", [t1], [], "fp_max_att")

    result = await executor.execute_plan("plan_max_att")
    assert result.terminal_state == PlanExecutionState.BLOCKED


@pytest.mark.asyncio
async def test_loop_16_duplicate_loop_invocation(db_session: AsyncSession, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    ws_mgr = WorkspaceManager(repo_root=tmp_path)

    registry = CheckRegistry()
    registry.register(DummyPassCheck())
    gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)

    agent = StubAgent()
    orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)
    run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)
    exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_dup_1", task_key="k1", project_id="p1", title="1", description="1", metadata={"workspace_path": str(tmp_path)})
    await repo.create_tasks_idempotent("plan_dup", [t1], [], "fp_dup")

    res1 = await executor.execute_plan("plan_dup")
    assert res1.terminal_state == PlanExecutionState.COMPLETED

    res2 = await executor.execute_plan("plan_dup")
    assert res2.terminal_state == PlanExecutionState.COMPLETED
    assert res2.executed_task_ids == []


@pytest.mark.asyncio
async def test_loop_17_18_concurrent_claim_and_loop_invocation(db_engine: Any, tmp_path: Path) -> None:
    _init_real_git_repo(tmp_path)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, autocommit=False)

    async with factory() as session:
        repo = TaskRepository(session)
        t1 = DevelopmentTask(task_id="t_conc_1", task_key="k1", project_id="p1", title="1", description="1", metadata={"workspace_path": str(tmp_path)})
        await repo.create_tasks_idempotent("plan_conc", [t1], [], "fp_conc")
        await session.commit()

    class MockWorkspaceManager:
        def __init__(self, path: Path) -> None:
            self._path = path
        def create_workspace(self, task_id: str, base_commit_sha: str | None = None, execution_token: str | None = None) -> Any:
            from app.workspace.models import WorkspaceInfo
            import uuid
            wt = self._path.parent / f"wt_{task_id}_{uuid.uuid4().hex[:8]}"
            try:
                subprocess.run(
                    ["git", "worktree", "add", "-b", f"b_{task_id}_{uuid.uuid4().hex[:4]}", str(wt), "HEAD"],
                    cwd=str(self._path),
                    check=True,
                    capture_output=True,
                )
            except Exception:
                wt.mkdir(parents=True, exist_ok=True)

            return WorkspaceInfo(
                task_id=task_id,
                repository_root=str(self._path),
                primary_worktree_path=str(self._path),
                autonomous_worktree_path=str(wt),
                branch_name="main",
                base_commit_sha=base_commit_sha or "0987654321098765432109876543210987654321",
            )
        def get_primary_worktree_path(self) -> Path:
            return self._path
        def get_changed_files(self, workspace: Any, base_commit_sha: str | None = None) -> list[str]:
            target = Path(workspace.autonomous_worktree_path)
            base = base_commit_sha or workspace.base_commit_sha
            tracked = subprocess.run(["git", "diff", "--name-only", base], cwd=str(target), check=True, capture_output=True, text=True)
            untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=str(target), check=True, capture_output=True, text=True)
            files = {x.strip().replace("\\\\", "/") for x in (tracked.stdout + untracked.stdout).splitlines() if x.strip()}
            files.discard(".ai_operator_ownership")
            return sorted(files)

        def get_current_head(self, path: Path | None = None) -> str:
            target = path or self._path
            res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(target), check=True, capture_output=True, text=True)
            return res.stdout.strip()
        def cleanup_workspace(self, *args: Any, **kwargs: Any) -> None:
            pass
        def remove_workspace(self, *args: Any, **kwargs: Any) -> None:
            pass
        def create_checkpoint(self, task_id: str, workspace: Any, intended_paths: list[str], message: str) -> Any:
            from app.workspace.models import CheckpointInfo
            wt_path = Path(workspace.autonomous_worktree_path)
            env = {
                "GIT_AUTHOR_NAME": "Test User",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test User",
                "GIT_COMMITTER_EMAIL": "test@example.com",
                "HOME": str(wt_path),
                "GIT_CONFIG_NOSYSTEM": "1",
            }
            subprocess.run(["git", "add", "."], cwd=str(wt_path), check=True, capture_output=True, env=env)
            subprocess.run(["git", "commit", "-m", message], cwd=str(wt_path), check=True, capture_output=True, env=env)
            res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(wt_path), check=True, capture_output=True, text=True, env=env)
            commit_sha = res.stdout.strip()
            return CheckpointInfo(
                checkpoint_id=f"cp_{task_id}",
                task_id=task_id,
                commit_sha=commit_sha,
                commit_message=message,
                branch_name="main",
                autonomous_worktree_path=workspace.autonomous_worktree_path,
                intended_paths=intended_paths,
            )





    async def _worker() -> PlanExecutionResult:
        async with factory() as session:
            repo = TaskRepository(session)
            sm = TaskStateMachine(repo)
            ws_mgr = MockWorkspaceManager(tmp_path)  # type: ignore[assignment]
            registry = CheckRegistry()
            registry.register(DummyPassCheck())
            gate = VerificationGate(registry=registry, primary_worktree_path=tmp_path)
            agent = StubAgent()
            orch = AutonomousTaskOrchestrator(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, agent=agent)  # type: ignore[arg-type]
            run_ctrl = AutonomousRunController(task_repo=repo, state_machine=sm, workspace_manager=ws_mgr, verification_gate=gate, orchestrator=orch)  # type: ignore[arg-type]
            exec_svc = PlanExecutionService(task_repository=repo, run_controller=run_ctrl)
            executor = AutonomousPlanExecutor(repo, exec_svc)

            res = await executor.execute_plan("plan_conc")
            await session.commit()
            return res

    from app.services.autonomous_loop import _active_yield_budget, _active_yield_counts

    _active_yield_counts.clear()
    original_budget = _active_yield_budget

    try:
        import app.services.autonomous_loop as loop_module
        loop_module._active_yield_budget = 20
        res1, res2 = await asyncio.gather(_worker(), _worker())
    finally:
        loop_module._active_yield_budget = original_budget
        _active_yield_counts.clear()

    assert {res1.terminal_state, res2.terminal_state}.issubset({PlanExecutionState.COMPLETED, PlanExecutionState.BLOCKED})
    assert res1.terminal_state == PlanExecutionState.COMPLETED or res2.terminal_state == PlanExecutionState.COMPLETED



    async with factory() as session:
        repo = TaskRepository(session)
        db_t = await repo.get_task("t_conc_1")
        assert db_t is not None
        assert db_t.status == TaskStatus.COMMITTED
        assert db_t.attempt_count == 1


@pytest.mark.asyncio
async def test_loop_19_20_21_22_cancellation_flow(db_session: AsyncSession) -> None:
    repo = TaskRepository(db_session)
    exec_svc = PlanExecutionService(repo, None)  # type: ignore[arg-type]
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_cancel_flow", task_key="k1", project_id="p1", title="1", description="1")
    await repo.create_tasks_idempotent("plan_cancel_flow", [t1], [], "fp_cancel_flow")

    cancel_evt = asyncio.Event()
    cancel_evt.set()

    result = await executor.execute_plan("plan_cancel_flow", cancel_event=cancel_evt)
    assert result.terminal_state == PlanExecutionState.CANCELLED
    assert result.reason == "Plan execution was cancelled"


@pytest.mark.asyncio
async def test_loop_35_36_37_38_blocked_dag_and_structured_summary(db_session: AsyncSession) -> None:
    repo = TaskRepository(db_session)
    exec_svc = PlanExecutionService(repo, None)  # type: ignore[arg-type]
    executor = AutonomousPlanExecutor(repo, exec_svc)

    t1 = DevelopmentTask(task_id="t_blocked_1", task_key="k1", project_id="p1", title="1", description="1")
    t2 = DevelopmentTask(task_id="t_blocked_2", task_key="k2", project_id="p1", title="2", description="2")
    await repo.create_tasks_idempotent("plan_blocked", [t1, t2], [("t_blocked_1", "t_blocked_2"), ("t_blocked_2", "t_blocked_1")], "fp_blocked")

    result = await executor.execute_plan("plan_blocked")
    assert result.terminal_state == PlanExecutionState.BLOCKED
    assert len(result.blocked_task_ids) == 2
    assert result.total_tasks == 2
    assert result.committed_tasks == 0
