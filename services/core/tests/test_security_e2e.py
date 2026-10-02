"""
Stage 5.5.5 — E2E Security Integration Tests.

Tests exercise the FULL real production security pipeline:

    AI/ToolCall
      → ToolExecutionService.execute_tool_call
        → PermissionEngine.evaluate
        → ToolExecutor.execute

Uses REAL PermissionEngine, ToolExecutionService, ToolExecutor,
ToolRegistry, ApprovalService, ApprovalRepository, AuditLogService.
Only the tool implementation's execute() is spied to count calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import asyncio
import pytest
from sqlalchemy import text

from app.core.logging import get_logger
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.grant_repository import GrantRepository
from app.services.settings_service import SettingsService
from app.tools.approval_models import (
    ApprovalStatus,
    ApprovalVerificationToken,
    compute_tool_definition_fingerprint,
)
from app.tools.approval_service import ApprovalError, ApprovalService
from app.tools.audit_service import AuditLogService
from app.tools.base import BaseTool
from app.tools.executor import ToolExecutor
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.models import (
    AutonomyMode,
    GrantType,
    RiskLevel,
    ToolCall,
    ToolCategory,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionStatus,
)
from app.tools.permission_engine import PermissionEngine
from app.tools.registry import ToolRegistry
from app.tools.tool_execution_service import ToolExecutionService

logger = get_logger(__name__)


# ===========================================================================
# Test tool implementation
# ===========================================================================

class SpyTool(BaseTool):
    """A test tool implementation that records every execute() call."""

    def __init__(
        self,
        tool_id: str = "test.spy_tool",
        action: str = "spy",
        risk_level: RiskLevel = RiskLevel.LOW,
        requires_confirmation: bool = False,
        enabled: bool = True,
    ) -> None:
        self._tool_id = tool_id
        self._action = action
        self._risk_level = risk_level
        self._requires_confirmation = requires_confirmation
        self._enabled = enabled
        self.execute_calls: list[dict[str, Any]] = []

    @property
    def tool_id(self) -> str:
        return self._tool_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_id=self._tool_id,
            action=self._action,
            name=f"Spy Tool {self._tool_id}",
            description="Test spy tool.",
            version="1.0.0",
            category=ToolCategory.SYSTEM,
            risk_level=self._risk_level,
            requires_confirmation=self._requires_confirmation,
            enabled=self._enabled,
            parameters_schema={"type": "object"},
            result_schema={"type": "object"},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> Any:
        self.execute_calls.append({"arguments": arguments, "context": context})
        return {"spied": True, "arguments": arguments}


# ===========================================================================
# Component wiring helper
# ===========================================================================

class Comp:
    """Real production components wired together for E2E testing."""

    def __init__(self, session):
        self.session = session
        self.registry = ToolRegistry()
        self.impl_registry = ToolImplementationRegistry()
        self.grant_repo = GrantRepository(session)
        self.approval_repo = ApprovalRepository(session)
        self.audit_repo = AuditLogRepository(session)
        self.audit_service = AuditLogService(self.audit_repo)
        self.settings = SettingsService(session)
        self.permission_engine = PermissionEngine(
            registry=self.registry,
            grant_repo=self.grant_repo,
            audit_service=self.audit_service,
            approval_repo=self.approval_repo,
        )
        self.executor = ToolExecutor(
            registry=self.registry,
            impl_registry=self.impl_registry,
        )
        self.tes = ToolExecutionService(
            registry=self.registry,
            impl_registry=self.impl_registry,
            permission_engine=self.permission_engine,
            audit_service=self.audit_service,
            executor=self.executor,
        )

    def reg(self, impl: SpyTool) -> None:
        self.registry.register(impl.definition)
        self.impl_registry.register(impl)

    @property
    def approval_service(self) -> ApprovalService:
        return ApprovalService(
            repo=self.approval_repo,
            tool_execution_service=self.tes,
            settings=self.settings,
            audit_service=self.audit_service,
        )


@pytest.fixture
def comp(session):
    return Comp(session)


async def _seed(comp):
    await comp.settings.ensure_defaults()
    await comp.session.commit()


# ===========================================================================
# A. ALLOW — real execution path
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_allow_executes_tool_once(comp, session):
    """E2E ALLOW: AI tool call → PermissionEngine ALLOW → ToolExecutor executes exactly once."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.allow_tool", action="allow", risk_level=RiskLevel.LOW)
    comp.reg(impl)

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.allow_tool", arguments={"key": "value"}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-allow",
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert len(impl.execute_calls) == 1, "Tool must execute exactly once"
    assert impl.execute_calls[0]["arguments"] == {"key": "value"}

    # Audit log should capture success
    events = await comp.audit_repo.list(tool_id="test.allow_tool")
    assert any("TOOL_EXECUTION_SUCCEEDED" in str(e.event_type) for e in events)


# ===========================================================================
# B. DENY — ToolExecutor MUST NOT be called
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_deny_disabled_tool_never_executes(comp):
    """E2E DENY: Disabled tool → DENY, ToolExecutor.call_count == 0."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.deny_tool", action="deny", risk_level=RiskLevel.LOW)
    comp.reg(impl)

    # Disable the tool via registry runtime state
    comp.registry.disable("test.deny_tool")

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.deny_tool", arguments={}),
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-deny",
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0, "ToolExecutor.execute MUST NOT be called on DENY"

    events = await comp.audit_repo.list(tool_id="test.deny_tool")
    assert any("TOOL_CALL_REQUESTED" in str(e.event_type) for e in events)


@pytest.mark.asyncio
async def test_e2e_deny_unknown_tool_never_executes(comp):
    """E2E: Unknown tool → DENY, executor not called."""
    await _seed(comp)
    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="not.registered.tool", arguments={}),
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-unknown",
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert result.error_code == "tool_not_found"


@pytest.mark.asyncio
async def test_e2e_critical_never_executes_manual(comp):
    """CRITICAL + MANUAL → DENY, ToolExecutor not called."""
    await _seed(comp)
    impl = SpyTool(tool_id="system.critical_op", action="critical_op", risk_level=RiskLevel.CRITICAL)
    comp.reg(impl)

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="system.critical_op", arguments={}),
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-crit",
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_critical_never_executes_autonomous(comp):
    """CRITICAL + AUTONOMOUS → DENY, ToolExecutor not called."""
    await _seed(comp)
    impl = SpyTool(tool_id="system.critical_op", action="critical_op", risk_level=RiskLevel.CRITICAL)
    comp.reg(impl)

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="system.critical_op", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-crit-auto",
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_critical_with_persistent_grant_comp_still_denied(comp):
    """CRITICAL + persistent grant → DENY (grant cannot bypass CRITICAL)."""
    await _seed(comp)
    impl = SpyTool(tool_id="system.critical_op", action="critical_op", risk_level=RiskLevel.CRITICAL)
    comp.reg(impl)
    await comp.grant_repo.create_grant(
        tool_id="system.critical_op",
        action_pattern="critical_op",
        grant_type=GrantType.PERSISTENT,
    )
    await comp.session.commit()

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="system.critical_op", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-crit-grant",
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


# ===========================================================================
# C. ASK_USER → APPROVE → EXECUTE (full real flow)
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_ask_user_approve_executes_once(comp):
    """Full real flow: ASK_USER → APPROVE → permission re-check → execution."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.ask_tool", action="ask", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.ask_tool", arguments={"data": "test"})
    tool_def = comp.registry.get("test.ask_tool")

    # Step 1: Execute → ASK_USER (HIGH risk in MANUAL → ASK_USER)
    result1 = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-ask",
    )
    assert result1.status == ToolExecutionStatus.CANCELLED
    assert result1.error_code == "ask_user"
    assert len(impl.execute_calls) == 0

    # Step 2: Create approval (simulating AIService._run_agent_loop)
    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments=tool_call.arguments,
        action=tool_def.action,
        request_id="req-ask",
        tool_definition=tool_def,
    )
    assert approval.status == ApprovalStatus.PENDING

    # Step 3: Approve → re-verify permissions → execute
    updated, exec_result = await comp.approval_service.approve(approval.approval_id)
    final = await comp.approval_repo.get(approval.approval_id)

    assert final.status == ApprovalStatus.EXECUTED
    assert final.executed_at is not None
    assert exec_result.status == ToolExecutionStatus.SUCCESS
    assert len(impl.execute_calls) == 1, "Tool must execute exactly once on approved"

    # Audit events
    events = await comp.audit_repo.list(request_id="req-ask")
    event_types = [str(e.event_type) for e in events]
    assert any("APPROVAL_REQUESTED" in et for et in event_types)
    assert any("APPROVAL_APPROVED" in et for et in event_types)
    assert any("APPROVAL_EXECUTING" in et for et in event_types)
    assert any("APPROVAL_EXECUTED" in et for et in event_types)


# ===========================================================================
# D. ASK_USER → REJECT
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_ask_user_reject_never_executes(comp):
    """ASK_USER → REJECT → ToolExecutor not called."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.reject_tool", action="reject", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.reject_tool", arguments={"data": "test"})
    tool_def = comp.registry.get("test.reject_tool")

    # Step 1: ASK_USER
    result1 = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-reject",
    )
    assert result1.status == ToolExecutionStatus.CANCELLED

    # Step 2: Create and reject approval
    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments=tool_call.arguments,
        action=tool_def.action,
        request_id="req-reject",
        tool_definition=tool_def,
    )
    await comp.approval_service.reject(approval.approval_id, decision_reason="User declined")
    final = await comp.approval_repo.get(approval.approval_id)

    assert final.status == ApprovalStatus.REJECTED
    assert len(impl.execute_calls) == 0, "Tool must NOT execute after rejection"

    events = await comp.audit_repo.list(request_id="req-reject")
    event_types = [str(e.event_type) for e in events]
    assert any("APPROVAL_REQUESTED" in et for et in event_types)
    assert any("APPROVAL_DENIED" in et for et in event_types)


# ===========================================================================
# E. Approval Expiration
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_expired_approval_cannot_approve(comp):
    """Expired approval → cannot be approved, ToolExecutor not called."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.exp_tool", action="exp", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.exp_tool", arguments={})
    tool_def = comp.registry.get("test.exp_tool")

    # Create approval with immediate expiration
    svc = comp.approval_service
    svc._ttl = timedelta(seconds=-1)
    approval = await svc.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments=tool_call.arguments,
        action=tool_def.action,
        request_id="req-exp",
        tool_definition=tool_def,
    )
    svc._ttl = timedelta(hours=1)

    with pytest.raises(ApprovalError):
        await comp.approval_service.approve(approval.approval_id)

    final = await comp.approval_repo.get(approval.approval_id)
    assert final.status in (ApprovalStatus.EXPIRED, ApprovalStatus.PENDING)
    assert len(impl.execute_calls) == 0

    events = await comp.audit_repo.list(request_id="req-exp")
    event_types = [str(e.event_type) for e in events]
    assert any("APPROVAL_EXPIRED" in et for et in event_types)


# ===========================================================================
# F. Approval Tampering
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_tampered_tool_id_denied(comp):
    """Approval bound to tool A must not authorize tool B."""
    await _seed(comp)
    impl_a = SpyTool(tool_id="test.tamper_a", action="act_a", risk_level=RiskLevel.HIGH)
    impl_b = SpyTool(tool_id="test.tamper_b", action="act_b", risk_level=RiskLevel.HIGH)
    comp.reg(impl_a)
    comp.reg(impl_b)

    tool_call = ToolCall(tool_id="test.tamper_a", arguments={"data": "original"})
    tool_def = comp.registry.get("test.tamper_a")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments=tool_call.arguments,
        action=tool_def.action,
        request_id="req-tamper",
        tool_definition=tool_def,
    )
    # Manually transition to APPROVED (bypass full approve execution)
    await comp.approval_repo.mark_approved(approval.approval_id)

    # Build a tampered token with wrong tool_id
    tampered_token = ApprovalVerificationToken(
        approval_id=approval.approval_id,
        tool_id="test.tamper_b",  # TAMPERED
        action=tool_def.action,
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=approval.expires_at,
    )

    tampered_call = ToolCall(tool_id="test.tamper_b", arguments={"data": "original"})
    result = await comp.tes.execute_tool_call(
        tampered_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-tamper",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-tamper"},
        approval_context=tampered_token,
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl_b.execute_calls) == 0
    assert len(impl_a.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_tampered_action_in_db_denied(comp):
    """If the DB-stored approval action is tampered, execution must be DENIED."""
    from sqlalchemy import text

    await _seed(comp)
    impl = SpyTool(tool_id="test.act_tamper", action="act_a", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.act_tamper", arguments={})
    tool_def = comp.registry.get("test.act_tamper")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-act",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)
    await comp.approval_repo.mark_executing(approval.approval_id)

    # Tamper with the DB-stored action directly
    await comp.approval_repo._session.execute(
        text("UPDATE pending_approvals SET action='act_b' WHERE approval_id=:id"),
        {"id": approval.approval_id},
    )
    await comp.approval_repo._session.commit()

    token = ApprovalVerificationToken(
        approval_id=approval.approval_id,
        tool_id=approval.tool_id,
        action="act_a",
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=approval.expires_at,
    )

    result = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-act",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-act"},
        approval_context=token,
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_tampered_arguments_denied(comp):
    """Approval for arguments X must not authorize arguments Y."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.arg_tamper", action="arg", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.arg_tamper", arguments={"key": "original"})
    tool_def = comp.registry.get("test.arg_tamper")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments=tool_call.arguments,
        action=tool_def.action,
        request_id="req-arg-tamper",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)
    await comp.approval_repo.mark_executing(approval.approval_id)

    # Tampered token with correct hash (token uses the original hash)
    # But we pass DIFFERENT arguments in the ToolCall
    correct_token = ApprovalVerificationToken(
        approval_id=approval.approval_id,
        tool_id=approval.tool_id,
        action=approval.action,
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=approval.expires_at,
    )

    # Execute with tampered arguments
    tampered_call = ToolCall(tool_id="test.arg_tamper", arguments={"key": "TAMPERED"})
    result = await comp.tes.execute_tool_call(
        tampered_call,
        action="arg",
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-arg-tamper",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-arg-tamper"},
        approval_context=correct_token,
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_tool_definition_tampering_denied(comp):
    """If tool definition changes after approval (different fingerprint), execution is DENIED."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.fp_tamper", action="fp", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.fp_tamper", arguments={})
    tool_def = comp.registry.get("test.fp_tamper")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-fp",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)
    await comp.approval_repo.mark_executing(approval.approval_id)

    # Tamper with the tool definition in the registry after approval
    tampered_def = ToolDefinition(
        tool_id=tool_def.tool_id,
        action=tool_def.action,
        name=tool_def.name,
        description="Tampered description",
        version="2.0.0",
        category=tool_def.category,
        risk_level=RiskLevel.CRITICAL,
        requires_confirmation=True,
        enabled=False,
        parameters_schema={"type": "object", "properties": {"evil": {"type": "string"}}},
        result_schema={"type": "object"},
    )
    comp.registry.unregister("test.fp_tamper")
    comp.registry.register(tampered_def)

    token = ApprovalVerificationToken(
        approval_id=approval.approval_id,
        tool_id=approval.tool_id,
        action=approval.action,
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=approval.expires_at,
    )

    result = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-fp",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-fp"},
        approval_context=token,
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


# ===========================================================================
# G. Persistent Grant
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_persistent_grant_allows_low_risk(comp):
    """Persistent grant allows execution for qualifying tool."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.grant_tool", action="grant", risk_level=RiskLevel.LOW)
    comp.reg(impl)
    await comp.grant_repo.create_grant(
        tool_id="test.grant_tool",
        action_pattern="grant",
        grant_type=GrantType.PERSISTENT,
    )
    await comp.session.commit()

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.grant_tool", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-grant",
    )
    assert result.status == ToolExecutionStatus.SUCCESS
    assert len(impl.execute_calls) == 1


# ===========================================================================
# H. One-Time Grant
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_one_time_grant_single_use(comp):
    """One-time grant: first use succeeds, second use denied."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.otp_tool", action="otp", risk_level=RiskLevel.MEDIUM)
    comp.reg(impl)
    await comp.grant_repo.create_grant(
        tool_id="test.otp_tool",
        action_pattern="otp",
        grant_type=GrantType.ONE_TIME,
    )
    await comp.session.commit()

    tool_call = ToolCall(tool_id="test.otp_tool", arguments={})

    # First use
    result1 = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-otp-1",
    )
    assert result1.status == ToolExecutionStatus.SUCCESS
    assert len(impl.execute_calls) == 1

    # Second use — same grant consumed
    result2 = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-otp-2",
    )
    assert result2.status == ToolExecutionStatus.CANCELLED
    assert len(impl.execute_calls) == 1, "Tool must not execute a second time"


# ===========================================================================
# I. Concurrency — double approval
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_concurrent_approve_single_execution(comp, tmp_path):
    """Two concurrent approve calls on same approval → exactly 1 execution."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    await _seed(comp)
    impl = SpyTool(tool_id="test.concurrent_tool", action="concurrent", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.concurrent_tool", arguments={})
    tool_def = comp.registry.get("test.concurrent_tool")

    # Create approval and transition to APPROVED + EXECUTING
    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-conc",
        tool_definition=tool_def,
    )

    # Both concurrent approve paths will find the approval already in EXECUTING state
    # and the atomic mark_executed / mark_failed will serialize.
    # But we test the actual approve path with shared DB state.
    # Use a shared file DB for true concurrency
    from sqlalchemy import text
    db_path = tmp_path / "e2e_concurrent.db"
    url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
    shared_engine = create_async_engine(url, connect_args={"check_same_thread": False, "timeout": 5})
    async with shared_engine.begin() as conn:
        from app.db.database import Base
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    factory = async_sessionmaker(bind=shared_engine, expire_on_commit=False)
    shared_comp = Comp.__new__(Comp)
    shared_comp.registry = comp.registry
    shared_comp.impl_registry = comp.impl_registry

    async with factory() as s1:
        async with factory() as s2:
            repo1 = ApprovalRepository(s1)
            repo2 = ApprovalRepository(s2)
            await s1.commit()  # init
            await s2.commit()  # init

            svc1 = ApprovalService(
                repo=repo1,
                tool_execution_service=comp.tes,
                settings=comp.settings,
                audit_service=comp.audit_service,
            )
            svc2 = ApprovalService(
                repo=repo2,
                tool_execution_service=comp.tes,
                settings=comp.settings,
                audit_service=comp.audit_service,
            )

            # Need approval in the shared DB
            approval_shared = await repo1.create(
                __import__("app.tools.approval_models", fromlist=["PendingApproval"]).PendingApproval(
                    approval_id=approval.approval_id,
                    conversation_id="conv-1",
                    request_id="req-conc",
                    tool_id=tool_call.tool_id,
                    action=tool_def.action,
                    tool_call_id=tool_call.tool_call_id,
                    arguments={},
                    arguments_hash=approval.arguments_hash,
                    tool_definition_version=tool_def.version,
                    tool_definition_fingerprint=approval.tool_definition_fingerprint,
                    status=ApprovalStatus.PENDING,
                    created_at=approval.created_at,
                    expires_at=approval.expires_at,
                )
            )
            await s1.commit()

            async def try_approve(svc):
                try:
                    await svc.approve(approval.approval_id)
                    return "success"
                except Exception:
                    return "failed"

            results = await asyncio.gather(try_approve(svc1), try_approve(svc2))
            await s1.commit()
            await s2.commit()

    await shared_engine.dispose()

    successes = sum(1 for r in results if r == "success")
    assert successes == 1, f"Expected exactly 1 success, got {successes}"
    assert len(impl.execute_calls) <= 1, "Tool must execute at most once"


# ===========================================================================
# J. Security Invariants
# ===========================================================================

@pytest.mark.asyncio
async def test_invariant_deny_zero_executor_calls(comp):
    """INVARIANT 1: DENY => ToolExecutor.execute == 0"""
    await _seed(comp)
    impl = SpyTool(tool_id="test.inv_denay", action="inv", risk_level=RiskLevel.LOW)
    comp.reg(impl)
    comp.registry.disable("test.inv_denay")

    await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.inv_denay", arguments={}),
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-inv1",
    )
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_invariant_ask_user_without_approval_zero_calls(comp):
    """INVARIANT 2: ASK_USER without valid approval => 0 executions"""
    await _seed(comp)
    impl = SpyTool(tool_id="test.inv_ask", action="inv", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.inv_ask", arguments={}),
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-inv2",
    )
    assert result.status == ToolExecutionStatus.CANCELLED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_invariant_critical_zero_calls(comp):
    """INVARIANT 6: CRITICAL => ToolExecutor.execute == 0"""
    await _seed(comp)
    impl = SpyTool(tool_id="test.inv_crit", action="inv", risk_level=RiskLevel.CRITICAL)
    comp.reg(impl)

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.inv_crit", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-inv6",
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_invariant_unknown_tool_zero_calls(comp):
    """INVARIANT 7: unknown/disabled tool => 0 executions"""
    await _seed(comp)
    impl = SpyTool(tool_id="test.inv_unknown", action="inv", risk_level=RiskLevel.LOW)
    comp.reg(impl)
    comp.registry.disable("test.inv_unknown")

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.inv_unknown", arguments={}),
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-inv7",
    )
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_invariant_otp_single_execution(comp):
    """INVARIANT 9: one-time grant => maximum one successful execution"""
    await _seed(comp)
    impl = SpyTool(tool_id="test.inv_otp", action="inv", risk_level=RiskLevel.MEDIUM)
    comp.reg(impl)
    await comp.grant_repo.create_grant(
        tool_id="test.inv_otp",
        action_pattern="inv",
        grant_type=GrantType.ONE_TIME,
    )
    await comp.session.commit()

    tool_call = ToolCall(tool_id="test.inv_otp", arguments={})
    r1 = await comp.tes.execute_tool_call(tool_call, autonomy_mode=AutonomyMode.MANUAL, request_id="req-otp-1")
    r2 = await comp.tes.execute_tool_call(tool_call, autonomy_mode=AutonomyMode.MANUAL, request_id="req-otp-2")

    assert r1.status == ToolExecutionStatus.SUCCESS
    assert r2.status == ToolExecutionStatus.CANCELLED
    assert len(impl.execute_calls) == 1


# ===========================================================================
# K. DB/Repository Failures
# ===========================================================================

class BrokenGrantRepository:
    """Grant repository that always raises on DB operations."""

    async def find_matching_grant(self, *args, **kwargs):
        raise RuntimeError("DB connection lost")

    async def consume_one_time_grant_atomically(self, *args, **kwargs):
        raise RuntimeError("DB connection lost")


class BrokenApprovalRepository:
    """Approval repository that always raises on DB operations."""

    async def get(self, *args, **kwargs):
        raise RuntimeError("DB connection lost")

    async def get_pending(self, *args, **kwargs):
        raise RuntimeError("DB connection lost")


class BrokenAuditRepository:
    """Audit repository that always raises on DB operations."""

    async def record(self, *args, **kwargs):
        raise RuntimeError("DB connection lost")

    async def list(self, *args, **kwargs):
        return ()


@pytest.mark.asyncio
async def test_e2e_db_grant_repo_failure_fail_closed(comp):
    """DB failure in GrantRepository must not reach ToolExecutor."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.db_grant", action="db_grant", risk_level=RiskLevel.LOW)
    comp.reg(impl)

    broken_repo = BrokenGrantRepository()
    broken_pe = PermissionEngine(
        registry=comp.registry,
        grant_repo=broken_repo,
        audit_service=comp.audit_service,
        approval_repo=comp.approval_repo,
    )
    broken_tes = ToolExecutionService(
        registry=comp.registry,
        impl_registry=comp.impl_registry,
        permission_engine=broken_pe,
        audit_service=comp.audit_service,
        executor=comp.executor,
    )

    result = await broken_tes.execute_tool_call(
        ToolCall(tool_id="test.db_grant", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-db-grant",
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_db_approval_repo_failure_denies(comp):
    """DB failure in ApprovalRepository during approval override must DENY."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.db_appr", action="db_appr", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.db_appr", arguments={})
    tool_def = comp.registry.get("test.db_appr")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-db-appr",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)

    broken_pe = PermissionEngine(
        registry=comp.registry,
        grant_repo=comp.grant_repo,
        audit_service=comp.audit_service,
        approval_repo=BrokenApprovalRepository(),
    )
    broken_tes = ToolExecutionService(
        registry=comp.registry,
        impl_registry=comp.impl_registry,
        permission_engine=broken_pe,
        audit_service=comp.audit_service,
        executor=comp.executor,
    )

    token = ApprovalVerificationToken(
        approval_id=approval.approval_id,
        tool_id=approval.tool_id,
        action=approval.action,
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=approval.expires_at,
    )

    result = await broken_tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-db-appr",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-db-appr"},
        approval_context=token,
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


# ===========================================================================
# L. AuditLog Failure Behavior
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_audit_log_failure_does_not_change_decision(comp):
    """AuditLog failure must not alter the computed permission decision."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.audit_fail", action="audit_fail", risk_level=RiskLevel.LOW)
    comp.reg(impl)

    broken_audit_repo = BrokenAuditRepository()
    broken_audit_service = AuditLogService(broken_audit_repo)

    pe = PermissionEngine(
        registry=comp.registry,
        grant_repo=comp.grant_repo,
        audit_service=broken_audit_service,
        approval_repo=comp.approval_repo,
    )
    tes = ToolExecutionService(
        registry=comp.registry,
        impl_registry=comp.impl_registry,
        permission_engine=pe,
        audit_service=broken_audit_service,
        executor=comp.executor,
    )

    result = await tes.execute_tool_call(
        ToolCall(tool_id="test.audit_fail", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-audit-fail",
    )
    assert result.status == ToolExecutionStatus.SUCCESS
    assert len(impl.execute_calls) == 1


# ===========================================================================
# M. AI Response Normalization Edge Cases
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_empty_ai_response_no_tool_calls(comp):
    """Empty AI response normalizes to zero tool calls."""
    from app.ai.normalization import normalize_tool_calls

    calls = normalize_tool_calls([])
    assert calls == []


@pytest.mark.asyncio
async def test_e2e_whitespace_only_ai_response_no_tool_calls(comp):
    """Whitespace-only content in AI response raises validation error."""
    from pydantic import ValidationError
    from app.ai.normalization import normalize_tool_calls

    with pytest.raises(ValidationError):
        normalize_tool_calls([
            {"type": "function", "function": {"name": "  ", "arguments": {}}},
        ])


@pytest.mark.asyncio
async def test_e2e_malformed_ai_response_raises(comp):
    """Malformed AI response raises ToolCallNormalizationError."""
    from app.ai.normalization import ToolCallNormalizationError, normalize_tool_calls

    with pytest.raises(ToolCallNormalizationError):
        normalize_tool_calls([{"type": "invalid", "function": {"name": "x"}}])


@pytest.mark.asyncio
async def test_e2e_missing_tool_name_raises(comp):
    """Missing tool name in AI response raises ToolCallNormalizationError."""
    from app.ai.normalization import ToolCallNormalizationError, normalize_tool_calls

    with pytest.raises(ToolCallNormalizationError):
        normalize_tool_calls([{"type": "function", "function": {}}])


# ===========================================================================
# N. Invalid Tool Call / Argument Validation
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_invalid_tool_call_format(comp):
    """Invalid tool call format is rejected before execution."""
    await _seed(comp)
    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="not.a.valid", arguments={}),
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-invalid-call",
    )
    assert result.status == ToolExecutionStatus.DENIED


@pytest.mark.asyncio
async def test_e2e_missing_required_arguments(comp):
    """Missing required arguments causes validation error."""
    await _seed(comp)
    impl = SpyTool(
        tool_id="test.req_args",
        action="req_args",
        risk_level=RiskLevel.LOW,
    )
    comp.reg(impl)
    # Override the registered definition to require a field
    from app.tools.models import ToolDefinition, ToolCategory
    req_def = ToolDefinition(
        tool_id="test.req_args",
        action="req_args",
        name="Req Args Tool",
        description="Requires arguments.",
        version="1.0.0",
        category=ToolCategory.SYSTEM,
        risk_level=RiskLevel.LOW,
        parameters_schema={
            "type": "object",
            "required": ["required_field"],
            "properties": {"required_field": {"type": "string"}},
        },
        result_schema={"type": "object"},
    )
    comp.registry.unregister("test.req_args")
    comp.registry.register(req_def)

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.req_args", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-missing-args",
    )
    assert result.status == ToolExecutionStatus.ERROR
    assert result.error_code == "invalid_arguments"


@pytest.mark.asyncio
async def test_e2e_invalid_argument_types(comp):
    """Invalid argument types cause validation error."""
    await _seed(comp)
    impl = SpyTool(
        tool_id="test.type_args",
        action="type_args",
        risk_level=RiskLevel.LOW,
    )
    comp.reg(impl)
    from app.tools.models import ToolDefinition, ToolCategory
    type_def = ToolDefinition(
        tool_id="test.type_args",
        action="type_args",
        name="Type Args Tool",
        description="Requires typed arguments.",
        version="1.0.0",
        category=ToolCategory.SYSTEM,
        risk_level=RiskLevel.LOW,
        parameters_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        },
        result_schema={"type": "object"},
    )
    comp.registry.unregister("test.type_args")
    comp.registry.register(type_def)

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.type_args", arguments={"count": "not_an_int"}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-bad-types",
    )
    assert result.status == ToolExecutionStatus.ERROR
    assert result.error_code == "invalid_arguments"


# ===========================================================================
# O. Multiple / Duplicated Tool Calls
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_multiple_tool_calls(comp):
    """Multiple tool calls each go through the full security pipeline."""
    await _seed(comp)
    impl1 = SpyTool(tool_id="test.multi_a", action="multi_a", risk_level=RiskLevel.LOW)
    impl2 = SpyTool(tool_id="test.multi_b", action="multi_b", risk_level=RiskLevel.LOW)
    comp.reg(impl1)
    comp.reg(impl2)

    results = await comp.tes.execute_tool_calls(
        [
            ToolCall(tool_id="test.multi_a", arguments={}),
            ToolCall(tool_id="test.multi_b", arguments={}),
        ],
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-multi",
    )
    assert len(results) == 2
    assert all(r.status == ToolExecutionStatus.SUCCESS for r in results)
    assert len(impl1.execute_calls) == 1
    assert len(impl2.execute_calls) == 1


@pytest.mark.asyncio
async def test_e2e_duplicated_tool_calls_execute_independently(comp):
    """Duplicate tool calls execute independently (no deduplication at TES level)."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.dup", action="dup", risk_level=RiskLevel.LOW)
    comp.reg(impl)

    tc = ToolCall(tool_id="test.dup", arguments={})
    results = await comp.tes.execute_tool_calls(
        [tc, tc],
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-dup",
    )
    assert len(results) == 2
    assert all(r.status == ToolExecutionStatus.SUCCESS for r in results)
    assert len(impl.execute_calls) == 2


# ===========================================================================
# P. Request/Tool_Call Correlation
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_missing_tool_call_id_correlation(comp):
    """Missing tool_call_id in context breaks approval correlation."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.corr", action="corr", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.corr", arguments={})
    tool_def = comp.registry.get("test.corr")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-corr",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)

    token = ApprovalVerificationToken(
        approval_id=approval.approval_id,
        tool_id=approval.tool_id,
        action=approval.action,
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=approval.expires_at,
    )

    # Missing tool_call_id in context
    result = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-corr",
        context={"request_id": "req-corr"},
        approval_context=token,
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


# ===========================================================================
# Q. Provider Normalization → ToolExecutionService
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_normalization_to_tes_pipeline(comp):
    """Normalized provider tool calls flow correctly through TES."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.norm", action="norm", risk_level=RiskLevel.LOW)
    comp.reg(impl)

    from app.ai.normalization import normalize_tool_calls

    raw = [
        {
            "id": "call-norm-1",
            "type": "function",
            "function": {"name": "test.norm", "arguments": '{"key": "value"}'},
        }
    ]
    calls = normalize_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0].tool_id == "test.norm"
    assert calls[0].arguments == {"key": "value"}

    result = await comp.tes.execute_tool_call(
        calls[0],
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-norm",
    )
    assert result.status == ToolExecutionStatus.SUCCESS
    assert len(impl.execute_calls) == 1


# ===========================================================================
# R. Approval Tampering (Additional)
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_tampered_approval_id_denied(comp):
    """Non-existent approval_id must be DENIED."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.appr_id", action="appr_id", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.appr_id", arguments={})
    tool_def = comp.registry.get("test.appr_id")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-appr-id",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)

    token = ApprovalVerificationToken(
        approval_id="nonexistent_approval_id",
        tool_id=approval.tool_id,
        action=approval.action,
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=approval.expires_at,
    )

    result = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-appr-id",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-appr-id"},
        approval_context=token,
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_tampered_tool_call_id_denied(comp):
    """tool_call_id mismatch between approval and request must DENY."""
    from sqlalchemy import text

    await _seed(comp)
    impl = SpyTool(tool_id="test.tc_id", action="tc_id", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.tc_id", arguments={})
    tool_def = comp.registry.get("test.tc_id")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-tc-id",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)
    await comp.approval_repo.mark_executing(approval.approval_id)

    # Pass wrong tool_call_id in request context
    result = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-tc-id",
        context={"tool_call_id": "wrong_tool_call_id", "request_id": "req-tc-id"},
        approval_context=ApprovalVerificationToken(
            approval_id=approval.approval_id,
            tool_id=approval.tool_id,
            action=approval.action,
            tool_call_id=approval.tool_call_id,
            arguments_hash=approval.arguments_hash,
            request_id=approval.request_id,
            tool_definition_version=approval.tool_definition_version,
            tool_definition_fingerprint=approval.tool_definition_fingerprint,
            expires_at=approval.expires_at,
        ),
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_tampered_args_hash_denied(comp):
    """arguments_hash mismatch must DENY."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.hash_tamper", action="hash_tamper", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.hash_tamper", arguments={"key": "original"})
    tool_def = comp.registry.get("test.hash_tamper")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments=tool_call.arguments,
        action=tool_def.action,
        request_id="req-hash",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)
    await comp.approval_repo.mark_executing(approval.approval_id)

    # Pass tampered arguments in the ToolCall — hash will mismatch
    tampered_call = ToolCall(tool_id="test.hash_tamper", arguments={"key": "TAMPERED"})
    result = await comp.tes.execute_tool_call(
        tampered_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-hash",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-hash"},
        approval_context=ApprovalVerificationToken(
            approval_id=approval.approval_id,
            tool_id=approval.tool_id,
            action=approval.action,
            tool_call_id=approval.tool_call_id,
            arguments_hash=approval.arguments_hash,
            request_id=approval.request_id,
            tool_definition_version=approval.tool_definition_version,
            tool_definition_fingerprint=approval.tool_definition_fingerprint,
            expires_at=approval.expires_at,
        ),
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_tampered_request_id_denied(comp):
    """request_id mismatch must DENY."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.req_id", action="req_id", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.req_id", arguments={})
    tool_def = comp.registry.get("test.req_id")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-req-id",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)
    await comp.approval_repo.mark_executing(approval.approval_id)

    # Pass wrong request_id in context
    result = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-req-id",
        context={"tool_call_id": approval.tool_call_id, "request_id": "wrong_request_id"},
        approval_context=ApprovalVerificationToken(
            approval_id=approval.approval_id,
            tool_id=approval.tool_id,
            action=approval.action,
            tool_call_id=approval.tool_call_id,
            arguments_hash=approval.arguments_hash,
            request_id=approval.request_id,
            tool_definition_version=approval.tool_definition_version,
            tool_definition_fingerprint=approval.tool_definition_fingerprint,
            expires_at=approval.expires_at,
        ),
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


@pytest.mark.asyncio
async def test_e2e_tampered_expiration_denied(comp):
    """Expired approval in DB must DENY."""
    from sqlalchemy import text

    await _seed(comp)
    impl = SpyTool(tool_id="test.exp_tamper", action="exp_tamper", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.exp_tamper", arguments={})
    tool_def = comp.registry.get("test.exp_tamper")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-exp-tamper",
        tool_definition=tool_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)

    # Set DB expiration to the past
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    await comp.approval_repo._session.execute(
        text("UPDATE pending_approvals SET expires_at=:exp WHERE approval_id=:id"),
        {"exp": past.isoformat(), "id": approval.approval_id},
    )
    await comp.approval_repo._session.commit()

    token = ApprovalVerificationToken(
        approval_id=approval.approval_id,
        tool_id=approval.tool_id,
        action=approval.action,
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=past,
    )

    result = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-exp-tamper",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-exp-tamper"},
        approval_context=token,
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


# ===========================================================================
# S. Invalid Approval Token / Context
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_none_approval_context_denied(comp):
    """None approval_context with ASK_USER-level risk must not execute."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.none_ctx", action="none_ctx", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.none_ctx", arguments={}),
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-none-ctx",
        approval_context=None,
    )
    assert result.status == ToolExecutionStatus.CANCELLED
    assert len(impl.execute_calls) == 0


# ===========================================================================
# T. Replay of Already-Consumed Approval / One-Time Grant
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_replay_consumed_one_time_grant_denied(comp):
    """Replaying a request after one-time grant consumption must be DENIED."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.replay", action="replay", risk_level=RiskLevel.MEDIUM)
    comp.reg(impl)

    await comp.grant_repo.create_grant(
        tool_id="test.replay",
        action_pattern="replay",
        grant_type=GrantType.ONE_TIME,
    )
    await comp.session.commit()

    tool_call = ToolCall(tool_id="test.replay", arguments={})

    # First use consumes grant
    r1 = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-replay-1",
    )
    assert r1.status == ToolExecutionStatus.SUCCESS
    assert len(impl.execute_calls) == 1

    # Replay same request
    r2 = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-replay-2",
    )
    assert r2.status == ToolExecutionStatus.CANCELLED
    assert len(impl.execute_calls) == 1


@pytest.mark.asyncio
async def test_e2e_replay_executed_approval_denied(comp):
    """Replaying an already-executed approval must be DENIED."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.replay_appr", action="replay_appr", risk_level=RiskLevel.HIGH)
    comp.reg(impl)

    tool_call = ToolCall(tool_id="test.replay_appr", arguments={})
    tool_def = comp.registry.get("test.replay_appr")

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=tool_def.action,
        request_id="req-replay-appr",
        tool_definition=tool_def,
    )
    updated, exec_result = await comp.approval_service.approve(approval.approval_id)
    assert exec_result.status == ToolExecutionStatus.SUCCESS

    # Replay the same approval
    token = ApprovalVerificationToken(
        approval_id=approval.approval_id,
        tool_id=approval.tool_id,
        action=approval.action,
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=approval.expires_at,
    )

    result = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-replay-appr",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-replay-appr"},
        approval_context=token,
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 1  # only the first execution


# ===========================================================================
# U. Concurrent One-Time Grant
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_concurrent_one_time_grant_single_success(comp, tmp_path):
    """Concurrent requests with same one-time grant → exactly 1 succeeds."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    await _seed(comp)
    impl = SpyTool(tool_id="test.conc_otp", action="conc_otp", risk_level=RiskLevel.MEDIUM)
    comp.reg(impl)

    db_path = tmp_path / "e2e_concurrent_otp.db"
    url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
    shared_engine = create_async_engine(url, connect_args={"check_same_thread": False, "timeout": 5})
    async with shared_engine.begin() as conn:
        from app.db.database import Base
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    factory = async_sessionmaker(bind=shared_engine, expire_on_commit=False)

    async with factory() as seed_session:
        seed_repo = GrantRepository(seed_session)
        grant = await seed_repo.create_grant(
            tool_id="test.conc_otp",
            action_pattern="conc_otp",
            grant_type=GrantType.ONE_TIME,
        )
        await seed_session.commit()

    async with factory() as s1:
        async with factory() as s2:
            repo1 = GrantRepository(s1)
            repo2 = GrantRepository(s2)
            approval_repo1 = ApprovalRepository(s1)
            approval_repo2 = ApprovalRepository(s2)
            await s1.commit()
            await s2.commit()

            pe1 = PermissionEngine(registry=comp.registry, grant_repo=repo1, approval_repo=approval_repo1)
            pe2 = PermissionEngine(registry=comp.registry, grant_repo=repo2, approval_repo=approval_repo2)
            tes1 = ToolExecutionService(
                registry=comp.registry,
                impl_registry=comp.impl_registry,
                permission_engine=pe1,
                audit_service=comp.audit_service,
                executor=comp.executor,
            )
            tes2 = ToolExecutionService(
                registry=comp.registry,
                impl_registry=comp.impl_registry,
                permission_engine=pe2,
                audit_service=comp.audit_service,
                executor=comp.executor,
            )

            tc = ToolCall(tool_id="test.conc_otp", arguments={})

            async def try_exec(tes):
                try:
                    return await tes.execute_tool_call(tc, autonomy_mode=AutonomyMode.MANUAL, request_id="req-conc-otp")
                except Exception:
                    return None

            r1, r2 = await asyncio.gather(try_exec(tes1), try_exec(tes2))
            await s1.commit()
            await s2.commit()

    await shared_engine.dispose()

    successes = sum(1 for r in (r1, r2) if r is not None and r.status == ToolExecutionStatus.SUCCESS)
    assert successes == 1, f"Expected exactly 1 success, got {successes}"
    assert len(impl.execute_calls) == 1, f"Expected exactly 1 tool execution, got {len(impl.execute_calls)}"



# ===========================================================================
# V. ToolExecutor / Tool Implementation Failure
# ===========================================================================

class FailingTool(BaseTool):
    """Tool implementation that always raises."""

    def __init__(self) -> None:
        self._tool_id = "test.failing"
        self._action = "fail"
        self._risk_level = RiskLevel.LOW
        self._requires_confirmation = False
        self._enabled = True

    @property
    def tool_id(self) -> str:
        return self._tool_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_id=self._tool_id,
            action=self._action,
            name="Failing Tool",
            description="Always fails.",
            version="1.0.0",
            category=ToolCategory.SYSTEM,
            risk_level=self._risk_level,
            requires_confirmation=self._requires_confirmation,
            enabled=self._enabled,
            parameters_schema={"type": "object"},
            result_schema={"type": "object"},
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> Any:
        raise RuntimeError("Intentional tool failure")


@pytest.mark.asyncio
async def test_e2e_tool_implementation_failure_reports_error(comp):
    """Tool implementation exception → ERROR status, not SUCCESS."""
    await _seed(comp)
    failing = FailingTool()
    comp.reg(failing)

    result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.failing", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-fail",
    )
    assert result.status == ToolExecutionStatus.ERROR
    assert result.error_code == "execution_error"
    assert "Intentional tool failure" in (result.error_message or "")


# ===========================================================================
# W. Fail-Closed Behavior
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_fail_closed_approval_repo_none(comp):
    """Without approval repo, any approval context must DENY."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.fail_closed", action="fail_closed", risk_level=RiskLevel.LOW)
    comp.reg(impl)

    pe = PermissionEngine(
        registry=comp.registry,
        grant_repo=comp.grant_repo,
        audit_service=comp.audit_service,
        approval_repo=None,
    )
    tes = ToolExecutionService(
        registry=comp.registry,
        impl_registry=comp.impl_registry,
        permission_engine=pe,
        audit_service=comp.audit_service,
        executor=comp.executor,
    )

    token = ApprovalVerificationToken(
        approval_id="fake",
        tool_id="test.fail_closed",
        action="fail_closed",
        tool_call_id="fake",
        arguments_hash="a" * 64,
        request_id="req-fail-closed",
        tool_definition_version="1.0.0",
        tool_definition_fingerprint="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    result = await tes.execute_tool_call(
        ToolCall(tool_id="test.fail_closed", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-fail-closed",
        approval_context=token,
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


# ===========================================================================
# X. Security Invariant #10: ToolExecutor is Never the Authorization Boundary
# ===========================================================================

@pytest.mark.asyncio
async def test_invariant_tool_executor_not_authorization_boundary(comp):
    """
    INVARIANT 10: ToolExecutor.execute() must never perform permission checks.
    PermissionEngine MUST ALWAYS be evaluated before ToolExecutor.execute().
    """
    await _seed(comp)
    impl = SpyTool(tool_id="test.inv_exec", action="inv_exec", risk_level=RiskLevel.CRITICAL)
    comp.reg(impl)

    # Direct ToolExecutor call for a CRITICAL tool must NOT be blocked by ToolExecutor itself.
    # ToolExecutor only checks registry existence and enabled state.
    # The authorization boundary is PermissionEngine.
    direct_result = await comp.executor.execute(
        ToolCall(tool_id="test.inv_exec", arguments={})
    )
    assert direct_result.status == ToolExecutionStatus.SUCCESS
    assert len(impl.execute_calls) == 1

    # But ToolExecutionService must DENY CRITICAL because PermissionEngine is the boundary.
    tes_result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.inv_exec", arguments={}),
        autonomy_mode=AutonomyMode.AUTONOMOUS,
        request_id="req-inv-exec",
    )
    assert tes_result.status == ToolExecutionStatus.DENIED


# ===========================================================================
# Y. Comprehensive Tool-Definition Tampering
# ===========================================================================

@pytest.mark.asyncio
async def test_e2e_tool_definition_field_tampering_denied(comp):
    """Changing any tool-definition field after approval invalidates the approval."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.def_tamp", action="def_tamp", risk_level=RiskLevel.LOW)
    comp.reg(impl)
    original_def = comp.registry.get("test.def_tamp")

    tool_call = ToolCall(tool_id="test.def_tamp", arguments={})

    approval = await comp.approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=tool_call,
        arguments={},
        action=original_def.action,
        request_id="req-def-tamp",
        tool_definition=original_def,
    )
    await comp.approval_repo.mark_approved(approval.approval_id)
    await comp.approval_repo.mark_executing(approval.approval_id)

    tampered_def = ToolDefinition(
        tool_id=original_def.tool_id,
        action=original_def.action,
        name=original_def.name,
        description="Completely different description",
        version="9.9.9",
        category=original_def.category,
        risk_level=RiskLevel.CRITICAL,
        requires_confirmation=not original_def.requires_confirmation,
        enabled=not original_def.enabled,
        parameters_schema={"type": "object", "properties": {"evil": {"type": "string"}}},
        result_schema={"type": "string"},
    )
    comp.registry.unregister("test.def_tamp")
    comp.registry.register(tampered_def)

    token = ApprovalVerificationToken(
        approval_id=approval.approval_id,
        tool_id=approval.tool_id,
        action=approval.action,
        tool_call_id=approval.tool_call_id,
        arguments_hash=approval.arguments_hash,
        request_id=approval.request_id,
        tool_definition_version=approval.tool_definition_version,
        tool_definition_fingerprint=approval.tool_definition_fingerprint,
        expires_at=approval.expires_at,
    )

    result = await comp.tes.execute_tool_call(
        tool_call,
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-def-tamp",
        context={"tool_call_id": approval.tool_call_id, "request_id": "req-def-tamp"},
        approval_context=token,
    )
    assert result.status == ToolExecutionStatus.DENIED
    assert len(impl.execute_calls) == 0


# ===========================================================================
# Z. Additional Invariants
# ===========================================================================

@pytest.mark.asyncio
async def test_invariant_executor_never_authorization_boundary(comp):
    """INVARIANT 10: Direct ToolExecutor call bypasses PermissionEngine."""
    await _seed(comp)
    impl = SpyTool(tool_id="test.inv_auth", action="inv_auth", risk_level=RiskLevel.LOW)
    comp.reg(impl)

    # Direct executor bypasses all permission checks
    direct = await comp.executor.execute(
        ToolCall(tool_id="test.inv_auth", arguments={})
    )
    assert direct.status == ToolExecutionStatus.SUCCESS

    # TES enforces permissions
    tes_result = await comp.tes.execute_tool_call(
        ToolCall(tool_id="test.inv_auth", arguments={}),
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-inv-auth",
    )
    assert tes_result.status == ToolExecutionStatus.CANCELLED
