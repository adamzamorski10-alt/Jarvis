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
from app.services.settings_service import SettingsService
from app.tools.approval_models import (
    ApprovalStatus,
    ApprovalVerificationToken,
    PendingApproval,
    compute_arguments_hash,
    compute_tool_definition_fingerprint,
)
from app.tools.audit_models import AuditEventType
from app.tools.audit_service import AuditLogService
from app.tools.tool_execution_service import ToolExecutionService
from app.tools.models import AutonomyMode, ToolCall, ToolExecutionStatus

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
        settings: SettingsService,
        ttl: timedelta = APPROVAL_TTL,
        audit_service: AuditLogService | None = None,
    ) -> None:
        self._repo = repo
        self._tool_execution_service = tool_execution_service
        self._settings = settings
        self._ttl = ttl
        self._audit_service = audit_service

    async def _safe_audit(self, event_type: AuditEventType | str, *, reason: str, **kwargs: Any) -> None:
        """Write an audit event for the approval lifecycle. Fail-safe: never raises."""
        if self._audit_service is None:
            return
        try:
            await self._audit_service.record(event_type, reason=reason, **kwargs)
        except Exception as exc:
            logger.error(
                "Audit log write failed in ApprovalService",
                event_type=str(event_type),
                error=str(exc),
                exc_info=True,
            )

    async def _get_autonomy_mode(self) -> AutonomyMode:
        raw = await self._settings.get_one("autonomy_mode")
        if raw == "semi_autonomous":
            return AutonomyMode.SEMI_AUTONOMOUS
        if raw == "autonomous":
            return AutonomyMode.AUTONOMOUS
        return AutonomyMode.MANUAL

    async def create_pending_approval(
        self,
        conversation_id: str,
        tool_call: ToolCall,
        arguments: dict[str, Any],
        action: str | None = None,
        request_id: str | None = None,
        tool_definition: Any = None,
        tool_call_result: Any = None,
    ) -> PendingApproval:
        now = datetime.now(timezone.utc)
        approval_id = f"approval_{secrets.token_hex(16)}"
        expires_at = now + self._ttl

        # Resolve canonical action from ToolDefinition if not explicitly provided
        resolved_action = action if action is not None else (
            getattr(tool_definition, "action", None) if tool_definition else None
        )
        if resolved_action is None:
            raise ApprovalError("Cannot create approval: action is required and tool_definition.action is unavailable.")

        arguments_hash = compute_arguments_hash(arguments)
        tool_definition_version = getattr(tool_definition, "version", None) if tool_definition else None
        tool_definition_fingerprint = compute_tool_definition_fingerprint(tool_definition) if tool_definition else None

        pending = PendingApproval(
            approval_id=approval_id,
            conversation_id=conversation_id,
            request_id=request_id,
            tool_id=tool_call.tool_id,
            action=resolved_action,
            tool_call_id=tool_call.tool_call_id,
            arguments=arguments,
            arguments_hash=arguments_hash,
            tool_definition_version=tool_definition_version,
            tool_definition_fingerprint=tool_definition_fingerprint,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=expires_at,
        )
        result = await self._repo.create(pending)
        await self._safe_audit(
            AuditEventType.APPROVAL_REQUESTED,
            reason=f"Approval '{result.approval_id}' pending user decision for tool '{result.tool_id}' action '{result.action}'.",
            request_id=request_id,
            tool_id=tool_call.tool_id,
            action=resolved_action,
        )
        return result

    async def approve(
        self,
        approval_id: str,
        decision_reason: str | None = None,
    ) -> tuple[PendingApproval, Any]:
        pending = await self._get_valid_pending(approval_id)
        if pending is None:
            raise ApprovalError("Approval request not found or no longer pending.")

        now = datetime.now(timezone.utc)

        # 1. Atomic transition PENDING -> APPROVED
        updated = await self._repo.mark_approved(approval_id)
        if updated is None:
            raise ApprovalError("Approval was already processed by a concurrent request.")

        await self._safe_audit(
            AuditEventType.APPROVAL_APPROVED,
            reason=f"Approval '{approval_id}' approved by user.",
            request_id=pending.request_id,
            tool_id=pending.tool_id,
            action=pending.action,
        )

        # 2. Create server-side verification token
        token = ApprovalVerificationToken(
            approval_id=pending.approval_id,
            tool_id=pending.tool_id,
            action=pending.action,
            tool_call_id=pending.tool_call_id,
            arguments_hash=pending.arguments_hash,
            request_id=pending.request_id,
            tool_definition_version=pending.tool_definition_version,
            tool_definition_fingerprint=pending.tool_definition_fingerprint,
            expires_at=pending.expires_at,
        )

        # 3. Reconstruct ToolCall from server-side state ONLY
        tool_call = ToolCall(
            tool_id=pending.tool_id,
            arguments=pending.arguments,
            request_id=pending.request_id,
            tool_call_id=pending.tool_call_id,
        )

        # 4. Transition to EXECUTING before execution
        executing = await self._repo.mark_executing(approval_id)
        if executing is None:
            raise ApprovalError("Approval was already processed by a concurrent request.")

        await self._safe_audit(
            AuditEventType.APPROVAL_EXECUTING,
            reason=f"Approval '{approval_id}' transitioning to execution.",
            request_id=pending.request_id,
            tool_id=pending.tool_id,
            action=pending.action,
        )

        # 5. Execute through ToolExecutionService with approval context
        autonomy_mode = await self._get_autonomy_mode()
        try:
            result = await self._tool_execution_service.execute_tool_call(
                tool_call,
                action=pending.action,
                autonomy_mode=autonomy_mode,
                request_id=pending.request_id,
                context={"tool_call_id": pending.tool_call_id, "request_id": pending.request_id},
                approval_context=token,
            )
        except Exception as exc:
            await self._repo.mark_failed(approval_id, error=str(exc))
            await self._safe_audit(
                AuditEventType.APPROVAL_FAILED,
                reason=f"Approval '{approval_id}' execution raised exception: {exc}",
                request_id=pending.request_id,
                tool_id=pending.tool_id,
                action=pending.action,
                success=False,
                error_code=getattr(exc, "code", "execution_error") or "execution_error",
                error_message=str(exc),
            )
            raise

        # 6. Only mark EXECUTED when ToolExecutor actually completed successfully.
        # Any DENY/CANCELLED/TIMEOUT/ERROR result must NOT become EXECUTED.
        if result.success and result.status == ToolExecutionStatus.SUCCESS:
            await self._repo.mark_executed(approval_id, executed_at=now)
            await self._safe_audit(
                AuditEventType.APPROVAL_EXECUTED,
                reason=f"Approval '{approval_id}' executed successfully.",
                request_id=pending.request_id,
                tool_id=pending.tool_id,
                action=pending.action,
                success=True,
                execution_status=result.status.value,
            )
        else:
            await self._repo.mark_failed(approval_id, error=f"Execution did not succeed: {result.status.value}")
            await self._safe_audit(
                AuditEventType.APPROVAL_DENIED,
                reason=f"Approval '{approval_id}' denied: execution did not succeed (status={result.status.value}).",
                request_id=pending.request_id,
                tool_id=pending.tool_id,
                action=pending.action,
                success=False,
                error_code=result.error_code,
                error_message=result.error_message,
                execution_status=result.status.value,
            )

        return updated, result

    async def reject(
        self,
        approval_id: str,
        decision_reason: str | None = None,
    ) -> PendingApproval:
        pending = await self._get_valid_pending(approval_id)
        if pending is None:
            raise ApprovalError("Approval request not found or no longer pending.")

        updated = await self._repo.mark_rejected(approval_id, reason=decision_reason)
        await self._safe_audit(
            AuditEventType.APPROVAL_DENIED,
            reason=f"Approval '{approval_id}' rejected by user. Reason: {decision_reason or 'unspecified'}",
            request_id=pending.request_id,
            tool_id=pending.tool_id,
            action=pending.action,
            decision="REJECTED",
        )
        return updated

    async def _get_valid_pending(self, approval_id: str) -> PendingApproval | None:
        pending = await self._repo.get_pending(approval_id)
        if pending is None:
            return None
        now = datetime.now(timezone.utc)
        if pending.is_expired(now):
            await self._repo.mark_expired(approval_id)
            await self._safe_audit(
                AuditEventType.APPROVAL_EXPIRED,
                reason=f"Approval '{approval_id}' expired before processing.",
                request_id=pending.request_id,
                tool_id=pending.tool_id,
                action=pending.action,
            )
            return None
        return pending
