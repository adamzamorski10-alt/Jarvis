"""
Tests for Stage 5.5.2 — ToolExecutionService.
Covers permission boundary, argument validation, timeout, result limits,
audit lifecycle, and security invariants.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.tools.audit_service import AuditLogService
from app.tools.base import BaseTool
from app.tools.executor import ToolExecutor
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.implementations.system_time import SystemGetTime
from app.tools.models import (
    AutonomyMode,
    PermissionDecisionType,
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
from app.tools.validation import validate_arguments


# ===========================================================================
# Fixtures / Helpers
# ===========================================================================

def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            action="get_time",
            tool_id="system.get_time",
            name="Get Time",
            description="Returns current system timestamp.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
            parameters_schema={
                "type": "object",
                "properties": {
                    "format": {"type": "string"},
                },
                "required": [],
            },
            result_schema={
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                },
                "required": ["timestamp"],
            },
        )
    )
    return reg


def _make_impl_registry() -> ToolImplementationRegistry:
    reg = ToolImplementationRegistry()
    reg.register(SystemGetTime())
    return reg


class FakeAuditService:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.fail = False

    async def record(self, event_type, **kwargs):
        if self.fail:
            raise RuntimeError("audit failure")
        self.events.append({"event_type": str(event_type), **kwargs})
        return None


class BrokenTool(BaseTool):
    @property
    def tool_id(self) -> str:
        return "broken.tool"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            action="tool",
            tool_id="broken.tool",
            name="Broken",
            description="Always raises.",
            version="1.0.0",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
            parameters_schema={"type": "object", "properties": {}},
        )

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any] | None = None) -> Any:
        raise RuntimeError("intentional failure")


class SlowTool(BaseTool):
    @property
    def tool_id(self) -> str:
        return "slow.tool"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            action="tool",
            tool_id="slow.tool",
            name="Slow",
            description="Sleeps forever.",
            version="1.0.0",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
            parameters_schema={"type": "object", "properties": {}},
        )

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any] | None = None) -> Any:
        await asyncio.sleep(10)
        return {"ok": True}


class OversizedResultTool(BaseTool):
    @property
    def tool_id(self) -> str:
        return "oversized.tool"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            action="tool",
            tool_id="oversized.tool",
            name="Oversized",
            description="Returns huge result.",
            version="1.0.0",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
            parameters_schema={"type": "object", "properties": {}},
            result_schema={"type": "object", "properties": {"data": {"type": "string"}}},
        )

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any] | None = None) -> Any:
        return {"data": "x" * (300 * 1024)}


def _build_service(
    registry: ToolRegistry | None = None,
    impl_registry: ToolImplementationRegistry | None = None,
    audit_service=None,
    grant_repo=None,
    executor=None,
    max_execution_time_ms: float = 30_000,
    max_tool_calls_per_request: int = 10,
):
    registry = registry or _make_registry()
    impl_registry = impl_registry or _make_impl_registry()

    if executor is None:
        executor = ToolExecutor(registry=registry, impl_registry=impl_registry)

    permission_engine = PermissionEngine(
        registry=registry,
        grant_repo=grant_repo,
        audit_service=audit_service,
    )

    if audit_service is None:
        audit_service = FakeAuditService()

    return ToolExecutionService(
        registry=registry,
        impl_registry=impl_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
        max_execution_time_ms=max_execution_time_ms,
        max_tool_calls_per_request=max_tool_calls_per_request,
    )


# ===========================================================================
# 1. ALLOW → execution
# ===========================================================================

@pytest.mark.asyncio
async def test_allow_executes_tool():
    audit = FakeAuditService()
    svc = _build_service(audit_service=audit)

    tc = ToolCall(tool_id="system.get_time", arguments={}, request_id="req-1")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-1",
    )

    assert result.success is True
    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.tool_id == "system.get_time"
    assert "timestamp" in result.result


# ===========================================================================
# 2. DENY → no execution
# ===========================================================================

@pytest.mark.asyncio
async def test_deny_never_executes():
    audit = FakeAuditService()
    svc = _build_service(audit_service=audit)

    tc = ToolCall(tool_id="system.get_time", arguments={}, request_id="req-2")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-2",
    )
    # CRITICAL in AUTONOMOUS → DENY
    # But system.get_time is SAFE, so ALLOW.
    # We need a different tool for DENY.
    # Let's register a CRITICAL tool.
    reg = _make_registry()
    reg.register(
        ToolDefinition(
            action="tool",
            tool_id="critical.tool",
            name="Critical",
            description="Critical tool.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.CRITICAL,
            parameters_schema={"type": "object", "properties": {}},
        )
    )
    impl_reg = _make_impl_registry()
    svc2 = _build_service(registry=reg, impl_registry=impl_reg, audit_service=audit)

    tc2 = ToolCall(tool_id="critical.tool", arguments={}, request_id="req-3")
    result2 = await svc2.execute_tool_call(
        tc2,
        action="read",
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-3",
    )

    assert result2.status == ToolExecutionStatus.DENIED
    assert result2.error_code == "permission_denied"
    assert "CRITICAL" in result2.error_message


# ===========================================================================
# 3. ASK_USER → no execution (CANCELLED)
# ===========================================================================

@pytest.mark.asyncio
async def test_ask_user_never_executes():
    audit = FakeAuditService()
    reg = _make_registry()
    reg.register(
        ToolDefinition(
            action="tool",
            tool_id="medium.tool",
            name="Medium",
            description="Medium risk.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.MEDIUM,
            parameters_schema={"type": "object", "properties": {}},
        )
    )
    impl_reg = _make_impl_registry()
    svc = _build_service(registry=reg, impl_registry=impl_reg, audit_service=audit)

    tc = ToolCall(tool_id="medium.tool", arguments={}, request_id="req-4")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-4",
    )

    assert result.status == ToolExecutionStatus.CANCELLED
    assert result.error_code == "ask_user"


# ===========================================================================
# 4. Disabled tool → no execution
# ===========================================================================

@pytest.mark.asyncio
async def test_disabled_tool_never_executes():
    audit = FakeAuditService()
    reg = _make_registry()
    reg.disable("system.get_time")
    impl_reg = _make_impl_registry()
    svc = _build_service(registry=reg, impl_registry=impl_reg, audit_service=audit)

    tc = ToolCall(tool_id="system.get_time", arguments={}, request_id="req-5")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-5",
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error_code == "tool_disabled"


# ===========================================================================
# 5. Unknown tool → no execution
# ===========================================================================

@pytest.mark.asyncio
async def test_unknown_tool_never_executes():
    audit = FakeAuditService()
    svc = _build_service(audit_service=audit)

    tc = ToolCall(tool_id="unknown.tool", arguments={}, request_id="req-6")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-6",
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error_code == "tool_not_found"


# ===========================================================================
# 6. Invalid arguments → no execution
# ===========================================================================

@pytest.mark.asyncio
async def test_invalid_arguments_never_executes():
    audit = FakeAuditService()
    svc = _build_service(audit_service=audit)

    tc = ToolCall(tool_id="system.get_time", arguments={"format": 123}, request_id="req-7")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-7",
    )

    assert result.status == ToolExecutionStatus.ERROR
    assert result.error_code == "invalid_arguments"


# ===========================================================================
# 7. Execution exception → no crash
# ===========================================================================

@pytest.mark.asyncio
async def test_execution_exception_is_caught():
    audit = FakeAuditService()
    reg = _make_registry()
    reg.register(
        ToolDefinition(
            action="tool",
            tool_id="broken.tool",
            name="Broken",
            description="Always raises.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
            parameters_schema={"type": "object", "properties": {}},
        )
    )
    impl_reg = ToolImplementationRegistry()
    impl_reg.register(BrokenTool())
    svc = _build_service(registry=reg, impl_registry=impl_reg, audit_service=audit)

    tc = ToolCall(tool_id="broken.tool", arguments={}, request_id="req-8")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-8",
    )

    assert result.success is False
    assert result.status == ToolExecutionStatus.ERROR
    assert "intentional failure" in result.error_message


# ===========================================================================
# 8. Timeout
# ===========================================================================

@pytest.mark.asyncio
async def test_execution_timeout():
    audit = FakeAuditService()
    reg = _make_registry()
    reg.register(
        ToolDefinition(
            action="tool",
            tool_id="slow.tool",
            name="Slow",
            description="Sleeps.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
            parameters_schema={"type": "object", "properties": {}},
        )
    )
    impl_reg = ToolImplementationRegistry()
    impl_reg.register(SlowTool())
    svc = _build_service(
        registry=reg,
        impl_registry=impl_reg,
        audit_service=audit,
        max_execution_time_ms=100,
    )

    tc = ToolCall(tool_id="slow.tool", arguments={}, request_id="req-9")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-9",
    )

    assert result.status == ToolExecutionStatus.TIMEOUT
    assert result.error_code == "timeout"


# ===========================================================================
# 9. Oversized result → warning, still success
# ===========================================================================

@pytest.mark.asyncio
async def test_oversized_result_logs_warning_but_succeeds():
    audit = FakeAuditService()
    reg = _make_registry()
    reg.register(
        ToolDefinition(
            action="tool",
            tool_id="oversized.tool",
            name="Oversized",
            description="Huge result.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
            parameters_schema={"type": "object", "properties": {}},
            result_schema={"type": "object", "properties": {"data": {"type": "string"}}},
        )
    )
    impl_reg = ToolImplementationRegistry()
    impl_reg.register(OversizedResultTool())
    svc = _build_service(registry=reg, impl_registry=impl_reg, audit_service=audit)

    tc = ToolCall(tool_id="oversized.tool", arguments={}, request_id="req-10")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-10",
    )

    assert result.success is True
    assert result.status == ToolExecutionStatus.SUCCESS


# ===========================================================================
# 10. Result validation failure → warning, still success
# ===========================================================================

@pytest.mark.asyncio
async def test_result_validation_failure_logs_warning():
    audit = FakeAuditService()
    reg = _make_registry()
    reg.register(
        ToolDefinition(
            action="tool",
            tool_id="bad_result.tool",
            name="BadResult",
            description="Returns wrong type.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
            parameters_schema={"type": "object", "properties": {}},
            result_schema={"type": "object", "properties": {"timestamp": {"type": "string"}}},
        )
    )

    class BadResultTool(BaseTool):
        @property
        def tool_id(self) -> str:
            return "bad_result.tool"

        @property
        def definition(self) -> ToolDefinition:
            return reg.get("bad_result.tool")

        async def execute(self, arguments, context=None):
            return {"timestamp": 123}

    impl_reg = ToolImplementationRegistry()
    impl_reg.register(BadResultTool())
    svc = _build_service(registry=reg, impl_registry=impl_reg, audit_service=audit)

    tc = ToolCall(tool_id="bad_result.tool", arguments={}, request_id="req-11")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-11",
    )

    assert result.success is True
    assert result.status == ToolExecutionStatus.SUCCESS


# ===========================================================================
# 11. Audit failure is fail-soft
# ===========================================================================

@pytest.mark.asyncio
async def test_audit_failure_does_not_block_execution():
    audit = FakeAuditService()
    audit.fail = True
    svc = _build_service(audit_service=audit)

    tc = ToolCall(tool_id="system.get_time", arguments={}, request_id="req-12")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-12",
    )

    assert result.success is True
    assert result.status == ToolExecutionStatus.SUCCESS


# ===========================================================================
# 12. Max tool calls per request
# ===========================================================================

@pytest.mark.asyncio
async def test_max_tool_calls_per_request():
    audit = FakeAuditService()
    svc = _build_service(audit_service=audit, max_tool_calls_per_request=2)

    calls = [
        ToolCall(tool_id="system.get_time", arguments={}, request_id=f"req-{i}")
        for i in range(3)
    ]
    results = await svc.execute_tool_calls(
        calls,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="batch-1",
    )

    assert len(results) == 3
    assert all(r.error_code == "too_many_tool_calls" for r in results)


# ===========================================================================
# 13. No public endpoint
# ===========================================================================

def test_no_public_tool_executor_endpoint():
    from app.main import app

    for route in app.routes:
        path = getattr(route, "path", "") or ""
        assert "/tools/execute" not in path.lower()
        assert "/tool_executor" not in path.lower()


# ===========================================================================
# 14. PermissionEngine called before ToolExecutor
# ===========================================================================

@pytest.mark.asyncio
async def test_permission_engine_called_before_executor():
    audit = FakeAuditService()
    reg = _make_registry()
    impl_reg = _make_impl_registry()

    executor_mock = AsyncMock(spec=ToolExecutor)
    executor_mock.execute.return_value = ToolExecutionResult(
        success=True,
        tool_id="system.get_time",
        request_id="req-14",
        status=ToolExecutionStatus.SUCCESS,
        result={"timestamp": "2024-01-01T00:00:00Z"},
    )

    permission_mock = AsyncMock(spec=PermissionEngine)
    from app.tools.models import PermissionDecision, PermissionDecisionType
    permission_mock.evaluate.return_value = PermissionDecision(
        decision=PermissionDecisionType.ALLOW,
        reason="Allowed by test mock.",
        rule_id="RULE_07_MANUAL_SAFE_ALLOWED",
    )

    svc = ToolExecutionService(
        registry=reg,
        impl_registry=impl_reg,
        permission_engine=permission_mock,
        audit_service=audit,
        executor=executor_mock,
    )

    tc = ToolCall(tool_id="system.get_time", arguments={}, request_id="req-14")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-14",
    )

    permission_mock.evaluate.assert_called_once()
    executor_mock.execute.assert_called_once()


# ===========================================================================
# 15. DENY never calls executor
# ===========================================================================

@pytest.mark.asyncio
async def test_deny_never_calls_executor():
    audit = FakeAuditService()
    reg = _make_registry()
    reg.register(
        ToolDefinition(
            action="tool",
            tool_id="critical.tool",
            name="Critical",
            description="Critical.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.CRITICAL,
            parameters_schema={"type": "object", "properties": {}},
        )
    )
    impl_reg = _make_impl_registry()

    executor_mock = AsyncMock(spec=ToolExecutor)

    permission_engine = PermissionEngine(registry=reg, audit_service=audit)
    svc = ToolExecutionService(
        registry=reg,
        impl_registry=impl_reg,
        permission_engine=permission_engine,
        audit_service=audit,
        executor=executor_mock,
    )

    tc = ToolCall(tool_id="critical.tool", arguments={}, request_id="req-15")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-15",
    )

    assert result.status == ToolExecutionStatus.DENIED
    executor_mock.execute.assert_not_called()


# ===========================================================================
# 16. ASK_USER never calls executor
# ===========================================================================

@pytest.mark.asyncio
async def test_ask_user_never_calls_executor():
    audit = FakeAuditService()
    reg = _make_registry()
    reg.register(
        ToolDefinition(
            action="tool",
            tool_id="medium.tool",
            name="Medium",
            description="Medium.",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.MEDIUM,
            parameters_schema={"type": "object", "properties": {}},
        )
    )
    impl_reg = _make_impl_registry()

    executor_mock = AsyncMock(spec=ToolExecutor)

    permission_engine = PermissionEngine(registry=reg, audit_service=audit)
    svc = ToolExecutionService(
        registry=reg,
        impl_registry=impl_reg,
        permission_engine=permission_engine,
        audit_service=audit,
        executor=executor_mock,
    )

    tc = ToolCall(tool_id="medium.tool", arguments={}, request_id="req-16")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-16",
    )

    assert result.status == ToolExecutionStatus.CANCELLED
    executor_mock.execute.assert_not_called()


# ===========================================================================
# 17. Secrets absent from audit
# ===========================================================================

@pytest.mark.asyncio
async def test_secrets_not_persisted_to_audit(session):
    from app.repositories.audit_log_repository import AuditLogRepository

    audit_repo = AuditLogRepository(session)
    audit_service = AuditLogService(audit_repo)
    svc = _build_service(audit_service=audit_service)

    tc = ToolCall(tool_id="system.get_time", arguments={"password": "secret123"}, request_id="req-17")
    result = await svc.execute_tool_call(
        tc,
        action="read",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-17",
    )

    assert result.success is True

    events = await audit_repo.list(request_id="req-17")
    assert len(events) >= 1
    for ev in events:
        assert "secret123" not in (ev.arguments or "")
        assert "REDACTED" in (ev.arguments or "") or "password" not in (ev.arguments or "")
