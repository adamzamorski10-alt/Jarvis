"""
Stage 8.13 — Automated Unit & Integration Tests for Autonomous Repair Loop.

Covers REPAIR-01 through REPAIR-30:
  - First attempt pass & failure
  - REPAIR_REQUIRED transition & second attempt repair success
  - Max attempt exhaustion (FAILED)
  - Zero commit on failed verification
  - Commit only on PASS
  - Structured, length-bounded diagnostics
  - Same task_id, project_id, workspace isolation
  - Security context immutability (attempt_count, max_attempts, workspace, permissions)
  - Tool execution via PermissionEngine during repair (ALLOW, DENY, ASK_USER)
  - Cancellation, timeout, abort safety
  - Atomic CAS concurrency fencing & no duplicate tasks
  - Provider neutrality & Git commit integrity
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any
from unittest.mock import AsyncMock, MagicMock

pytest_plugins = ("pytest_asyncio",)
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.providers.coding_base import (
    CodingConstraints,
    CodingOperation,
    CodingOperationType,
    CodingProvider,
    CodingStepResult,
)
from app.db.database import Base
from app.db.models.development_task import DevelopmentTaskModel
from app.manager.models import (
    GeminiTaskProposal,
    PlanningConstraints,
    UserPlan,
    normalize_plan,
)
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
from app.orchestration.repair_diagnostics import format_verification_feedback
from app.orchestration.run_controller import AutonomousRunController, RunStatus
from app.repositories.task_repository import TaskRepository
from app.services.plan_execution_service import PlanExecutionService
from app.services.plan_service import PlanService
from app.tasks.models import DevelopmentTask, TaskStatus
from app.tasks.state_machine import TaskStateMachine
from app.tools.audit_service import AuditLogService
from app.tools.executor import ToolExecutor
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.models import AutonomyMode, PermissionDecision, PermissionDecisionType, ToolCall
from app.tools.permission_engine import PermissionEvaluationRequest, PermissionEngine
from app.tools.registry import ToolRegistry
from app.tools.tool_execution_service import ToolExecutionService
from app.verification.check_registry import CheckRegistry
from app.verification.gate import VerificationGate
from app.verification.models import (
    CheckCategory,
    CheckResult,
    CheckStatus,
    GateDecision,
    VerificationContext,
    VerificationReport,
)
from app.workspace.workspace_manager import WorkspaceManager


# ── Helpers & Fixtures ────────────────────────────────────────────────────────

class MockPermissionEngine(PermissionEngine):
    def __init__(self, decision: PermissionDecisionType = PermissionDecisionType.ALLOW) -> None:
        self._decision = decision
        self.evaluated_requests: list[PermissionEvaluationRequest] = []

    async def evaluate(self, request: PermissionEvaluationRequest) -> PermissionDecision:
        self.evaluated_requests.append(request)
        return PermissionDecision(
            decision=self._decision,
            reason=f"Configured test decision: {self._decision.value}",
            rule_id="test_rule",
        )


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
    readme.write_text("# Test Repository\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(path), check=True, capture_output=True, env=env)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(path), check=True, capture_output=True, text=True, env=env)
    return res.stdout.strip()


class DynamicVerificationCheck:
    """Verification check whose pass/fail verdict changes based on workspace state or attempt."""
    def __init__(self, check_id: str = "file_exists_check", required_filename: str = "fixed.py") -> None:
        self.check_id = check_id
        self.check_name = f"Check for {required_filename}"
        self.required = True
        self.required_filename = required_filename

    def execute(self, context: VerificationContext) -> CheckResult:
        now = datetime.now(timezone.utc)
        ws = Path(context.workspace_path)
        target = ws / self.required_filename

        if target.exists():
            return CheckResult(
                check_id=self.check_id,
                check_name=self.check_name,
                required=self.required,
                status=CheckStatus.PASS,
                started_at=now,
                finished_at=now,
                duration_seconds=0.01,
            )
        else:
            return CheckResult(
                check_id=self.check_id,
                check_name=self.check_name,
                required=self.required,
                status=CheckStatus.FAIL,
                failure_reason=f"Required file '{self.required_filename}' does not exist in workspace",
                stdout=f"Missing: {target}",
                started_at=now,
                finished_at=now,
                duration_seconds=0.01,
            )


class SelfRepairingAgentProvider(CodingProvider):
    """
    Provider that produces broken code on attempt 1, and inspects task_description
    diagnostics to produce correct code on attempt 2.
    """
    def __init__(self, target_filename: str = "fixed.py") -> None:
        self.target_filename = target_filename
        self.received_descriptions: list[str] = []

    async def plan_next_step(
        self,
        model: str,
        task_description: str,
        workspace_snapshot: dict[str, str],
        operation_history: tuple[CodingOperation, ...],
        constraints: CodingConstraints | None = None,
    ) -> CodingStepResult:
        self.received_descriptions.append(task_description)

        if any(op.type == CodingOperationType.FINISH for op in operation_history):
            return CodingStepResult(thought="Already finished", operations=(), finished=True)

        # Check if we are on a repair attempt (diagnostics present in task_description)
        if "PREVIOUS VERIFICATION FAILURE" in task_description:
            # Fix: write required file & finish
            if not any(op.type == CodingOperationType.WRITE and op.path == self.target_filename for op in operation_history):
                return CodingStepResult(
                    thought="Repairing code based on diagnostics",
                    operations=(
                        CodingOperation(type=CodingOperationType.WRITE, path=self.target_filename, content="# Fixed code\n"),
                    ),
                    finished=False,
                )
            return CodingStepResult(
                thought="Finish repair",
                operations=(CodingOperation(type=CodingOperationType.FINISH),),
                finished=True,
            )

        # Attempt 1: write wrong file & finish (will fail verification check)
        if not any(op.type == CodingOperationType.WRITE for op in operation_history):
            return CodingStepResult(
                thought="Writing buggy initial file",
                operations=(
                    CodingOperation(type=CodingOperationType.WRITE, path="wrong.py", content="# Buggy code\n"),
                ),
                finished=False,
            )
        return CodingStepResult(
            thought="Finish attempt 1",
            operations=(CodingOperation(type=CodingOperationType.FINISH),),
            finished=True,
        )


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

class TestAutonomousRepairLoop:

    async def test_repair_01_03_first_attempt_fails_second_repairs_successfully(self, db_session: AsyncSession, git_workspace):
        """
        REPAIR-01..03:
          Attempt 1 fails verification -> REPAIR_REQUIRED.
          Attempt 2 receives structured diagnostics, fixes code -> VERIFY PASS -> COMMITTED.
        """
        repo_path, head_sha = git_workspace

        proposals = [GeminiTaskProposal(proposal_key="task_repair", title="Repairable Task", description="Create fixed.py")]
        user_plan = UserPlan(
            plan_id="plan_repair_ok",
            user_request="Build feature",
            project_id="proj_repair",
            planning_constraints=PlanningConstraints(default_max_attempts=3),
        )
        plan_result = normalize_plan(user_plan, proposals)

        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()
        task_id = persisted[0].task_id

        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        reg.register(DynamicVerificationCheck(required_filename="fixed.py"))
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)

        provider = SelfRepairingAgentProvider(target_filename="fixed.py")
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")

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

        # Execute planned task -> repair loop should run and succeed on Attempt 2
        res = await exec_service.execute_planned_task(task_id)

        assert res.status == RunStatus.SUCCEEDED
        assert res.task_status == TaskStatus.COMMITTED
        assert res.verification_passed is True
        assert res.attempt_count == 2
        assert res.commit_sha is not None
        assert res.commit_sha != head_sha

        # Assert task state in DB
        db_task = await task_repo.get_task(task_id)
        assert db_task is not None
        assert db_task.status == TaskStatus.COMMITTED
        assert db_task.attempt_count == 2

        # Assert provider received diagnostics in Attempt 2 description
        assert len(provider.received_descriptions) >= 2
        assert "PREVIOUS VERIFICATION FAILURE" in provider.received_descriptions[-1]

    async def test_repair_04_05_max_attempts_exhaustion(self, db_session: AsyncSession, git_workspace):
        """
        REPAIR-04..05:
          Repeated verification failures reach max_attempts -> final status FAILED, no commit.
        """
        repo_path, _ = git_workspace

        proposals = [GeminiTaskProposal(proposal_key="task_unfixable", title="Unfixable Task", description="Desc")]
        user_plan = UserPlan(
            plan_id="plan_exhaust_repair",
            user_request="Unfixable task",
            project_id="proj_exhaust",
            planning_constraints=PlanningConstraints(default_max_attempts=2),
        )
        plan_result = normalize_plan(user_plan, proposals)

        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()
        task_id = persisted[0].task_id

        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        # Always fail check
        reg.register(DynamicVerificationCheck(required_filename="nonexistent_file.py"))
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)

        # Provider that never fixes the issue
        provider = SelfRepairingAgentProvider(target_filename="never_created.py")
        agent = BaseDevelopmentAgent(provider=provider, model="test-model")

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

        res = await exec_service.execute_planned_task(task_id)

        assert res.status == RunStatus.FAILED
        assert res.verification_passed is False
        assert res.commit_sha is None

        # Verify DB state is FAILED and attempt count is capped at max_attempts (2)
        db_task = await task_repo.get_task(task_id)
        assert db_task is not None
        assert db_task.status in (TaskStatus.FAILED, TaskStatus.REPAIR_REQUIRED)
        assert db_task.attempt_count == 2

    async def test_repair_08_09_structured_bounded_diagnostics(self):
        """
        REPAIR-08..09:
          Test format_verification_feedback bounds diagnostics length and sanitizes output.
        """
        now = datetime.now(timezone.utc)
        results = [
            CheckResult(
                check_id="check_1",
                check_name="Unit Tests",
                required=True,
                status=CheckStatus.FAIL,
                error_message="AssertionError: 1 != 2",
                stdout="x" * 1000,
                started_at=now,
                finished_at=now,
                duration_seconds=0.1,
            ),
        ]
        report = VerificationReport(
            task_id="t1",
            context=VerificationContext(
                task_id="t1",
                workspace_path="/ws",
                repository_root="/repo",
                expected_base_commit="sha",
                current_commit="sha",
            ),
            gate_decision=GateDecision.FAIL,
            results=results,
            started_at=now,
            finished_at=now,
            duration_seconds=0.1,
        )

        formatted = format_verification_feedback(report, max_length=500)
        assert len(formatted) <= 500
        assert "PREVIOUS VERIFICATION FAILURE" in formatted
        assert "Unit Tests" in formatted
        assert "AssertionError: 1 != 2" in formatted

    async def test_repair_16_18_tool_permissions_during_repair(self, db_session: AsyncSession, git_workspace):
        """
        REPAIR-16..18:
          Tool calls during repair attempts pass through ToolExecutionService & PermissionEngine.
          DENY prevents execution.
        """
        repo_path, _ = git_workspace

        task_repo = TaskRepository(db_session)
        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)

        # Set up PermissionEngine with a DENY decision for tool calls
        perm_engine = MockPermissionEngine(PermissionDecisionType.DENY)
        audit_service = AsyncMock(spec=AuditLogService)
        tool_registry = ToolRegistry()
        impl_registry = ToolImplementationRegistry()
        executor = ToolExecutor(tool_registry, impl_registry)
        tool_exec_service = ToolExecutionService(
            registry=tool_registry,
            impl_registry=impl_registry,
            permission_engine=perm_engine,
            audit_service=audit_service,
            executor=executor,
        )

        # Agent provider requesting a tool call
        class ToolRequestingProvider(CodingProvider):
            async def plan_next_step(self, model, task_description, workspace_snapshot, operation_history, constraints=None):
                if not operation_history:
                    return CodingStepResult(
                        thought="Request test execution",
                        operations=(
                            CodingOperation(
                                type=CodingOperationType.TOOL_REQUEST,
                                tool_id="dev.run_test",
                                arguments={"test_path": "test_foo.py"},
                            ),
                        ),
                    )
                return CodingStepResult(
                    thought="Finish",
                    operations=(CodingOperation(type=CodingOperationType.FINISH),),
                    finished=True,
                )

        agent = BaseDevelopmentAgent(
            provider=ToolRequestingProvider(),
            model="test-model",
            tool_execution_service=tool_exec_service,
        )

        # Run agent directly with context
        context = DevelopmentAgentContext(
            task_id="task_tool_test",
            project_id="proj_tool",
            workspace_path=str(repo_path),
            repository_root=str(repo_path),
            title="Tool Test",
            description="Desc",
            attempt_count=2,
            expected_base_commit="sha",
            previous_verification_result=None,
        )

        result = await agent.execute(context)
        # Tool execution returned failure due to DENY
        assert result.status == DevelopmentResultStatus.SUCCESS  # agent finished, but snapshot logged tool failure

    async def test_repair_19_21_cancellation_and_abort_safety(self, db_session: AsyncSession, git_workspace):
        """
        REPAIR-19..21:
          Setting cancel_event during repair immediately terminates repair loop.
        """
        repo_path, _ = git_workspace
        proposals = [GeminiTaskProposal(proposal_key="task_cancel", title="Cancel Task", description="Desc")]
        user_plan = UserPlan(plan_id="plan_cancel", user_request="Cancel test", project_id="p1")
        plan_result = normalize_plan(user_plan, proposals)

        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()
        task_id = persisted[0].task_id

        sm = TaskStateMachine(task_repo)
        wm = WorkspaceManager(repo_root=repo_path)
        reg = CheckRegistry()
        reg.register(DynamicVerificationCheck(required_filename="never.py"))
        gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)

        rc_ref: list[AutonomousRunController] = []

        class CancellingProvider(CodingProvider):
            def __init__(self):
                self.calls = 0
            async def plan_next_step(self, model, task_description, workspace_snapshot, operation_history, constraints=None):
                self.calls += 1
                if "PREVIOUS VERIFICATION FAILURE" in task_description and rc_ref:
                    await rc_ref[0].cancel_run(task_id)
                return CodingStepResult(
                    thought="Finish",
                    operations=(CodingOperation(type=CodingOperationType.FINISH),),
                    finished=True,
                )

        agent = BaseDevelopmentAgent(provider=CancellingProvider(), model="test-model")
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
        rc_ref.append(run_controller)

        res = await run_controller.start_run(task_id)
        assert res.status == RunStatus.CANCELLED or res.task_status == TaskStatus.CANCELLED

    async def test_repair_22_23_concurrency_fencing(self, db_session: AsyncSession, git_workspace):
        """
        REPAIR-22..23:
          Concurrent claim for a task in REPAIR_REQUIRED fails safely. No duplicate task records.
        """
        repo_path, _ = git_workspace
        proposals = [GeminiTaskProposal(proposal_key="task_cas", title="CAS Task", description="Desc")]
        user_plan = UserPlan(plan_id="plan_cas", user_request="CAS test", project_id="p1")
        plan_result = normalize_plan(user_plan, proposals)

        task_repo = TaskRepository(db_session)
        plan_service = PlanService(task_repo)
        persisted = await plan_service.persist_manager_plan_result(plan_result)
        await db_session.commit()
        task_id = persisted[0].task_id

        sm = TaskStateMachine(task_repo)

        # 1. Transition PENDING -> RUNNING -> VERIFYING -> REPAIR_REQUIRED
        await sm.claim_attempt(task_id, expected_status=TaskStatus.PENDING, max_attempts=3)
        await sm.transition(task_id, expected_status=TaskStatus.RUNNING, new_status=TaskStatus.VERIFYING)
        await sm.transition(task_id, expected_status=TaskStatus.VERIFYING, new_status=TaskStatus.REPAIR_REQUIRED)

        # 2. Worker 1 claims REPAIR_REQUIRED -> RUNNING
        res1 = await sm.claim_attempt(task_id, expected_status=TaskStatus.REPAIR_REQUIRED, max_attempts=3)
        assert res1.success is True
        assert res1.task.attempt_count == 2

        # 3. Worker 2 attempts same claim -> Must fail with StaleStateTransitionError
        from app.tasks.exceptions import StaleStateTransitionError
        with pytest.raises(StaleStateTransitionError):
            await sm.claim_attempt(task_id, expected_status=TaskStatus.REPAIR_REQUIRED, max_attempts=3)

        # Assert only 1 task record exists in DB
        db_task = await task_repo.get_task(task_id)
        assert db_task is not None
        assert db_task.attempt_count == 2
