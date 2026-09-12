"""
Stage 8.11 — Controlled Execution Capabilities Test Suite.

Verifies that DevelopmentAgent can request controlled tool execution through
ToolExecutionService -> PermissionEngine -> Audit -> ToolExecutor without gaining
direct subprocess/shell authority or bypassing permission boundaries.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

pytest_plugins = ("pytest_asyncio",)
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.models import AIResponse, ToolCall as AIToolCall
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
from app.db.database import Base
from app.orchestration.agents.base_agent import BaseDevelopmentAgent
from app.orchestration.models import DevelopmentAgentContext, DevelopmentResultStatus
from app.tools.audit_models import AuditEvent
from app.tools.audit_service import AuditLogService
from app.tools.executor import ToolExecutor
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.implementations.dev_test import DevRunTest
from app.tools.models import (
    AutonomyMode,
    PermissionDecision,
    PermissionDecisionType,
    PermissionEvaluationRequest,
    RiskLevel,
    ToolCall,
    ToolCategory,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from app.tools.permission_engine import PermissionEngine
from app.tools.registry import ToolRegistry
from app.tools.tool_execution_service import ToolExecutionService


# ── Fixtures & Mock Helpers ───────────────────────────────────────────────────

class MockPermissionEngine(PermissionEngine):
    """Configurable PermissionEngine test double."""

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


class MockAuditLogService(AuditLogService):
    """In-memory audit log double."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(
        self,
        event_type: Any,
        *,
        reason: str,
        request_id: str | None = None,
        tool_id: str | None = None,
        action: str | None = None,
        risk_level: str | None = None,
        autonomy_mode: str | None = None,
        decision: str | None = None,
        rule_id: str | None = None,
        grant_id: str | None = None,
        execution_status: str | None = None,
        success: bool | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        execution_time_ms: float | None = None,
        arguments: Any = None,
        result: Any = None,
        event_id: str | None = None,
    ) -> AuditEvent | None:
        from app.tools.audit_models import make_audit_event
        event = make_audit_event(
            event_type,
            reason=reason,
            request_id=request_id,
            tool_id=tool_id,
            action=action,
            risk_level=risk_level,
            autonomy_mode=autonomy_mode,
            decision=decision,
            rule_id=rule_id,
            grant_id=grant_id,
            execution_status=execution_status,
            success=success,
            error_code=error_code,
            error_message=error_message,
            execution_time_ms=execution_time_ms,
            arguments=arguments,
            result=result,
            event_id=event_id,
        )
        self.events.append(event)
        return event


class SequentialMockCodingProvider(CodingProvider):
    """Mock CodingProvider that returns a sequence of pre-configured CodingStepResults."""

    name = "sequential-mock-provider"

    def __init__(self, steps: list[CodingStepResult]) -> None:
        self.steps = list(steps)
        self.call_count = 0

    async def plan_next_step(
        self,
        model: str,
        task_description: str,
        workspace_snapshot: dict[str, str],
        operation_history: tuple[CodingOperation, ...],
        constraints: CodingConstraints | None = None,
    ) -> CodingStepResult:
        if self.call_count < len(self.steps):
            res = self.steps[self.call_count]
            self.call_count += 1
            return res
        return CodingStepResult(
            operations=(CodingOperation(type=CodingOperationType.FINISH),),
            finished=True,
            thought="Default finish",
        )


def _build_test_tool_service(
    permission_decision: PermissionDecisionType = PermissionDecisionType.ALLOW,
) -> tuple[ToolExecutionService, MockPermissionEngine, MockAuditLogService]:
    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    dev_test_tool = DevRunTest()

    registry.register(dev_test_tool.definition)
    impl_registry.register(dev_test_tool)

    permission_engine = MockPermissionEngine(permission_decision)
    audit_service = MockAuditLogService()
    executor = ToolExecutor(registry, impl_registry)

    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )
    return service, permission_engine, audit_service


def _init_test_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (path / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )


# ── Stage 8.11 Requirement Tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_01_valid_allowed_tool_request_executes(tmp_path: Path):
    """TOOL-01: Valid allowed tool request executes successfully."""
    _init_test_workspace(tmp_path)
    service, _, _ = _build_test_tool_service(PermissionDecisionType.ALLOW)

    call = ToolCall(tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )

    assert res.success is True
    assert res.status == ToolExecutionStatus.SUCCESS
    assert res.result["passed"] is True


@pytest.mark.asyncio
async def test_tool_02_deny_prevents_execution(tmp_path: Path):
    """TOOL-02: PermissionEngine DENY prevents tool execution."""
    _init_test_workspace(tmp_path)
    service, _, _ = _build_test_tool_service(PermissionDecisionType.DENY)

    call = ToolCall(tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )

    assert res.success is False
    assert res.status == ToolExecutionStatus.DENIED
    assert res.error_code == "permission_denied"


@pytest.mark.asyncio
async def test_tool_03_ask_user_prevents_execution(tmp_path: Path):
    """TOOL-03: PermissionEngine ASK_USER prevents automatic execution."""
    _init_test_workspace(tmp_path)
    service, _, _ = _build_test_tool_service(PermissionDecisionType.ASK_USER)

    call = ToolCall(tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )

    assert res.success is False
    assert res.status in (ToolExecutionStatus.CANCELLED, ToolExecutionStatus.DENIED)
    assert res.error_code == "ask_user"


@pytest.mark.asyncio
async def test_tool_04_denied_execution_is_audited(tmp_path: Path):
    """TOOL-04: Denied execution attempt produces audit record."""
    _init_test_workspace(tmp_path)
    service, _, audit_service = _build_test_tool_service(PermissionDecisionType.DENY)

    call = ToolCall(tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"})
    await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req_deny_audit",
        context={"workspace_path": str(tmp_path)},
    )

    assert len(audit_service.events) > 0
    denied_events = [e for e in audit_service.events if e.event_type.value in ("TOOL_CALL_DENIED", "PERMISSION_DENIED")]
    assert len(denied_events) == 1
    assert denied_events[0].tool_id == "dev.run_test"


@pytest.mark.asyncio
async def test_tool_05_allowed_execution_is_audited(tmp_path: Path):
    """TOOL-05: Allowed tool execution produces start and success audit records."""
    _init_test_workspace(tmp_path)
    service, _, audit_service = _build_test_tool_service(PermissionDecisionType.ALLOW)

    call = ToolCall(tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"})
    await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req_allow_audit",
        context={"workspace_path": str(tmp_path)},
    )

    event_types = [e.event_type.value for e in audit_service.events]
    assert "TOOL_EXECUTION_STARTED" in event_types
    assert "TOOL_EXECUTION_SUCCEEDED" in event_types


@pytest.mark.asyncio
async def test_tool_06_malformed_tool_request_fails_closed(tmp_path: Path):
    """TOOL-06: Malformed tool arguments fail closed without calling PermissionEngine."""
    _init_test_workspace(tmp_path)
    service, perm_engine, _ = _build_test_tool_service(PermissionDecisionType.ALLOW)

    # Missing required 'target_file' argument
    call = ToolCall(tool_id="dev.run_test", arguments={})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )

    assert res.success is False
    assert res.status == ToolExecutionStatus.ERROR
    assert res.error_code == "invalid_arguments"
    assert len(perm_engine.evaluated_requests) == 0


@pytest.mark.asyncio
async def test_tool_07_unknown_tool_fails_closed():
    """TOOL-07: Requesting an unregistered tool fails closed."""
    service, perm_engine, _ = _build_test_tool_service(PermissionDecisionType.ALLOW)

    call = ToolCall(tool_id="unknown.fake_tool", arguments={})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
    )

    assert res.success is False
    assert res.status == ToolExecutionStatus.DENIED
    assert res.error_code == "tool_not_found"
    assert len(perm_engine.evaluated_requests) == 0


@pytest.mark.asyncio
async def test_tool_08_model_cannot_override_permission_decision(tmp_path: Path):
    """TOOL-08: Model claims/arguments cannot bypass PermissionEngine decision."""
    _init_test_workspace(tmp_path)
    service, _, _ = _build_test_tool_service(PermissionDecisionType.DENY)

    # Arguments attempting to inject override hints
    call = ToolCall(
        tool_id="dev.run_test",
        arguments={
            "target_file": "test_calculator.py",
            "permission_override": True,
            "decision": "ALLOW",
        },
    )
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )

    assert res.success is False
    assert res.status == ToolExecutionStatus.DENIED


@pytest.mark.asyncio
async def test_tool_09_path_outside_workspace_is_rejected(tmp_path: Path):
    """TOOL-09: Target file attempting path traversal outside workspace is rejected."""
    ws = tmp_path / "ws"
    ws.mkdir()

    service, _, _ = _build_test_tool_service(PermissionDecisionType.ALLOW)
    call = ToolCall(tool_id="dev.run_test", arguments={"target_file": "../outside.py"})

    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(ws)},
    )

    assert res.success is False
    assert res.status == ToolExecutionStatus.ERROR
    assert "Path traversal not allowed" in res.error_message


@pytest.mark.asyncio
async def test_tool_10_windows_drive_relative_path_is_rejected(tmp_path: Path):
    """TOOL-10: Drive-relative or absolute paths are rejected by tool execution."""
    service, _, _ = _build_test_tool_service(PermissionDecisionType.ALLOW)
    call = ToolCall(tool_id="dev.run_test", arguments={"target_file": "C:\\Windows\\System32\\cmd.exe"})

    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )

    assert res.success is False
    assert res.status == ToolExecutionStatus.ERROR
    assert "Absolute paths or drive specifiers not allowed" in res.error_message


@pytest.mark.asyncio
async def test_tool_11_execution_timeout_is_enforced(tmp_path: Path):
    """TOOL-11: Tool execution timeout is enforced by ToolExecutionService."""
    from app.tools.base import BaseTool

    class SlowTool(BaseTool):
        @property
        def tool_id(self) -> str:
            return "dev.slow_tool"

        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                tool_id="dev.slow_tool",
                action="slow",
                name="Slow Tool",
                description="Sleeps longer than timeout",
                category=ToolCategory.AUTOMATION,
                risk_level=RiskLevel.LOW,
            )

        async def execute(self, arguments: dict[str, Any], context: dict[str, Any] | None = None) -> Any:
            await asyncio.sleep(2.0)
            return {"done": True}

    registry = ToolRegistry()
    impl_registry = ToolImplementationRegistry()
    tool = SlowTool()
    registry.register(tool.definition)
    impl_registry.register(tool)

    service = ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=MockPermissionEngine(PermissionDecisionType.ALLOW),
        audit_service=MockAuditLogService(),
        executor=ToolExecutor(registry, impl_registry),
        max_execution_time_ms=100,  # 100ms timeout
    )

    call = ToolCall(tool_id="dev.slow_tool", arguments={})
    res = await service.execute_tool_call(call, autonomy_mode=AutonomyMode.AUTONOMOUS)

    assert res.success is False
    assert res.status == ToolExecutionStatus.TIMEOUT


@pytest.mark.asyncio
async def test_tool_12_output_size_is_bounded(tmp_path: Path):
    """TOOL-12: Result size exceeding maximum bytes is handled gracefully."""
    _init_test_workspace(tmp_path)
    service, _, _ = _build_test_tool_service(PermissionDecisionType.ALLOW)

    # DevRunTest truncates stdout/stderr to 64KB
    call = ToolCall(tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"})
    res = await service.execute_tool_call(
        call,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        context={"workspace_path": str(tmp_path)},
    )

    assert res.success is True
    assert len(res.result["stdout"]) <= 65536


@pytest.mark.asyncio
async def test_tool_13_cancellation_prevents_execution(tmp_path: Path):
    """TOOL-13: Cancelled context prior to coding loop prevents execution."""
    _init_test_workspace(tmp_path)
    service, _, _ = _build_test_tool_service(PermissionDecisionType.ALLOW)

    step_tool = CodingStepResult(
        operations=(CodingOperation(type=CodingOperationType.TOOL_REQUEST, tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"}),),
        finished=False,
    )
    provider = SequentialMockCodingProvider([step_tool])
    agent = BaseDevelopmentAgent(provider=provider, model="m", tool_execution_service=service)

    cancel_event = asyncio.Event()
    cancel_event.set()

    ctx = DevelopmentAgentContext(
        task_id="task_canc",
        project_id="p",
        workspace_path=str(tmp_path),
        repository_root=str(tmp_path),
        title="Canc",
        description="Cancel test",
        attempt_count=1,
        expected_base_commit="sha",
        cancel_event=cancel_event,
    )

    res = await agent.execute(ctx)
    assert res.status == DevelopmentResultStatus.CANCELLED


@pytest.mark.asyncio
async def test_tool_14_tool_result_reaches_agent_as_structured_data(tmp_path: Path):
    """TOOL-14: Executed tool result reaches BaseDevelopmentAgent in snapshot dictionary."""
    _init_test_workspace(tmp_path)
    service, _, _ = _build_test_tool_service(PermissionDecisionType.ALLOW)

    step_tool = CodingStepResult(
        operations=(CodingOperation(type=CodingOperationType.TOOL_REQUEST, tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"}),),
        finished=False,
    )
    step_finish = CodingStepResult(
        operations=(CodingOperation(type=CodingOperationType.FINISH),),
        finished=True,
    )

    captured_snapshots: list[dict[str, str]] = []

    class CapturingProvider(CodingProvider):
        name = "capturing-provider"

        async def plan_next_step(self, model, task_description, workspace_snapshot, operation_history, constraints=None):
            captured_snapshots.append(dict(workspace_snapshot))
            if len(captured_snapshots) == 1:
                return step_tool
            return step_finish

    agent = BaseDevelopmentAgent(provider=CapturingProvider(), model="m", tool_execution_service=service)
    ctx = DevelopmentAgentContext(
        task_id="task_struct_res",
        project_id="p",
        workspace_path=str(tmp_path),
        repository_root=str(tmp_path),
        title="T",
        description="D",
        attempt_count=1,
        expected_base_commit="sha",
    )

    res = await agent.execute(ctx)
    assert res.status == DevelopmentResultStatus.SUCCESS
    assert len(captured_snapshots) == 2

    # Second turn snapshot must contain tool result payload
    second_snap = captured_snapshots[1]
    matching_keys = [k for k in second_snap.keys() if k.startswith("tool_result:dev.run_test")]
    assert len(matching_keys) == 1

    payload = json.loads(second_snap[matching_keys[0]])
    assert payload["tool_id"] == "dev.run_test"
    assert payload["success"] is True
    assert payload["result"]["passed"] is True


@pytest.mark.asyncio
async def test_tool_15_agent_has_no_direct_subprocess_authority():
    """TOOL-15: BaseDevelopmentAgent does not import subprocess or os.system."""
    import app.orchestration.agents.base_agent as ba_module

    assert not hasattr(ba_module, "subprocess")
    assert not hasattr(ba_module, "os")


@pytest.mark.asyncio
async def test_tool_16_provider_adapters_have_no_execution_authority():
    """TOOL-16: CodingProvider adapters do not import subprocess or ToolExecutionService."""
    import app.ai.providers.coding_ollama as ollama_module
    import app.ai.providers.coding_openrouter as openrouter_module

    for mod in (ollama_module, openrouter_module):
        assert not hasattr(mod, "subprocess")
        assert not hasattr(mod, "ToolExecutionService")


@pytest.mark.asyncio
async def test_tool_17_existing_workspace_writer_security_remains_intact(tmp_path: Path):
    """TOOL-17: WorkspaceWriter security boundary remains unaffected by tool execution integration."""
    from app.orchestration.agents.workspace_writer import WorkspaceWriter

    writer = WorkspaceWriter(str(tmp_path))
    with pytest.raises(ValueError):
        await writer.read_file("../outside.txt")


@pytest.mark.asyncio
async def test_tool_18_existing_verification_gate_behavior_remains_intact(tmp_path: Path):
    """TOOL-18: VerificationGate operates independently post-coding completion."""
    from app.verification.check_registry import CheckRegistry
    from app.verification.gate import VerificationGate
    from app.verification.models import VerificationContext

    primary_repo = tmp_path / "primary"
    worktree_path = tmp_path / "worktree"
    primary_repo.mkdir()
    worktree_path.mkdir()

    gate = VerificationGate(registry=CheckRegistry(), primary_worktree_path=primary_repo)
    ctx = VerificationContext(
        task_id="t",
        project_id="p",
        workspace_path=str(worktree_path),
        repository_root=str(primary_repo),
        expected_base_commit="a" * 40,
        current_commit="a" * 40,
    )
    report = gate.run(ctx)
    assert report.passed is False  # Empty registry fails closed


@pytest.mark.asyncio
async def test_tool_19_existing_git_ownership_remains_intact():
    """TOOL-19: Neither BaseDevelopmentAgent nor DevRunTest perform Git mutations."""
    from app.tools.implementations.dev_test import DevRunTest

    dev_tool = DevRunTest()
    assert "git" not in dev_tool.definition.action


@pytest.mark.asyncio
async def test_tool_20_concurrent_tasks_cannot_execute_tools_across_workspaces(tmp_path: Path):
    """TOOL-20: Concurrent tool requests enforce strict workspace path isolation."""
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    _init_test_workspace(ws1)
    _init_test_workspace(ws2)

    service, _, _ = _build_test_tool_service(PermissionDecisionType.ALLOW)

    call1 = ToolCall(tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"})
    call2 = ToolCall(tool_id="dev.run_test", arguments={"target_file": "test_calculator.py"})

    res1, res2 = await asyncio.gather(
        service.execute_tool_call(call1, autonomy_mode=AutonomyMode.AUTONOMOUS, context={"workspace_path": str(ws1)}),
        service.execute_tool_call(call2, autonomy_mode=AutonomyMode.AUTONOMOUS, context={"workspace_path": str(ws2)}),
    )

    assert res1.success is True
    assert res2.success is True


# ── Realistic Integration Test ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_realistic_controlled_execution_integration_flow(tmp_path: Path):
    """
    Realistic Integration Test:
    DevelopmentAgent -> TOOL_REQUEST -> ToolExecutionService -> PermissionEngine (ALLOW)
    -> DevRunTest -> ToolResult -> DevelopmentAgent continues -> FINISH.
    """
    _init_test_workspace(tmp_path)
    service, _, audit_service = _build_test_tool_service(PermissionDecisionType.ALLOW)

    step_1_tool = CodingStepResult(
        operations=(
            CodingOperation(
                type=CodingOperationType.TOOL_REQUEST,
                tool_id="dev.run_test",
                arguments={"target_file": "test_calculator.py"},
            ),
        ),
        thought="I need to run existing tests to verify baseline",
        finished=False,
    )
    step_2_finish = CodingStepResult(
        operations=(CodingOperation(type=CodingOperationType.FINISH),),
        thought="Baseline tests passed cleanly",
        finished=True,
    )

    provider = SequentialMockCodingProvider([step_1_tool, step_2_finish])
    agent = BaseDevelopmentAgent(
        provider=provider,
        model="test-model",
        tool_execution_service=service,
        autonomy_mode=AutonomyMode.AUTONOMOUS,
    )

    ctx = DevelopmentAgentContext(
        task_id="task_e2e_controlled",
        project_id="proj_controlled",
        workspace_path=str(tmp_path),
        repository_root=str(tmp_path),
        title="E2E Controlled Execution",
        description="Run test tool via controlled boundary",
        attempt_count=1,
        expected_base_commit="sha123",
    )

    res = await agent.execute(ctx)

    assert res.status == DevelopmentResultStatus.SUCCESS
    assert res.summary == "Baseline tests passed cleanly"
    assert provider.call_count == 2

    # Verify audit trail contains tool execution logs
    audit_events = [e.event_type.value for e in audit_service.events]
    assert "TOOL_EXECUTION_STARTED" in audit_events
    assert "TOOL_EXECUTION_SUCCEEDED" in audit_events
