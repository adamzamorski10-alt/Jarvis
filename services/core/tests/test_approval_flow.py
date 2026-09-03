"""Tests for Stage 5.5.4 — hardened approval/ASK_USER flow."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.services.ai_service import AIService
from app.ai.models import AIResponse
from app.api.routes import approvals as approval_routes
from app.api.schemas.ai import ApprovalApproveRequest, ApprovalRejectRequest
from app.repositories.approval_repository import ApprovalRepository
from app.tools.approval_models import (
    ApprovalStatus,
    ApprovalVerificationToken,
    PendingApproval,
    canonicalize_arguments,
    compute_arguments_hash,
    compute_tool_definition_fingerprint,
)
from app.tools.approval_service import ApprovalError, ApprovalService
from app.tools.audit_models import AuditEventType
from app.tools.models import (
    AutonomyMode,
    ToolCall,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolCategory,
    RiskLevel,
)
from app.tools.tool_execution_service import ToolExecutionService


def _make_approval_repo(session) -> ApprovalRepository:
    return ApprovalRepository(session)


# ===========================================================================
# 1. Basic approval flow
# ===========================================================================

@pytest.mark.asyncio
async def test_ask_user_creates_pending_approval(session):
    ai = _make_ai_service_with_approval(session)
    provider = AsyncMock()
    tool_call = ToolCall(tool_id="system.exec", arguments={"command": "ls"})
    provider.chat = AsyncMock(
        return_value=AIResponse(content=None, tool_calls=[tool_call], finish_reason="tool_calls")
    )

    result = await ai._run_agent_loop(
        provider=provider,
        provider_name="ollama",
        model="test",
        messages=[{"role": "user", "content": "do it"}],
        autonomy_mode=AutonomyMode.MANUAL,
        request_id="req-1",
    )

    assert result.approval_required is True
    assert result.approval_id is not None
    assert result.pending_tool_call is not None
    assert result.pending_tool_call.tool_id == "system.exec"

    repo = _make_approval_repo(session)
    stored = await repo.get(result.approval_id)
    assert stored is not None
    assert stored.status == ApprovalStatus.PENDING
    assert stored.tool_id == "system.exec"
    assert stored.arguments == {"command": "ls"}
    assert stored.arguments_hash == compute_arguments_hash({"command": "ls"})
    assert stored.action == "exec"


@pytest.mark.asyncio
async def test_approve_executes_through_tool_execution_service(session):
    service = _make_approval_service(session)
    tool_def = ToolDefinition(
        action="read_file",
        tool_id="files.read_file",
        name="Read File",
        description="Reads a file.",
        category=ToolCategory.FILES,
        risk_level=RiskLevel.LOW,
        parameters_schema={"type": "object"},
        result_schema={"type": "object"},
        enabled=True,
        requires_confirmation=False,
    )
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
        tool_definition=tool_def,
    )

    updated, result = await service.approve(approval.approval_id, decision_reason="ok")

    final = await service._repo.get(approval.approval_id)
    assert final.status == ApprovalStatus.EXECUTED
    assert result.success is True
    assert result.tool_id == "files.read_file"
    tool_execution_service = service._tool_execution_service
    tool_execution_service.execute_tool_call.assert_called_once()
    call_kwargs = tool_execution_service.execute_tool_call.call_args[1]
    assert "approval_context" in call_kwargs
    assert isinstance(call_kwargs["approval_context"], ApprovalVerificationToken)


@pytest.mark.asyncio
async def test_reject_never_executes(session):
    service = _make_approval_service(session)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )

    updated = await service.reject(approval.approval_id, decision_reason="no")

    assert updated.status == ApprovalStatus.REJECTED
    tool_execution_service = service._tool_execution_service
    tool_execution_service.execute_tool_call.assert_not_called()


@pytest.mark.asyncio
async def test_expired_approval_is_rejected(session):
    service = _make_approval_service(session, ttl=timedelta(seconds=-1))
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )

    with pytest.raises(ApprovalError):
        await service.approve(approval.approval_id)

    repo = _make_approval_repo(session)
    stored = await repo.get(approval.approval_id)
    assert stored.status == ApprovalStatus.EXPIRED


@pytest.mark.asyncio
async def test_nonexistent_approval_fails(session):
    service = _make_approval_service(session)
    with pytest.raises(ApprovalError):
        await service.approve("approval_nonexistent")

    with pytest.raises(ApprovalError):
        await service.reject("approval_nonexistent")


# ===========================================================================
# 2. Concurrency
# ===========================================================================

@pytest.mark.asyncio
async def test_double_approve_only_executes_once(session):
    repo = _make_approval_repo(session)
    tool_execution_service = AsyncMock(spec=ToolExecutionService)
    tool_execution_service.execute_tool_call = AsyncMock(
        return_value=ToolExecutionResult(
            success=True,
            tool_id="files.read_file",
            tool_call_id="call-1",
            status=ToolExecutionStatus.SUCCESS,
            result={"content": "ok"},
        )
    )
    service = ApprovalService(repo=repo, tool_execution_service=tool_execution_service, settings=AsyncMock(), ttl=timedelta(hours=1))
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )

    await service.approve(approval.approval_id)
    with pytest.raises(ApprovalError):
        await service.approve(approval.approval_id)

    tool_execution_service.execute_tool_call.assert_called_once()


@pytest.mark.asyncio
async def test_concurrent_approve_only_one_succeeds(session, tmp_path):
    """
    Genuine shared-database concurrency test.

    Two independent sessions bound to the SAME SQLite file database both
    attempt to approve the same approval_id concurrently. The atomic
    WHERE clause in mark_approved (status == PENDING AND expires_at > now)
    must guarantee that exactly one succeeds and ToolExecutor runs exactly once.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    # Use a file-based SQLite with a short busy timeout
    db_path = tmp_path / "concurrent_approval.db"
    url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
    shared_engine = create_async_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    async with shared_engine.begin() as conn:
        from app.db.database import Base
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    factory = async_sessionmaker(bind=shared_engine, expire_on_commit=False, autocommit=False)

    tool_execution_service = AsyncMock(spec=ToolExecutionService)
    tool_execution_service.execute_tool_call = AsyncMock(
        return_value=ToolExecutionResult(
            success=True,
            tool_id="files.read_file",
            tool_call_id="call-1",
            status=ToolExecutionStatus.SUCCESS,
            result={"content": "ok"},
        )
    )

    # Create two independent sessions on the shared engine
    async with factory() as s1:
        async with factory() as s2:
            repo1 = ApprovalRepository(s1)
            repo2 = ApprovalRepository(s2)

            service1 = ApprovalService(
                repo=repo1, tool_execution_service=tool_execution_service,
                settings=AsyncMock(), ttl=timedelta(hours=1),
            )
            service2 = ApprovalService(
                repo=repo2, tool_execution_service=tool_execution_service,
                settings=AsyncMock(), ttl=timedelta(hours=1),
            )

            # Create the approval in s1 first, then commit so s2 can see it
            approval = await service1.create_pending_approval(
                conversation_id="conv-1",
                tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
                arguments={"path": "x.txt"},
                action="read_file",
                request_id="req-1",
            )
            await s1.commit()

            async def approve_with_service(svc, session):
                try:
                    result = await svc.approve(approval.approval_id)
                    await session.flush()
                    return ("success", result)
                except Exception:
                    return ("failed", None)

            results = await asyncio.gather(
                approve_with_service(service1, s1),
                approve_with_service(service2, s2),
            )

            await s1.commit()
            await s2.commit()

    await shared_engine.dispose()

    # Exactly one must succeed, the other must fail safely
    successes = [r for r in results if r[0] == "success"]
    failures = [r for r in results if r[0] == "failed"]
    assert len(successes) == 1
    assert len(failures) == 1

    # ToolExecutor (via mock) was called exactly once
    assert tool_execution_service.execute_tool_call.call_count == 1

    # Final state must be EXECUTED, NOT PENDING or APPROVED
    async with factory() as s3:
        repo3 = ApprovalRepository(s3)
        final = await repo3.get(approval.approval_id)
        assert final is not None
        assert final.status in (ApprovalStatus.EXECUTED, ApprovalStatus.FAILED)


# ===========================================================================
# 3. Binding
# ===========================================================================

@pytest.mark.asyncio
async def test_approval_binds_to_exact_tool_id(session):
    service = _make_approval_service(session)
    tool_def = ToolDefinition(
        action="read_file",
        tool_id="files.read_file",
        name="Read File",
        description="Reads a file.",
        category=ToolCategory.FILES,
        risk_level=RiskLevel.LOW,
        parameters_schema={"type": "object"},
        result_schema={"type": "object"},
        enabled=True,
        requires_confirmation=False,
    )
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
        tool_definition=tool_def,
    )

    stored = await service._repo.get(approval.approval_id)
    assert stored.tool_id == "files.read_file"


@pytest.mark.asyncio
async def test_arguments_hash_is_deterministic(session):
    args = {"path": "x.txt", "nested": {"a": 1, "b": 2}}
    hash1 = compute_arguments_hash(args)
    hash2 = compute_arguments_hash(args)
    assert hash1 == hash2
    assert len(hash1) == 64

    hash3 = compute_arguments_hash({"nested": {"b": 2, "a": 1}, "path": "x.txt"})
    assert hash1 == hash3


@pytest.mark.asyncio
async def test_arguments_hash_rejects_non_dict(session):
    with pytest.raises(ValueError):
        compute_arguments_hash(None)
    with pytest.raises(ValueError):
        compute_arguments_hash("string")
    with pytest.raises(ValueError):
        compute_arguments_hash([1, 2, 3])


# ===========================================================================
# 4. Policy
# ===========================================================================

@pytest.mark.asyncio
async def test_manual_high_approval_overrides_ask_user(session):
    service = _make_approval_service(session)
    tool_def = ToolDefinition(
        action="exec",
        tool_id="system.exec",
        name="Execute Command",
        description="Executes a system command.",
        category=ToolCategory.SYSTEM,
        risk_level=RiskLevel.HIGH,
        parameters_schema={"type": "object"},
        result_schema={"type": "object"},
        enabled=True,
        requires_confirmation=False,
    )
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="system.exec", arguments={"command": "ls"}),
        arguments={"command": "ls"},
        action="exec",
        request_id="req-1",
        tool_definition=tool_def,
    )

    updated, result = await service.approve(approval.approval_id)
    final = await service._repo.get(approval.approval_id)
    assert final.status == ApprovalStatus.EXECUTED
    tool_execution_service = service._tool_execution_service
    call_kwargs = tool_execution_service.execute_tool_call.call_args[1]
    assert call_kwargs["approval_context"] is not None


@pytest.mark.asyncio
async def test_disabled_tool_never_executes(session):
    tool_execution_service = AsyncMock(spec=ToolExecutionService)
    tool_execution_service.execute_tool_call = AsyncMock(
        return_value=ToolExecutionResult(
            success=False,
            tool_id="files.read_file",
            tool_call_id="call-1",
            status=ToolExecutionStatus.DENIED,
            error_code="tool_disabled",
            error_message="Tool is currently disabled.",
        )
    )
    service = ApprovalService(repo=_make_approval_repo(session), tool_execution_service=tool_execution_service, settings=AsyncMock(), ttl=timedelta(hours=1))
    tool_def = ToolDefinition(
        action="read_file",
        tool_id="files.read_file",
        name="Read File",
        description="Reads a file.",
        category=ToolCategory.FILES,
        risk_level=RiskLevel.LOW,
        parameters_schema={"type": "object"},
        result_schema={"type": "object"},
        enabled=False,
        requires_confirmation=False,
    )
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
        tool_definition=tool_def,
    )

    updated, result = await service.approve(approval.approval_id)
    final = await service._repo.get(approval.approval_id)
    assert final.status == ApprovalStatus.FAILED
    assert result.status == ToolExecutionStatus.DENIED
    assert result.error_code == "tool_disabled"


# ===========================================================================
# 5. Security — ApprovalVerificationToken
# ===========================================================================

def test_approval_verification_token_is_immutable():
    token = ApprovalVerificationToken(
        approval_id="approval_123",
        tool_id="files.read_file",
        action="read_file",
        tool_call_id="call-1",
        arguments_hash="abc123",
        request_id="req-1",
        tool_definition_version="1.0.0",
        tool_definition_fingerprint="fp123",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert token.approval_id == "approval_123"
    assert token.tool_id == "files.read_file"
    with pytest.raises(AttributeError):
        token.approval_id = "hacked"
    with pytest.raises(AttributeError):
        token.tool_id = "hacked"


def test_approval_verification_token_not_in_ai_module():
    import app.ai.providers.base as base
    assert not hasattr(base, "ApprovalVerificationToken")
    import app.ai.services.ai_service as ai_service
    assert not hasattr(ai_service, "ApprovalVerificationToken")


# ===========================================================================
# 6. Crash / failure
# ===========================================================================

@pytest.mark.asyncio
async def test_execution_exception_marks_failed(session):
    repo = _make_approval_repo(session)
    tool_execution_service = AsyncMock(spec=ToolExecutionService)
    tool_execution_service.execute_tool_call = AsyncMock(side_effect=RuntimeError("execution failed"))
    service = ApprovalService(repo=repo, tool_execution_service=tool_execution_service, settings=AsyncMock(), ttl=timedelta(hours=1))
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )

    with pytest.raises(RuntimeError):
        await service.approve(approval.approval_id)

    stored = await repo.get(approval.approval_id)
    assert stored.status == ApprovalStatus.FAILED


# ===========================================================================
# 7. Approval endpoint API tests
# ===========================================================================

@pytest.mark.asyncio
async def test_approve_endpoint_returns_execution_result(client, session):
    approval = PendingApproval(
        approval_id="approval_test_123",
        conversation_id="conv-1",
        request_id="req-1",
        tool_id="files.read_file",
        action="read_file",
        tool_call_id="call-1",
        arguments={"path": "x.txt"},
        arguments_hash=compute_arguments_hash({"path": "x.txt"}),
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    repo = _make_approval_repo(session)
    await repo.create(approval)

    response = await client.post("/api/ai/approvals/approval_test_123/approve", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["approval_id"] == "approval_test_123"
    assert body["status"] in ("EXECUTED", "APPROVED")
    assert body["action"] == "read_file"


@pytest.mark.asyncio
async def test_reject_endpoint_returns_rejected(client, session):
    approval = PendingApproval(
        approval_id="approval_test_456",
        conversation_id="conv-1",
        request_id="req-1",
        tool_id="files.read_file",
        action="read_file",
        tool_call_id="call-1",
        arguments={"path": "x.txt"},
        arguments_hash=compute_arguments_hash({"path": "x.txt"}),
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    repo = _make_approval_repo(session)
    await repo.create(approval)

    response = await client.post("/api/ai/approvals/approval_test_456/reject", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["action"] == "read_file"


@pytest.mark.asyncio
async def test_approve_invalid_approval_id_returns_400(client):
    response = await client.post("/api/ai/approvals/approval_nonexistent/approve", json={})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reject_invalid_approval_id_returns_400(client):
    response = await client.post("/api/ai/approvals/approval_nonexistent/reject", json={})
    assert response.status_code == 400


def _make_ai_service_with_approval(session, tool_execution_service=None):
    from app.services.activity_service import ActivityService
    from app.services.settings_service import SettingsService
    from app.ai.services.conversation_service import ConversationService
    from app.ai.services.provider_manager import ProviderManager

    providers = AsyncMock(spec=ProviderManager)
    conversations = AsyncMock(spec=ConversationService)
    activity = AsyncMock(spec=ActivityService)
    settings = AsyncMock(spec=SettingsService)
    settings.get_one.return_value = "manual"

    if tool_execution_service is None:
        tool_execution_service = AsyncMock(spec=ToolExecutionService)
        tool_execution_service.execute_tool_call = AsyncMock(
            return_value=ToolExecutionResult(
                success=False,
                tool_id="system.exec",
                tool_call_id="call-1",
                status=ToolExecutionStatus.CANCELLED,
                error_code="ask_user",
                error_message="User confirmation required.",
            )
        )

    def _get_td(tool_id):
        action = tool_id.split(".")[-1] if "." in tool_id else tool_id
        return ToolDefinition(
            tool_id=tool_id,
            action=action,
            name="Test Tool",
            description="Test tool for unit tests",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.LOW,
        )

    tool_execution_service.get_tool_definition = Mock(side_effect=_get_td)
    approval_service = _make_approval_service(session, tool_execution_service=tool_execution_service)
    return AIService(providers, conversations, activity, settings, tool_execution_service, approval_service)


def _make_audit_service():
    """Return a mock AuditLogService that records calls for inspection."""
    svc = AsyncMock()
    svc.record = AsyncMock(return_value=None)
    return svc


def _make_approval_service(
    session,
    tool_execution_service=None,
    settings=None,
    ttl: timedelta = timedelta(hours=1),
    audit_service=None,
) -> ApprovalService:
    repo = _make_approval_repo(session)
    if tool_execution_service is None:
        tool_execution_service = AsyncMock(spec=ToolExecutionService)
        tool_execution_service.execute_tool_call = AsyncMock(
            return_value=ToolExecutionResult(
                success=True,
                tool_id="files.read_file",
                tool_call_id="call-1",
                status=ToolExecutionStatus.SUCCESS,
                result={"content": "approved"},
            )
        )
    if settings is None:
        settings = AsyncMock()
        settings.get_one.return_value = "manual"
    if audit_service is None:
        audit_service = _make_audit_service()
    return ApprovalService(repo=repo, tool_execution_service=tool_execution_service, settings=settings, ttl=ttl, audit_service=audit_service)


# ===========================================================================
# 6. Security — Fail-closed and timestamp semantics
# ===========================================================================

@pytest.mark.asyncio
async def test_denied_result_marks_failed_not_executed(session):
    """Finding 1: DENIED/ERROR/CANCELLED results must mark FAILED, never EXECUTED."""
    service = _make_approval_service(session)
    tool_def = ToolDefinition(
        action="read_file",
        tool_id="files.read_file",
        name="Read File",
        description="Reads a file.",
        category=ToolCategory.FILES,
        risk_level=RiskLevel.LOW,
        parameters_schema={"type": "object"},
        result_schema={"type": "object"},
        enabled=True,
    )
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
        tool_definition=tool_def,
    )
    tool_execution_service = service._tool_execution_service
    tool_execution_service.execute_tool_call = AsyncMock(
        return_value=ToolExecutionResult(
            success=False,
            tool_id="files.read_file",
            tool_call_id="call-1",
            status=ToolExecutionStatus.DENIED,
            error_code="denied",
            error_message="Tool denied.",
        )
    )

    updated, result = await service.approve(approval.approval_id)
    final = await service._repo.get(approval.approval_id)
    assert final.status == ApprovalStatus.FAILED
    assert final.executed_at is None
    assert result.status == ToolExecutionStatus.DENIED


@pytest.mark.asyncio
async def test_execution_exception_marks_failed(session):
    """A raised exception during execution must leave status FAILED, not EXECUTED."""
    service = _make_approval_service(session)
    tool_def = ToolDefinition(
        action="read_file",
        tool_id="files.read_file",
        name="Read File",
        description="Reads a file.",
        category=ToolCategory.FILES,
        risk_level=RiskLevel.LOW,
        parameters_schema={"type": "object"},
        result_schema={"type": "object"},
        enabled=True,
    )
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
        tool_definition=tool_def,
    )
    tool_execution_service = service._tool_execution_service
    tool_execution_service.execute_tool_call = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await service.approve(approval.approval_id)
    final = await service._repo.get(approval.approval_id)
    assert final.status == ApprovalStatus.FAILED
    assert final.executed_at is None


@pytest.mark.asyncio
async def test_mark_executed_requires_executing_state(session):
    """Finding 6: mark_executed should only transition EXECUTING → EXECUTED, not APPROVED → EXECUTED."""
    repo = _make_approval_repo(session)
    tool_def = ToolDefinition(
        action="read_file",
        tool_id="files.read_file",
        name="Read File",
        description="Reads a file.",
        category=ToolCategory.FILES,
        risk_level=RiskLevel.LOW,
    )
    approval = await repo.create(
        PendingApproval(
            approval_id="approval_exec_test",
            conversation_id="conv-1",
            tool_id="files.read_file",
            action="read_file",
            tool_call_id="call-1",
            arguments={"path": "x.txt"},
            arguments_hash="abc",
            status=ApprovalStatus.APPROVED,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    result = await repo.mark_executed("approval_exec_test")
    assert result is None
    stored = await repo.get("approval_exec_test")
    assert stored.status == ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_mark_failed_does_not_set_executed_at(session):
    """Finding 6: mark_failed must NOT set executed_at."""
    repo = _make_approval_repo(session)
    approval = await repo.create(
        PendingApproval(
            approval_id="approval_fail_ts",
            conversation_id="conv-1",
            tool_id="files.read_file",
            action="read_file",
            tool_call_id="call-1",
            arguments={"path": "x.txt"},
            arguments_hash="abc",
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    result = await repo.mark_failed("approval_fail_ts", error="test error")
    assert result is not None
    assert result.executed_at is None


@pytest.mark.asyncio
async def test_deserialize_arguments_raises_on_corrupted_json(session):
    """Finding 7: _deserialize_arguments must fail-closed, never silently return {}."""
    from app.repositories.approval_repository import _deserialize_arguments

    with pytest.raises(ValueError, match="Corrupted arguments JSON"):
        _deserialize_arguments("{broken json")
    with pytest.raises(ValueError, match="not a dict"):
        _deserialize_arguments('"just a string"')

    assert _deserialize_arguments(None) == {}
    assert _deserialize_arguments('{"key": "value"}') == {"key": "value"}


@pytest.mark.asyncio
async def test_mark_approved_rejects_expired_approval(session):
    """Finding 4: mark_approved must not atomically approve an already-expired approval."""
    repo = _make_approval_repo(session)
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    approval = await repo.create(
        PendingApproval(
            approval_id="approval_expired_atomic",
            conversation_id="conv-1",
            tool_id="files.read_file",
            action="read_file",
            tool_call_id="call-1",
            arguments={"path": "x.txt"},
            arguments_hash="abc",
            status=ApprovalStatus.PENDING,
            created_at=past - timedelta(hours=2),
            expires_at=past,
        )
    )
    result = await repo.mark_approved("approval_expired_atomic")
    assert result is None
    stored = await repo.get("approval_expired_atomic")
    assert stored.status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_rejected_approval_never_executes(session):
    """Security: a REJECTED approval must never reach EXECUTED state."""
    service = _make_approval_service(session)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )
    tool_execution_service = service._tool_execution_service
    tool_execution_service.execute_tool_call = AsyncMock(
        return_value=ToolExecutionResult(
            success=True,
            tool_id="files.read_file",
            tool_call_id="call-1",
            status=ToolExecutionStatus.SUCCESS,
            result={"content": "ok"},
        )
    )
    await service.reject(approval.approval_id)
    stored = await service._repo.get(approval.approval_id)
    assert stored.status == ApprovalStatus.REJECTED
    tool_execution_service.execute_tool_call.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_approve_atomicity(session):
    """Finding 11: Concurrent approve calls — only one should succeed, the other gets ApprovalError."""
    repo = _make_approval_repo(session)
    tool_exec_mock = AsyncMock(spec=ToolExecutionService)
    tool_exec_mock.execute_tool_call = AsyncMock(
        return_value=ToolExecutionResult(
            success=True,
            tool_id="files.read_file",
            tool_call_id="call-1",
            status=ToolExecutionStatus.SUCCESS,
            result={"content": "ok"},
        )
    )
    settings = AsyncMock()
    settings.get_one.return_value = "manual"
    service = ApprovalService(repo=repo, tool_execution_service=tool_exec_mock, settings=settings, ttl=timedelta(hours=1))

    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )

    async def approve_once():
        try:
            await service.approve(approval.approval_id)
            return "success"
        except ApprovalError:
            return "already_processed"

    results = await asyncio.gather(approve_once(), approve_once())
    assert results.count("success") == 1
    assert results.count("already_processed") == 1
    tool_exec_mock.execute_tool_call.assert_called_once()


# ===========================================================================
# 7. Approval Lifecycle Audit Logging
# ===========================================================================

@pytest.mark.asyncio
async def test_approval_created_event_logged(session):
    """Approval creation emits APPROVAL_REQUESTED audit event."""
    audit_service = _make_audit_service()
    service = _make_approval_service(session, audit_service=audit_service)
    await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )
    audit_service.record.assert_called()
    call_kwargs = audit_service.record.call_args
    assert call_kwargs[0][0] == AuditEventType.APPROVAL_REQUESTED or "APPROVAL_REQUESTED" in str(call_kwargs)


@pytest.mark.asyncio
async def test_approval_approved_event_logged(session):
    """Approval success emits APPROVAL_APPROVED, APPROVAL_EXECUTING, APPROVAL_EXECUTED."""
    audit_service = _make_audit_service()
    service = _make_approval_service(session, audit_service=audit_service)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )
    await service.approve(approval.approval_id)

    event_types = [c.kwargs.get("event_type") or c.args[0] for c in audit_service.record.call_args_list]
    event_type_strs = [str(et) for et in event_types]
    assert any("APPROVAL_APPROVED" in s for s in event_type_strs)
    assert any("APPROVAL_EXECUTING" in s for s in event_type_strs)
    assert any("APPROVAL_EXECUTED" in s for s in event_type_strs)


@pytest.mark.asyncio
async def test_approval_rejected_event_logged(session):
    """Rejection emits APPROVAL_DENIED (rejection) audit event."""
    audit_service = _make_audit_service()
    service = _make_approval_service(session, audit_service=audit_service)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )
    await service.reject(approval.approval_id, decision_reason="user said no")

    event_types = [c.kwargs.get("event_type") or c.args[0] for c in audit_service.record.call_args_list]
    event_type_strs = [str(et) for et in event_types]
    assert any("APPROVAL_DENIED" in s for s in event_type_strs)


@pytest.mark.asyncio
async def test_approval_expired_event_logged(session):
    """Expired approval emits APPROVAL_EXPIRED audit event."""
    audit_service = _make_audit_service()
    service = _make_approval_service(session, audit_service=audit_service, ttl=timedelta(seconds=-1))
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
    )
    with pytest.raises(ApprovalError):
        await service.approve(approval.approval_id)

    event_types = [c.kwargs.get("event_type") or c.args[0] for c in audit_service.record.call_args_list]
    event_type_strs = [str(et) for et in event_types]
    assert any("APPROVAL_EXPIRED" in s for s in event_type_strs)


@pytest.mark.asyncio
async def test_approval_failed_execution_logs_failure(session):
    """Failed execution (DENIED result) emits APPROVAL_DENIED audit event, not EXECUTED."""
    audit_service = _make_audit_service()
    service = _make_approval_service(session, audit_service=audit_service)
    tool_def = ToolDefinition(
        action="read_file",
        tool_id="files.read_file",
        name="Read File",
        description="Reads a file.",
        category=ToolCategory.FILES,
        risk_level=RiskLevel.LOW,
    )
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        action="read_file",
        request_id="req-1",
        tool_definition=tool_def,
    )
    service._tool_execution_service.execute_tool_call = AsyncMock(
        return_value=ToolExecutionResult(
            success=False,
            tool_id="files.read_file",
            tool_call_id="call-1",
            status=ToolExecutionStatus.DENIED,
            error_code="permission_denied",
            error_message="Tool denied.",
        )
    )
    await service.approve(approval.approval_id)

    event_types = [c.kwargs.get("event_type") or c.args[0] for c in audit_service.record.call_args_list]
    event_type_strs = [str(et) for et in event_types]
    assert any("APPROVAL_DENIED" in s for s in event_type_strs)
    assert not any("APPROVAL_EXECUTED" in s for s in event_type_strs)


@pytest.mark.asyncio
async def test_audit_does_not_log_raw_sensitive_arguments(session):
    """Audit events must never contain raw sensitive arguments."""
    audit_service = _make_audit_service()
    service = _make_approval_service(session, audit_service=audit_service)
    await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"api_key": "SECRET123", "path": "/etc/passwd"}),
        arguments={"api_key": "SECRET123", "path": "/etc/passwd"},
        action="read_file",
        request_id="req-1",
    )
    for call in audit_service.record.call_args_list:
        args_json = str(call)
        assert "SECRET123" not in args_json


# ===========================================================================
# 8. Permission Re-evaluation During Approval — DENY must not execute
# ===========================================================================

@pytest.mark.asyncio
async def test_denied_by_permission_during_approval_does_not_execute(session):
    """
    REAL production path test:

    ApprovalService.approve()
      → ToolExecutionService.execute_tool_call()
        → PermissionEngine.evaluate()
        → DENY (CRITICAL risk)
        → ToolExecutor.execute() is NEVER called
      → approval becomes FAILED
      → executed_at remains NULL

    Uses the REAL PermissionEngine, ToolExecutionService, ToolExecutor, and
    ToolRegistry. Only the ToolExecutor.execute() method is spied to prove
    it was never invoked.
    """
    from app.tools.registry import ToolRegistry
    from app.tools.implementation_registry import ToolImplementationRegistry
    from app.tools.executor import ToolExecutor
    from app.tools.permission_engine import PermissionEngine
    from app.repositories.grant_repository import GrantRepository
    from app.repositories.audit_log_repository import AuditLogRepository
    from app.tools.audit_service import AuditLogService

    # 1. Real registry with a CRITICAL tool (enabled, registered, valid schema)
    reg = ToolRegistry()
    impl_reg = ToolImplementationRegistry()
    critical_tool_def = ToolDefinition(
        action="format",
        tool_id="system.format_drive",
        name="Format Drive",
        description="Formats a drive.",
        category=ToolCategory.SYSTEM,
        risk_level=RiskLevel.CRITICAL,
        enabled=True,
        parameters_schema={"type": "object"},
        result_schema={"type": "object"},
    )
    reg.register(critical_tool_def)

    # 2. Real PermissionEngine with no grants (CRITICAL → DENY)
    grant_repo = GrantRepository(session)
    audit_repo = AuditLogRepository(session)
    audit_svc = AuditLogService(audit_repo)
    permission_engine = PermissionEngine(
        registry=reg,
        grant_repo=grant_repo,
        audit_service=audit_svc,
    )

    # 3. Real ToolExecutor, but spy on execute() to prove it is NOT called
    executor = ToolExecutor(registry=reg, impl_registry=impl_reg)
    executor.execute = AsyncMock(return_value=None)

    # 4. Real ToolExecutionService wired with real components
    tool_execution_service = ToolExecutionService(
        registry=reg,
        impl_registry=impl_reg,
        permission_engine=permission_engine,
        audit_service=audit_svc,
        executor=executor,
    )

    # 5. Real ApprovalService with approval_repo wired
    approval_repo = _make_approval_repo(session)
    approval_service = ApprovalService(
        repo=approval_repo,
        tool_execution_service=tool_execution_service,
        settings=AsyncMock(),
        ttl=timedelta(hours=1),
        audit_service=audit_svc,
    )

    # 6. Create a pending approval for the CRITICAL tool
    approval = await approval_service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="system.format_drive", arguments={"device": "/dev/sda"}),
        arguments={"device": "/dev/sda"},
        action="format",
        request_id="req-1",
        tool_definition=critical_tool_def,
    )

    # 7. Approve — this will call execute_tool_call → PermissionEngine → DENY
    updated, result = await approval_service.approve(approval.approval_id)

    final = await approval_service._repo.get(approval.approval_id)

    # 8. Assertions: DENY must not execute
    assert executor.execute.call_count == 0, "ToolExecutor.execute() MUST NOT be called when PermissionEngine returns DENY"
    assert final.status == ApprovalStatus.FAILED, f"Expected FAILED, got {final.status}"
    assert final.executed_at is None, "executed_at MUST remain NULL when execution was denied"
    assert result.status == ToolExecutionStatus.DENIED
    assert "CRITICAL" in (result.error_message or "")
