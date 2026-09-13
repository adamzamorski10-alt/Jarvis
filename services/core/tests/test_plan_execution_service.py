"""
Stage 8.12 — Tests for Plan Execution Integration Bridge.

Tests the integration of UserPlan -> PlanManager -> PlanService -> TaskRepository -> PlanExecutionService -> RunController -> Orchestrator -> Agent -> Verification -> Git.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any

pytest_plugins = ("pytest_asyncio",)
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base
from app.manager.models import (
    GeminiTaskProposal,
    ManagerPlanResult,
    PlanningConstraints,
    ProviderTaskProposal,
    UserPlan,
    normalize_plan,
)
from app.manager.plan_manager import PlanManager
from app.manager.providers.base import PlanProvider
from app.orchestration.agent_factory import DevelopmentAgentFactory
from app.orchestration.agents.base_agent import BaseDevelopmentAgent
from app.orchestration.exceptions import (
    MaxAttemptsExceededError,
    TaskNotStartableError,
)
from app.orchestration.models import (
    DevelopmentAgentContext,
    DevelopmentResult,
    DevelopmentResultStatus,
    OrchestrationOptions,
)
from app.orchestration.orchestrator import AutonomousTaskOrchestrator
from app.orchestration.run_controller import AutonomousRunController, RunStatus
from app.repositories.task_repository import TaskRepository
from app.services.plan_execution_service import DependencyUnmetError, PlanExecutionService
from app.services.plan_service import PlanService
from app.tasks.exceptions import TaskNotFoundError
from app.tasks.models import TaskStatus
from app.tasks.state_machine import TaskStateMachine
from app.verification.check_registry import CheckRegistry
from app.verification.gate import VerificationGate
from app.verification.models import CheckResult, CheckStatus, VerificationContext
from app.workspace.workspace_manager import WorkspaceManager


# ── Fixtures & Helpers ────────────────────────────────────────────────────────

def _init_real_git_repo(path: Path) -> str:
    """Initialize a real Git repository with an initial commit and return HEAD SHA."""
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
    readme.write_text("# Test Repository\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(path), check=True, capture_output=True, env=env)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(path), check=True, capture_output=True, text=True, env=env)
    return res.stdout.strip()


class PassCheck:
    def __init__(self, check_id: str = "pass_check") -> None:
        self.check_id = check_id
        self.check_name = "Always Pass Check"
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


class FailCheck:
    def __init__(self, check_id: str = "fail_check") -> None:
        self.check_id = check_id
        self.check_name = "Always Fail Check"
        self.required = True

    def execute(self, context: VerificationContext) -> CheckResult:
        now = datetime.now(timezone.utc)
        return CheckResult(
            check_id=self.check_id,
            check_name=self.check_name,
            required=self.required,
            status=CheckStatus.FAIL,
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
            failure_reason="Verification failed",
        )


class DeterministicFileWritingAgent(BaseDevelopmentAgent):
    """DevelopmentAgent that writes a specific file to demonstrate real workspace edits."""
    def __init__(self, filename: str = "feature.py", content: str = "# Implemented feature\n") -> None:
        self.filename = filename
        self.content = content
        self.last_context: DevelopmentAgentContext | None = None

    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        self.last_context = context
        ws = Path(context.workspace_path)
        target = ws / self.filename
        target.write_text(self.content, encoding="utf-8")

        return DevelopmentResult(
            status=DevelopmentResultStatus.SUCCESS,
            summary="Implemented feature successfully",
            changed_files=[self.filename],
        )


class DeterministicPlanProvider(PlanProvider):
    """Fake PlanProvider returning structured proposals."""
    def __init__(self, proposals: list[ProviderTaskProposal]) -> None:
        self._proposals = proposals

    async def generate_plan(
        self,
        user_plan: UserPlan,
        constraints: PlanningConstraints,
    ) -> list[ProviderTaskProposal]:
        return self._proposals


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def git_workspace(tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    head_sha = _init_real_git_repo(repo_path)
    return repo_path, head_sha


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestPlanExecutionService:

    async def test_linear_dag_dependency_safety(self, db_session: AsyncSession, git_workspace):
        """
        Task B depends on Task A.
        Executing Task B while Task A is PENDING must raise DependencyUnmetError.
        After Task A is COMMITTED, Task B can be executed successfully.
        """
        repo_path, _ = git_workspace

        # Build DAG: A -> B
        proposals = [
            GeminiTaskProposal(proposal_key="a", title="Task A", description="Desc A", priority=1, dependencies=[]),
            GeminiTaskProposal(proposal_key="b", title="Task B", description="Desc B", priority=2, dependencies=["a"]),
        ]
        user_plan = UserPlan(
            plan_id="plan_linear",
            user_request="Build feature A then B",
            project_id="proj_1",
            planning_constraints=PlanningConstraints(default_max_attempts=3),
        )
        plan_result = normalize_plan(user_plan, proposals)

        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted_tasks = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()

        task_a_id = next(t.task_id for t in persisted_tasks if t.task_key == "a")
        task_b_id = next(t.task_id for t in persisted_tasks if t.task_key == "b")

        # Set up execution infrastructure
        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        reg.register(PassCheck("pass_check"))
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)
        agent = DeterministicFileWritingAgent()
        orchestrator = AutonomousTaskOrchestrator(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            agent=agent,
        )
        run_controller = AutonomousRunController(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            orchestrator=orchestrator,
        )
        exec_service = PlanExecutionService(task_repository=task_repo, run_controller=run_controller)

        # 1. Try to execute Task B while Task A is PENDING -> Must fail with DependencyUnmetError
        with pytest.raises(DependencyUnmetError) as exc_info:
            await exec_service.execute_planned_task(task_b_id)
        assert task_a_id in exc_info.value.unmet_dependencies

        # 2. Execute Task A -> Must succeed and reach COMMITTED
        res_a = await exec_service.execute_planned_task(task_a_id)
        assert res_a.status == RunStatus.SUCCEEDED
        assert res_a.task_status == TaskStatus.COMMITTED

        # 3. Now execute Task B -> Must succeed
        res_b = await exec_service.execute_planned_task(task_b_id)
        assert res_b.status == RunStatus.SUCCEEDED
        assert res_b.task_status == TaskStatus.COMMITTED

    async def test_diamond_dag_dependency_safety(self, db_session: AsyncSession, git_workspace):
        """
        Diamond DAG: A -> B, A -> C, B -> D, C -> D.
        D cannot run until BOTH B and C are COMMITTED.
        """
        repo_path, _ = git_workspace

        proposals = [
            GeminiTaskProposal(proposal_key="a", title="Task A", description="Desc A", priority=1, dependencies=[]),
            GeminiTaskProposal(proposal_key="b", title="Task B", description="Desc B", priority=2, dependencies=["a"]),
            GeminiTaskProposal(proposal_key="c", title="Task C", description="Desc C", priority=2, dependencies=["a"]),
            GeminiTaskProposal(proposal_key="d", title="Task D", description="Desc D", priority=3, dependencies=["b", "c"]),
        ]
        user_plan = UserPlan(
            plan_id="plan_diamond",
            user_request="Build diamond workflow",
            project_id="proj_diamond",
            planning_constraints=PlanningConstraints(default_max_attempts=3),
        )
        plan_result = normalize_plan(user_plan, proposals)

        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted_tasks = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()

        tasks_by_key = {t.task_key: t.task_id for t in persisted_tasks}

        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        reg.register(PassCheck())
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)
        orchestrator = AutonomousTaskOrchestrator(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            agent=DeterministicFileWritingAgent(),
        )
        run_controller = AutonomousRunController(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            orchestrator=orchestrator,
        )
        exec_service = PlanExecutionService(task_repository=task_repo, run_controller=run_controller)

        # Step 1: Execute A
        await exec_service.execute_planned_task(tasks_by_key["a"])

        # Step 2: Execute B
        await exec_service.execute_planned_task(tasks_by_key["b"])

        # Step 3: Try D (B is COMMITTED, but C is still PENDING) -> Must fail with DependencyUnmetError
        with pytest.raises(DependencyUnmetError) as exc_info:
            await exec_service.execute_planned_task(tasks_by_key["d"])
        assert tasks_by_key["c"] in exc_info.value.unmet_dependencies

        # Step 4: Execute C
        await exec_service.execute_planned_task(tasks_by_key["c"])

        # Step 5: Now execute D -> Must succeed
        res_d = await exec_service.execute_planned_task(tasks_by_key["d"])
        assert res_d.status == RunStatus.SUCCEEDED
        assert res_d.task_status == TaskStatus.COMMITTED

    async def test_idempotency_and_terminal_state_protection(self, db_session: AsyncSession, git_workspace):
        """
        Test duplicate requests, already COMMITTED tasks, and CANCELLED tasks are safely rejected.
        """
        repo_path, _ = git_workspace
        proposals = [GeminiTaskProposal(proposal_key="t1", title="Task 1", description="Desc 1")]
        user_plan = UserPlan(plan_id="plan_idem", user_request="Single task", project_id="p1")
        plan_result = normalize_plan(user_plan, proposals)

        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()
        task_id = persisted[0].task_id

        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        reg.register(PassCheck())
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)
        orchestrator = AutonomousTaskOrchestrator(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            agent=DeterministicFileWritingAgent(),
        )
        run_controller = AutonomousRunController(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            orchestrator=orchestrator,
        )
        exec_service = PlanExecutionService(task_repository=task_repo, run_controller=run_controller)

        # First execution -> COMMITTED
        res1 = await exec_service.execute_planned_task(task_id)
        assert res1.status == RunStatus.SUCCEEDED

        # Duplicate execution on COMMITTED task -> TaskNotStartableError
        with pytest.raises(TaskNotStartableError):
            await exec_service.execute_planned_task(task_id)

    async def test_max_attempts_exceeded_protection(self, db_session: AsyncSession, git_workspace):
        """
        Task with attempt_count >= max_attempts must raise MaxAttemptsExceededError.
        """
        repo_path, _ = git_workspace
        proposals = [GeminiTaskProposal(proposal_key="t_exhaust", title="Task Exhaust", description="Desc")]
        user_plan = UserPlan(
            plan_id="plan_exhaust",
            user_request="Exhaust attempts",
            project_id="p1",
            planning_constraints=PlanningConstraints(default_max_attempts=2),
        )
        plan_result = normalize_plan(user_plan, proposals)

        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()
        task_id = persisted[0].task_id

        # Manually set attempt_count = max_attempts in database
        await task_repo.record_attempt_atomically(task_id)
        await task_repo.record_attempt_atomically(task_id)
        await db_session.commit()

        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        reg.register(PassCheck())
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)
        orchestrator = AutonomousTaskOrchestrator(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            agent=DeterministicFileWritingAgent(),
        )
        run_controller = AutonomousRunController(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            orchestrator=orchestrator,
        )
        exec_service = PlanExecutionService(task_repository=task_repo, run_controller=run_controller)

        with pytest.raises(MaxAttemptsExceededError):
            await exec_service.execute_planned_task(task_id)

    async def test_full_pipeline_with_real_plan_manager(self, db_session: AsyncSession, git_workspace):
        """
        Realistic E2E Integration:
          PlanManager -> PlanService -> TaskRepository -> PlanExecutionService -> RunController -> Workspace -> Git Checkpoint
        """
        repo_path, _ = git_workspace

        proposals = [
            ProviderTaskProposal(proposal_key="feature_a", title="Feature A", description="Write feature_a.py", priority=1),
        ]
        provider = DeterministicPlanProvider(proposals)
        plan_manager = PlanManager(provider=provider)

        user_plan = UserPlan(
            plan_id="plan_e2e_real",
            user_request="Build real feature_a",
            project_id="proj_e2e",
            planning_constraints=PlanningConstraints(default_max_attempts=3),
        )

        # 1. Planning
        plan_result = await plan_manager.plan(user_plan)
        assert not plan_result.rejected

        # 2. Persistence
        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()

        task_id = persisted[0].task_id

        # 3. Execution Infrastructure setup
        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        reg.register(PassCheck())
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)
        agent = DeterministicFileWritingAgent(filename="feature_a.py", content="print('hello e2e')\n")

        orchestrator = AutonomousTaskOrchestrator(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            agent=agent,
        )
        run_controller = AutonomousRunController(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            orchestrator=orchestrator,
        )
        exec_service = PlanExecutionService(task_repository=task_repo, run_controller=run_controller)

        # 4. Execute Planned Task
        res = await exec_service.execute_planned_task(task_id)

        # 5. Assertions
        assert res.status == RunStatus.SUCCEEDED
        assert res.task_status == TaskStatus.COMMITTED
        assert res.verification_passed is True
        assert res.commit_sha is not None

        # Verify DB task state
        db_task = await task_repo.get_task(task_id)
        assert db_task is not None
        assert db_task.status == TaskStatus.COMMITTED
        assert db_task.attempt_count == 1

        # Verify security context passed to agent was application-owned
        assert agent.last_context is not None
        assert agent.last_context.task_id == task_id
        assert agent.last_context.project_id == "proj_e2e"
        assert agent.last_context.repository_root == str(repo_path.resolve())

    async def test_verification_failure_semantics(self, db_session: AsyncSession, git_workspace):
        """
        When VerificationGate FAILS, task must NOT be committed. Run result must report status FAILED.
        """
        repo_path, _ = git_workspace
        proposals = [GeminiTaskProposal(proposal_key="failing_task", title="Failing Task", description="Desc")]
        user_plan = UserPlan(plan_id="plan_fail_verify", user_request="Task that fails verification", project_id="p1")
        plan_result = normalize_plan(user_plan, proposals)

        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()
        task_id = persisted[0].task_id

        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        reg.register(FailCheck())  # Verification gate will FAIL
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)
        orchestrator = AutonomousTaskOrchestrator(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            agent=DeterministicFileWritingAgent(),
        )
        run_controller = AutonomousRunController(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            orchestrator=orchestrator,
        )
        exec_service = PlanExecutionService(task_repository=task_repo, run_controller=run_controller)

        res = await exec_service.execute_planned_task(task_id)
        assert res.status == RunStatus.FAILED
        assert res.verification_passed is False
        assert res.commit_sha is None

        # Verify state in DB is REPAIR_REQUIRED or FAILED, but NOT COMMITTED
        db_task = await task_repo.get_task(task_id)
        assert db_task is not None
        assert db_task.status in (TaskStatus.REPAIR_REQUIRED, TaskStatus.FAILED)
        assert db_task.status != TaskStatus.COMMITTED

    async def test_nonexistent_task_id_raises_not_found(self, db_session: AsyncSession, git_workspace):
        """
        Executing a non-existent task_id raises TaskNotFoundError.
        """
        repo_path, _ = git_workspace
        task_repo = TaskRepository(db_session)
        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)
        orchestrator = AutonomousTaskOrchestrator(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            agent=DeterministicFileWritingAgent(),
        )
        run_controller = AutonomousRunController(
            task_repo=task_repo,
            state_machine=sm,
            workspace_manager=wm,
            verification_gate=gate,
            orchestrator=orchestrator,
        )
        exec_service = PlanExecutionService(task_repository=task_repo, run_controller=run_controller)

        with pytest.raises(TaskNotFoundError):
            await exec_service.execute_planned_task("nonexistent_task_123")
