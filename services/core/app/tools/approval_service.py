"""
Approval Service — secure user-approval flow for ASK_USER tool calls.

Security boundary:
  Frontend -> ApprovalRoute -> ApprovalService -> ToolExecutionService -> PermissionEngine -> ToolExecutor

ApprovalService MUST NOT:
  - import ToolExecutor directly
  - import PermissionEngine directly
  - trust frontend-provided tool_id/arguments for execution
  - execute tools without going through ToolExecutionService
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta
from typing import Any

from app.core.logging import get_logger
from app.repositories.approval_repository import ApprovalRepository
from app.tools.approval_models import ApprovalStatus, PendingApproval
from app.tools.tool_execution_service import ToolExecutionService
from app.tools.models import ToolCall

logger = get_logger(__name__)

APPROVAL_TTL = timedelta(hours=1)
MAX_PENDING_APPROVALS_PER_CONVERSATION = 50


class ApprovalError(ValueError):
    pass


class ApprovalService:
    def __init__(
        self,
        repo: ApprovalRepository,
        tool_execution_service: ToolExecutionService,
        ttl: timedelta = APPROVAL_TTL,
    ) -> None:
        self._repo = repo
        self._tool_execution_service = tool_execution_service
        self._ttl = ttl

    async def create_pending_approval(
        self,
        conversation_id: str,
        tool_call: ToolCall,
        arguments: dict[str, Any],
        request_id: str | None = None,
        tool_call_result: Any = None,
    ) -> PendingApproval:
        now = datetime.now(timezone.utc)
        approval_id = f"approval_{secrets.token_hex(16)}"
        expires_at = now + self._ttl

        pending = PendingApproval(
            approval_id=approval_id,
            conversation_id=conversation_id,
            request_id=request_id,
            tool_id=tool_call.tool_id,
            arguments=arguments,
            tool_call_id=tool_call.tool_call_id,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=expires_at,
        )
        return await self._repo.create(pending)

    async def approve(
        self,
        approval_id: str,
        decision_reason: str | None = None,
    ) -> tuple[PendingApproval, Any]:
        pending = await self._get_valid_pending(approval_id)
        if pending is None:
            raise ApprovalError("Approval request not found or no longer pending.")

        now = datetime.now(timezone.utc)
        if pending.is_expired(now):
            await self._repo.mark_expired(approval_id)
            raise ApprovalError("Approval request has expired.")

        tool_call = ToolCall(
            tool_id=pending.tool_id,
            arguments=pending.arguments,
            request_id=pending.request_id,
            tool_call_id=pending.tool_call_id,
        )

        result = await self._tool_execution_service.execute_tool_call(
            tool_call,
            action=pending.tool_id.split(".")[0] if "." in pending.tool_id else pending.tool_id,
            autonomy_mode=None,
            request_id=pending.request_id,
        )

        updated = await self._repo.mark_executed(approval_id, executed_at=now)
        if updated is None:
            raise ApprovalError("Approval was already executed by a concurrent request.")

        return updated, result

    async def reject(
        self,
        approval_id: str,
        decision_reason: str | None = None,
    ) -> PendingApproval:
        pending = await self._get_valid_pending(approval_id)
        if pending is None:
            raise ApprovalError("Approval request not found or no longer pending.")

        now = datetime.now(timezone.utc)
        if pending.is_expired(now):
            await self._repo.mark_expired(approval_id)
            raise ApprovalError("Approval request has expired.")

        updated = await self._repo.mark_rejected(approval_id, reason=decision_reason)
        return updated

    async def _get_valid_pending(self, approval_id: str) -> PendingApproval | None:
        pending = await self._repo.get_pending(approval_id)
        if pending is None:
            return None
        now = datetime.now(timezone.utc)
        if pending.is_expired(now):
            await self._repo.mark_expired(approval_id)
            return None
        return pending
