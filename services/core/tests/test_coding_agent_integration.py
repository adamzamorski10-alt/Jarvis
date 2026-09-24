"""
Stage 8.9 — Coding Agent Integration Architecture & Verification Tests.

Tests end-to-end integration of DevelopmentAgent, ProviderManager,
DevelopmentAgentFactory, AutonomousTaskOrchestrator, WorkspaceManager,
VerificationGate, and Git checkpoints.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any, AsyncGenerator

pytest_plugins = ("pytest_asyncio",)
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.models import AIResponse
from app.ai.providers.base import AIProvider
from app.ai.providers.coding_base import (
    CodingConstraints,
    CodingOperation,
    CodingOperationType,
    CodingProvider,
    CodingStepResult,
)
from app.ai.providers.coding_ollama import OllamaCodingAdapter
from app.ai.providers.coding_openrouter import OpenRouterCodingAdapter
from app.ai.services.provider_manager import ProviderManager
from app.db.database import Base
from app.orchestration.agent_factory import (
    DEFAULT_OLLAMA_CODING_MODEL,
    DEFAULT_OPENROUTER_CODING_MODEL,
    DevelopmentAgentFactory,
)
from app.orchestration.agents.base_agent import BaseDevelopmentAgent
from app.orchestration.models import (
    DevelopmentAgentContext,
    DevelopmentResult,
    DevelopmentResultStatus,
    OrchestrationOptions,
)
from app.orchestration.orchestrator import AutonomousTaskOrchestrator
from app.repositories.task_repository import TaskRepository
from app.services.settings_service import SettingsService
from app.tasks.models import DevelopmentTask, TaskStatus
from app.tasks.state_machine import TaskStateMachine
from app.verification.check_registry import CheckRegistry
from app.verification.gate import VerificationGate
from app.verification.models import CheckResult, CheckStatus, VerificationContext
from app.workspace.workspace_manager import WorkspaceManager


# ── Helpers & Fixtures ────────────────────────────────────────────────────────

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


class FakeCheck:
    """Configurable verification check fixture."""
    def __init__(self, check_id: str, status: CheckStatus = CheckStatus.PASS) -> None:
        self.check_id = check_id
        self.check_name = f"Check {check_id}"
        self.required = True
        self._status = status

    def execute(self, context: VerificationContext) -> CheckResult:
        now = datetime.now(timezone.utc)
        return CheckResult(
            check_id=self.check_id,
            check_name=self.check_name,
            required=self.required,
            status=self._status,
            started_at=now,
            finished_at=now,
            duration_seconds=0.01,
        )


class MockAIProvider(AIProvider):
    """Fake AIProvider returning pre-configured responses."""
    name = "mock-provider"

    def __init__(self, step_result: CodingStepResult | None = None) -> None:
        self.step_result = step_result or CodingStepResult(
            operations=(CodingOperation(type=CodingOperationType.FINISH, path=""),),
            finished=True,
            thought="done",
        )

    async def chat(self, model: str, messages: list[dict[str, str]], tools: list[dict] | None = None) -> AIResponse:
        # Construct valid tool call matching coding.propose_step schema
        ops_data = []
        for op in self.step_result.operations:
            item = {"type": op.type.value}
            if op.path:
                item["path"] = op.path
            if op.content is not None:
                item["content"] = op.content
            if op.old_string is not None:
                item["old_string"] = op.old_string
            if op.new_string is not None:
                item["new_string"] = op.new_string
            ops_data.append(item)

        tool_args = {
            "thought": self.step_result.thought,
            "finished": self.step_result.finished,
            "operations": ops_data,
        }

        from app.ai.models import ToolCall
        return AIResponse(
            content="",
            tool_calls=[ToolCall(tool_id="coding.propose_step", arguments=tool_args)],
        )

    async def health_check(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return ["mock-model"]


class FakeSettingsService(SettingsService):
    """In-memory SettingsService double."""
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._store = initial or {}

    async def get_all(self) -> dict[str, str]:
        return dict(self._store)

    async def get_one(self, key: str) -> str | None:
        return self._store.get(key)

    async def update(self, settings: dict[str, str | None]) -> dict[str, str]:
        for k, v in settings.items():
            if v is None:
                self._store.pop(k, None)
            else:
                self._store[k] = v
        return dict(self._store)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ── DevelopmentAgentFactory Tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_factory_creates_ollama_agent_by_default():
    settings = FakeSettingsService({"ai_provider": "ollama"})
    pm = ProviderManager(settings)
    factory = DevelopmentAgentFactory(pm)

    agent = await factory.create_agent()
    assert isinstance(agent, BaseDevelopmentAgent)
    assert isinstance(agent._provider, OllamaCodingAdapter)
    assert agent._model == DEFAULT_OLLAMA_CODING_MODEL


@pytest.mark.asyncio
async def test_factory_creates_openrouter_agent_when_configured():
    settings = FakeSettingsService({"ai_provider": "openrouter"})
    pm = ProviderManager(settings)
    factory = DevelopmentAgentFactory(pm)

    agent = await factory.create_agent()
    assert isinstance(agent, BaseDevelopmentAgent)
    assert isinstance(agent._provider, OpenRouterCodingAdapter)
    assert agent._model == DEFAULT_OPENROUTER_CODING_MODEL


@pytest.mark.asyncio
async def test_factory_uses_explicit_selected_model():
    settings = FakeSettingsService({
        "ai_provider": "ollama",
        "ai_selected_model": "custom-coder:32b",
    })
    pm = ProviderManager(settings)
    factory = DevelopmentAgentFactory(pm)

    agent = await factory.create_agent()
    assert agent._model == "custom-coder:32b"


@pytest.mark.asyncio
async def test_factory_kilo_auto_approve_true_propagates():
    settings = FakeSettingsService({
        "ai_provider": "kilo",
        "kilo_executable_path": "C:\\kilo.exe",
        "ai_selected_model": "google/gemini-flash-latest",
        "kilo_auto_approve": "true",
    })
    pm = ProviderManager(settings)
    factory = DevelopmentAgentFactory(pm)

    agent = await factory.create_agent()
    from app.orchestration.agents.kilo_agent import KiloDevelopmentAgent
    assert isinstance(agent, KiloDevelopmentAgent)
    assert agent._auto_approve is True


@pytest.mark.asyncio
async def test_factory_kilo_auto_approve_false_propagates():
    settings = FakeSettingsService({
        "ai_provider": "kilo",
        "kilo_executable_path": "C:\\kilo.exe",
        "ai_selected_model": "google/gemini-flash-latest",
        "kilo_auto_approve": "false",
    })
    pm = ProviderManager(settings)
    factory = DevelopmentAgentFactory(pm)

    agent = await factory.create_agent()
    from app.orchestration.agents.kilo_agent import KiloDevelopmentAgent
    assert isinstance(agent, KiloDevelopmentAgent)
    assert agent._auto_approve is False


@pytest.mark.asyncio
async def test_factory_kilo_auto_approve_missing_defaults_false():
    settings = FakeSettingsService({
        "ai_provider": "kilo",
        "kilo_executable_path": "C:\\kilo.exe",
        "ai_selected_model": "google/gemini-flash-latest",
    })
    pm = ProviderManager(settings)
    factory = DevelopmentAgentFactory(pm)

    agent = await factory.create_agent()
    from app.orchestration.agents.kilo_agent import KiloDevelopmentAgent
    assert isinstance(agent, KiloDevelopmentAgent)
    assert agent._auto_approve is False


@pytest.mark.asyncio
async def test_factory_kilo_auto_approve_invalid_value_defaults_false():
    settings = FakeSettingsService({
        "ai_provider": "kilo",
        "kilo_executable_path": "C:\\kilo.exe",
        "ai_selected_model": "google/gemini-flash-latest",
        "kilo_auto_approve": "not_a_bool",
    })
    pm = ProviderManager(settings)
    factory = DevelopmentAgentFactory(pm)

    agent = await factory.create_agent()
    from app.orchestration.agents.kilo_agent import KiloDevelopmentAgent
    assert isinstance(agent, KiloDevelopmentAgent)
    assert agent._auto_approve is False


@pytest.mark.asyncio
async def test_provider_manager_snapshot_includes_kilo_auto_approve():
    settings = FakeSettingsService({
        "ai_provider": "kilo",
        "kilo_executable_path": "C:\\kilo.exe",
        "ai_selected_model": "google/gemini-flash-latest",
        "kilo_auto_approve": "true",
    })
    pm = ProviderManager(settings)
    config = await pm.get_config()
    assert "kilo_auto_approve" in config
    assert config["kilo_auto_approve"] == "true"


@pytest.mark.asyncio
async def test_provider_manager_snapshot_kilo_auto_approve_not_secret():
    settings = FakeSettingsService({
        "ai_provider": "kilo",
        "kilo_executable_path": "C:\\kilo.exe",
        "ai_selected_model": "google/gemini-flash-latest",
        "kilo_auto_approve": "true",
    })
    pm = ProviderManager(settings)
    config = await pm.get_config()
    assert "kilo_auto_approve" in config
    value = config["kilo_auto_approve"]
    assert isinstance(value, str)
    assert value in ("true", "false", "1", "0", "yes", "no")


# ── End-to-End Orchestrator & Factory Integration Tests ───────────────────────

@pytest.mark.asyncio
async def test_e2e_successful_coding_verification_and_commit(tmp_path: Path, db_session: AsyncSession):
    """
    End-to-End Test:
    Task claim → AgentFactory creates agent → CodingProvider writes code →
    VerificationGate PASS → WorkspaceManager Git Checkpoint → COMMITTED.
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

    # Setup ProviderManager with mock AIProvider that writes app.py and finishes
    mock_ai = MockAIProvider(step_result=CodingStepResult(
        operations=(
            CodingOperation(type=CodingOperationType.WRITE, path="app.py", content="print('hello integration')\n"),
            CodingOperation(type=CodingOperationType.FINISH, path=""),
        ),
        finished=True,
        thought="Writing integration code",
    ))

    settings = FakeSettingsService({"ai_provider": "ollama"})
    pm = ProviderManager(settings)
    async def mock_get_coding_provider():
        return OllamaCodingAdapter(mock_ai)

    pm.get_coding_provider = mock_get_coding_provider

    factory = DevelopmentAgentFactory(pm)

    orchestrator = AutonomousTaskOrchestrator(
        task_repo=repo,
        state_machine=sm,
        workspace_manager=wm,
        verification_gate=gate,
        agent_factory=factory,
    )

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_integration",
        title="Add app.py feature",
        description="Write app.py",
    ))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.final_status == TaskStatus.COMMITTED
    assert res.committed is True
    assert res.checkpoint_sha is not None
    assert len(res.checkpoint_sha) == 40
    assert res.verification_report is not None
    assert res.verification_report.passed is True

    # Verify task state in database
    db_task = await repo.get_task(task.task_id)
    assert db_task is not None
    assert db_task.status == TaskStatus.COMMITTED


@pytest.mark.asyncio
async def test_e2e_verification_failure_prevents_commit(tmp_path: Path, db_session: AsyncSession):
    """
    End-to-End Test:
    Agent produces code edits, but VerificationGate fails.
    Verifies NO commit is created and task state transitions to REPAIR_REQUIRED.
    """
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")

    registry = CheckRegistry()
    registry.register(FakeCheck("check_failing", status=CheckStatus.FAIL))
    gate = VerificationGate(registry=registry, primary_worktree_path=repo_root)

    mock_ai = MockAIProvider(step_result=CodingStepResult(
        operations=(
            CodingOperation(type=CodingOperationType.WRITE, path="broken.py", content="syntax error\n"),
            CodingOperation(type=CodingOperationType.FINISH, path=""),
        ),
        finished=True,
    ))

    settings = FakeSettingsService({"ai_provider": "ollama"})
    pm = ProviderManager(settings)
    async def mock_get_coding_provider_failing():
        return OllamaCodingAdapter(mock_ai)

    pm.get_coding_provider = mock_get_coding_provider_failing

    factory = DevelopmentAgentFactory(pm)
    orchestrator = AutonomousTaskOrchestrator(
        task_repo=repo,
        state_machine=sm,
        workspace_manager=wm,
        verification_gate=gate,
        agent_factory=factory,
    )

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_fail_ver",
        title="Failing Verification",
        description="Fails check",
        max_attempts=3,
    ))
    await db_session.commit()

    res = await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert res.final_status == TaskStatus.REPAIR_REQUIRED
    assert res.committed is False
    assert res.checkpoint_sha is None
    assert res.verification_report is not None
    assert res.verification_report.passed is False

    db_task = await repo.get_task(task.task_id)
    assert db_task.status == TaskStatus.REPAIR_REQUIRED


@pytest.mark.asyncio
async def test_security_context_isolation(tmp_path: Path, db_session: AsyncSession):
    """
    Security Audit Test:
    Ensures DevelopmentAgentContext passed to agent contains zero
    permission, secret, tool executor, or credential handles.
    """
    repo_root = tmp_path / "primary_repo"
    repo_root.mkdir()
    base_sha = _init_real_git_repo(repo_root)

    repo = TaskRepository(db_session)
    sm = TaskStateMachine(repo)
    wm = WorkspaceManager(repo_root=repo_root, worktree_base_dir=tmp_path / "worktrees")
    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=repo_root)

    captured_contexts: list[DevelopmentAgentContext] = []

    class InspectingAgent(BaseDevelopmentAgent):
        async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
            captured_contexts.append(context)
            return DevelopmentResult(status=DevelopmentResultStatus.SUCCESS)

    orchestrator = AutonomousTaskOrchestrator(
        task_repo=repo,
        state_machine=sm,
        workspace_manager=wm,
        verification_gate=gate,
        agent=InspectingAgent(provider=MockAIProvider(), model="test"),
    )

    task = await repo.create_task(DevelopmentTask(
        project_id="proj_sec_audit",
        title="Security Audit Task",
        description="Verify context fields",
    ))
    await db_session.commit()

    await orchestrator.start_task(task.task_id, OrchestrationOptions(base_commit_sha=base_sha))

    assert len(captured_contexts) == 1
    ctx = captured_contexts[0]

    # Verify forbidden terms are absent from field names
    forbidden_terms = ["permission", "grant", "tool", "executor", "secret", "token", "password", "credential"]
    for field_name in DevelopmentAgentContext.model_fields.keys():
        for term in forbidden_terms:
            assert term not in field_name.lower(), f"Forbidden term '{term}' found in DevelopmentAgentContext field '{field_name}'"


@pytest.mark.asyncio
async def test_unsupported_provider_fails_closed():
    """
    Hostile Audit Test:
    Unsupported provider names (e.g. 'kimi', 'omniroute', 'unknown') MUST fail closed
    by raising AIConfigError, preventing accidental fallback or unauthorized provider execution.
    """
    from app.ai.services.provider_manager import AIConfigError

    for provider_name in ["kimi", "omniroute", "unsupported_xyz"]:
        settings = FakeSettingsService({"ai_provider": provider_name})
        pm = ProviderManager(settings)
        with pytest.raises(AIConfigError) as exc_info:
            await pm.get_coding_provider()
        assert "Unsupported" in str(exc_info.value) or "provider" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_factory_creates_fresh_agent_per_call():
    """
    Hostile Audit Test:
    DevelopmentAgentFactory MUST instantiate a fresh agent (with fresh WorkspaceWriter
    and clean state) on every call, preventing cross-task state leaks.
    """
    settings = FakeSettingsService({"ai_provider": "ollama"})
    pm = ProviderManager(settings)
    factory = DevelopmentAgentFactory(pm)

    agent1 = await factory.create_agent()
    agent2 = await factory.create_agent()

    assert agent1 is not agent2
    assert agent1._provider is not agent2._provider


@pytest.mark.asyncio
async def test_concurrent_task_isolation(tmp_path: Path):
    """
    Hostile Audit Test:
    Two concurrent DevelopmentAgents executing in separate workspaces must maintain
    strict file isolation and state separation.
    """
    ws1 = tmp_path / "workspace1"
    ws2 = tmp_path / "workspace2"
    ws1.mkdir()
    ws2.mkdir()

    mock_ai1 = MockAIProvider(step_result=CodingStepResult(
        operations=(
            CodingOperation(type=CodingOperationType.WRITE, path="file1.txt", content="content1\n"),
            CodingOperation(type=CodingOperationType.FINISH, path=""),
        ),
        finished=True,
    ))
    mock_ai2 = MockAIProvider(step_result=CodingStepResult(
        operations=(
            CodingOperation(type=CodingOperationType.WRITE, path="file2.txt", content="content2\n"),
            CodingOperation(type=CodingOperationType.FINISH, path=""),
        ),
        finished=True,
    ))

    agent1 = BaseDevelopmentAgent(provider=OllamaCodingAdapter(mock_ai1), model="model1")
    agent2 = BaseDevelopmentAgent(provider=OllamaCodingAdapter(mock_ai2), model="model2")

    ctx1 = DevelopmentAgentContext(
        task_id="task_1",
        project_id="p1",
        workspace_path=str(ws1),
        repository_root=str(ws1),
        title="Task 1",
        description="Write file 1",
        attempt_count=1,
        expected_base_commit="sha1",
    )
    ctx2 = DevelopmentAgentContext(
        task_id="task_2",
        project_id="p2",
        workspace_path=str(ws2),
        repository_root=str(ws2),
        title="Task 2",
        description="Write file 2",
        attempt_count=1,
        expected_base_commit="sha2",
    )

    res1, res2 = await asyncio.gather(
        agent1.execute(ctx1),
        agent2.execute(ctx2),
    )

    assert res1.status == DevelopmentResultStatus.SUCCESS
    assert res2.status == DevelopmentResultStatus.SUCCESS

    assert (ws1 / "file1.txt").exists()
    assert not (ws1 / "file2.txt").exists()
    assert (ws2 / "file2.txt").exists()
    assert not (ws2 / "file1.txt").exists()


@pytest.mark.asyncio
async def test_cancellation_during_coding_turns(tmp_path: Path):
    """
    Hostile Audit Test:
    When cancel_event is set prior to/during turn execution, agent MUST respect cancellation
    and return CANCELLED status immediately.
    """
    ws = tmp_path / "workspace_cancel"
    ws.mkdir()

    mock_ai = MockAIProvider(step_result=CodingStepResult(
        operations=(CodingOperation(type=CodingOperationType.WRITE, path="never.txt", content="no"),),
        finished=False,
    ))
    agent = BaseDevelopmentAgent(provider=OllamaCodingAdapter(mock_ai), model="model")

    cancel_event = asyncio.Event()
    cancel_event.set()  # Signal cancellation immediately

    ctx = DevelopmentAgentContext(
        task_id="task_cancelled",
        project_id="p_cancel",
        workspace_path=str(ws),
        repository_root=str(ws),
        title="Cancelled Task",
        description="Should cancel",
        attempt_count=1,
        expected_base_commit="shacancel",
        cancel_event=cancel_event,
    )

    res = await agent.execute(ctx)
    assert res.status == DevelopmentResultStatus.CANCELLED
    assert not (ws / "never.txt").exists()

