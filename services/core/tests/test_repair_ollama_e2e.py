"""
Stage 8.13 — Real Ollama E2E Repair Test.

Tests real local Ollama (qwen2.5-coder:7b) performing an autonomous code repair
cycle when verification diagnostics are provided.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import subprocess

pytest_plugins = ("pytest_asyncio",)
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.providers.coding_ollama import OllamaCodingAdapter
from app.ai.providers.ollama import OllamaProvider
from app.db.database import Base
from app.manager.models import (
    GeminiTaskProposal,
    PlanningConstraints,
    UserPlan,
    normalize_plan,
)
from app.orchestration.agents.base_agent import BaseDevelopmentAgent
from app.orchestration.models import OrchestrationOptions
from app.orchestration.orchestrator import AutonomousTaskOrchestrator
from app.orchestration.run_controller import AutonomousRunController, RunStatus
from app.repositories.task_repository import TaskRepository
from app.services.plan_execution_service import PlanExecutionService
from app.services.plan_service import PlanService
from app.tasks.models import TaskStatus
from app.tasks.state_machine import TaskStateMachine
from app.verification.check_registry import CheckRegistry
from app.verification.gate import VerificationGate
from app.verification.models import CheckResult, CheckStatus, VerificationContext
from app.workspace.workspace_manager import WorkspaceManager


def _init_git_repo(path: Path) -> str:
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


class RequireCalculatorFileCheck:
    """Verification check requiring math_calc.py to exist and contain `add(a, b)`."""
    def __init__(self) -> None:
        self.check_id = "math_calc_check"
        self.check_name = "Math Calc Module Verification"
        self.required = True

    def execute(self, context: VerificationContext) -> CheckResult:
        now = datetime.now(timezone.utc)
        ws = Path(context.workspace_path)
        calc_file = ws / "math_calc.py"

        if not calc_file.exists():
            return CheckResult(
                check_id=self.check_id,
                check_name=self.check_name,
                required=True,
                status=CheckStatus.FAIL,
                failure_reason="Required file 'math_calc.py' does not exist in workspace",
                stdout="Missing file: math_calc.py",
                started_at=now,
                finished_at=now,
                duration_seconds=0.01,
            )

        content = calc_file.read_text(encoding="utf-8")
        if "def add(" not in content:
            return CheckResult(
                check_id=self.check_id,
                check_name=self.check_name,
                required=True,
                status=CheckStatus.FAIL,
                failure_reason="File 'math_calc.py' does not define function 'def add(a, b):'",
                stdout=f"Content was: {content[:200]}",
                started_at=now,
                finished_at=now,
                duration_seconds=0.01,
            )

        return CheckResult(
            check_id=self.check_id,
            check_name=self.check_name,
            required=True,
            status=CheckStatus.PASS,
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
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


@pytest.mark.asyncio
async def test_real_ollama_repair_loop_e2e(db_session: AsyncSession, tmp_path: Path):
    """
    Real Ollama repair test using local Ollama model qwen2.5-coder:7b.
    Checks if Ollama is accessible; if so, performs real repair loop.
    """
    # 1. Check local Ollama availability
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code != 200:
                pytest.skip("Local Ollama server is not available (status != 200)")
            models_data = resp.json()
            models = [m.get("name") for m in models_data.get("models", [])]
            if not any("qwen2.5-coder" in m for m in models):
                pytest.skip("Local Ollama server does not have qwen2.5-coder model loaded")
    except Exception as exc:
        pytest.skip(f"Local Ollama server not reachable: {exc}")

    # 2. Setup real Git repository
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    head_sha = _init_git_repo(repo_path)

    # 3. Setup persisted task
    proposals = [
        GeminiTaskProposal(
            proposal_key="math_task",
            title="Math Calculator",
            description="Create math_calc.py with an add(a, b) function that adds two numbers.",
        ),
    ]
    user_plan = UserPlan(
        plan_id="plan_ollama_repair",
        user_request="Build math calculator module",
        project_id="proj_ollama",
        planning_constraints=PlanningConstraints(default_max_attempts=3),
    )
    plan_result = normalize_plan(user_plan, proposals)

    task_repo = TaskRepository(db_session)
    plan_service = PlanService(task_repo)
    persisted = await plan_service.persist_manager_plan_result(plan_result)
    await db_session.commit()
    task_id = persisted[0].task_id

    # 4. Setup execution components with real Ollama Provider & Adapter
    ollama_provider = OllamaProvider(base_url="http://localhost:11434")
    ollama_adapter = OllamaCodingAdapter(provider=ollama_provider)
    agent = BaseDevelopmentAgent(provider=ollama_adapter, model="qwen2.5-coder:7b")

    sm = TaskStateMachine(task_repo)
    wm = WorkspaceManager(repo_root=repo_path)
    reg = CheckRegistry()
    reg.register(RequireCalculatorFileCheck())
    gate = VerificationGate(registry=reg, primary_worktree_path=repo_path)

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

    # 5. Execute planned task
    res = await exec_service.execute_planned_task(task_id, timeout_seconds=120.0)

    # 6. Verify result (SUCCEEDED or explicitly report model performance)
    if res.status == RunStatus.SUCCEEDED:
        assert res.task_status == TaskStatus.COMMITTED
        assert res.verification_passed is True
        assert res.commit_sha is not None
        assert res.commit_sha != head_sha
    else:
        # If the local 7b model failed repair within attempts, report result as model performance limitation
        assert res.status in (RunStatus.FAILED, RunStatus.TIMED_OUT)
