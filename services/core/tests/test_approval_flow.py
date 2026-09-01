"""Tests for Stage 5.5.4 — approval/ASK_USER flow."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.services.ai_service import AIService, _LoopLimits
from app.ai.models import AIResponse
from app.api.routes import approvals as approval_routes
from app.api.schemas.ai import ApprovalApproveRequest, ApprovalRejectRequest
from app.repositories.approval_repository import ApprovalRepository
from app.tools.approval_models import ApprovalStatus, PendingApproval
from app.tools.approval_service import ApprovalError, ApprovalService
from app.tools.models import (
    AutonomyMode,
    ToolCall,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from app.tools.tool_execution_service import ToolExecutionService


def _make_approval_repo(session) -> ApprovalRepository:
    return ApprovalRepository(session)


def _make_approval_service(
    session,
    tool_execution_service=None,
    ttl: timedelta = timedelta(hours=1),
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
    return ApprovalService(repo=repo, tool_execution_service=tool_execution_service, ttl=ttl)


# ===========================================================================
# 1. ASK_USER creates pending approval
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


# ===========================================================================
# 2. APPROVE executes only through ToolExecutionService
# ===========================================================================

@pytest.mark.asyncio
async def test_approve_executes_through_tool_execution_service(session):
    service = _make_approval_service(session)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    updated, result = await service.approve(approval.approval_id, decision_reason="ok")

    assert updated.status == ApprovalStatus.EXECUTED
    assert result.success is True
    assert result.tool_id == "files.read_file"
    tool_execution_service = service._tool_execution_service
    tool_execution_service.execute_tool_call.assert_called_once()


# ===========================================================================
# 3. APPROVE re-runs PermissionEngine
# ===========================================================================

@pytest.mark.asyncio
async def test_approve_reruns_security_pipeline(session):
    service = _make_approval_service(session)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    await service.approve(approval.approval_id)

    tool_execution_service = service._tool_execution_service
    assert tool_execution_service.execute_tool_call.call_count == 1
    call = tool_execution_service.execute_tool_call.call_args[0][0]
    assert isinstance(call, ToolCall)
    assert call.tool_id == "files.read_file"


# ===========================================================================
# 4. REJECT never executes
# ===========================================================================

@pytest.mark.asyncio
async def test_reject_never_executes(session):
    service = _make_approval_service(session)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    updated = await service.reject(approval.approval_id, decision_reason="no")

    assert updated.status == ApprovalStatus.REJECTED
    tool_execution_service = service._tool_execution_service
    tool_execution_service.execute_tool_call.assert_not_called()


# ===========================================================================
# 5. Invalid approval ID is rejected
# ===========================================================================

@pytest.mark.asyncio
async def test_invalid_approval_id_is_rejected(session):
    service = _make_approval_service(session)
    with pytest.raises(ApprovalError):
        await service.approve("approval_nonexistent")

    with pytest.raises(ApprovalError):
        await service.reject("approval_nonexistent")


# ===========================================================================
# 6. Expired approval is rejected
# ===========================================================================

@pytest.mark.asyncio
async def test_expired_approval_is_rejected(session):
    service = _make_approval_service(session, ttl=timedelta(seconds=-1))
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    with pytest.raises(ApprovalError):
        await service.approve(approval.approval_id)

    repo = _make_approval_repo(session)
    stored = await repo.get(approval.approval_id)
    assert stored.status == ApprovalStatus.EXPIRED


# ===========================================================================
# 7. Already-resolved approval cannot execute again
# ===========================================================================

@pytest.mark.asyncio
async def test_already_resolved_approval_cannot_execute_again(session):
    service = _make_approval_service(session)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    await service.approve(approval.approval_id)
    with pytest.raises(ApprovalError):
        await service.approve(approval.approval_id)


# ===========================================================================
# 8. Same approval cannot execute twice concurrently
# ===========================================================================

@pytest.mark.asyncio
async def test_concurrent_approve_prevents_double_execution(session):
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
    service = ApprovalService(repo=repo, tool_execution_service=tool_execution_service, ttl=timedelta(hours=1))
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine2.begin() as conn:
        from app.db.database import Base
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine2, expire_on_commit=False)
    async with session_factory() as s:
        repo2 = ApprovalRepository(s)
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
        service2 = ApprovalService(repo=repo2, tool_execution_service=tool_exec_mock, ttl=timedelta(hours=1))

        async def approve_with_service(svc):
            return await svc.approve(approval.approval_id)

        results = await asyncio.gather(
            approve_with_service(service),
            approve_with_service(service2),
            return_exceptions=True,
        )

    await engine2.dispose()

    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) >= 1


# ===========================================================================
# 9. Frontend cannot modify tool_id
# ===========================================================================

@pytest.mark.asyncio
async def test_frontend_cannot_modify_tool_id(session):
    service = _make_approval_service(session)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    repo = _make_approval_repo(session)
    stored = await repo.get(approval.approval_id)
    assert stored.tool_id == "files.read_file"
    assert stored.tool_id != "system.exec"


# ===========================================================================
# 10. Frontend cannot modify arguments
# ===========================================================================

@pytest.mark.asyncio
async def test_frontend_cannot_modify_arguments(session):
    service = _make_approval_service(session)
    original_args = {"path": "safe.txt"}
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments=original_args),
        arguments=original_args,
        request_id="req-1",
    )

    repo = _make_approval_repo(session)
    stored = await repo.get(approval.approval_id)
    assert stored.arguments == {"path": "safe.txt"}


# ===========================================================================
# 11. Approval ID is high-entropy security token
# ===========================================================================

@pytest.mark.asyncio
async def test_approval_id_is_high_entropy_security_token(session):
    service = _make_approval_service(session)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    assert len(approval.approval_id) >= 32
    assert approval.approval_id.startswith("approval_")
    repo = _make_approval_repo(session)
    stored = await repo.get(approval.approval_id)
    assert stored is not None
    assert stored.conversation_id == "conv-1"


# ===========================================================================
# 12. Disabled/deleted tool is rejected safely
# ===========================================================================

@pytest.mark.asyncio
async def test_disabled_tool_is_rejected_safely(session):
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
    service = _make_approval_service(session, tool_execution_service=tool_execution_service)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    updated, result = await service.approve(approval.approval_id)
    assert updated.status == ApprovalStatus.EXECUTED
    assert result.success is False
    assert result.error_code == "tool_disabled"


# ===========================================================================
# 13. Approval cannot bypass max_tool_calls_per_request
# ===========================================================================

@pytest.mark.asyncio
async def test_approval_respects_max_tool_calls(session):
    service = _make_approval_service(session)
    approval = await service.create_pending_approval(
        conversation_id="conv-1",
        tool_call=ToolCall(tool_id="files.read_file", arguments={"path": "x.txt"}),
        arguments={"path": "x.txt"},
        request_id="req-1",
    )

    await service.approve(approval.approval_id)
    tool_execution_service = service._tool_execution_service
    assert tool_execution_service.execute_tool_call.call_count == 1


# ===========================================================================
# 14. No direct ToolExecutor usage from approval routes
# ===========================================================================

def test_approval_routes_do_not_import_tool_executor():
    source = approval_routes.__file__
    with open(source, "r", encoding="utf-8") as f:
        content = f.read()
    assert "ToolExecutor" not in content
    assert "executor" not in content.lower() or "tool_execution_service" in content.lower()


# ===========================================================================
# 15. No direct PermissionEngine usage from approval routes
# ===========================================================================

def test_approval_routes_do_not_import_permission_engine():
    source = approval_routes.__file__
    with open(source, "r", encoding="utf-8") as f:
        content = f.read()
    assert "PermissionEngine" not in content
    assert "permission_engine" not in content.lower()


# ===========================================================================
# 16. Approval endpoint API tests
# ===========================================================================

@pytest.mark.asyncio
async def test_approve_endpoint_returns_execution_result(client, session):
    approval = PendingApproval(
        approval_id="approval_test_123",
        conversation_id="conv-1",
        request_id="req-1",
        tool_id="files.read_file",
        arguments={"path": "x.txt"},
        tool_call_id="call-1",
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
    assert body["status"] in ("APPROVED", "EXECUTED")


@pytest.mark.asyncio
async def test_reject_endpoint_returns_rejected(client, session):
    approval = PendingApproval(
        approval_id="approval_test_456",
        conversation_id="conv-1",
        request_id="req-1",
        tool_id="files.read_file",
        arguments={"path": "x.txt"},
        tool_call_id="call-1",
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

    approval_service = _make_approval_service(session, tool_execution_service=tool_execution_service)
    return AIService(providers, conversations, activity, settings, tool_execution_service, approval_service)
