"""
AI Manager — Capability Approval Service.

Application-owned service for requesting, granting, denying, consuming,
and auditing explicit user approval for AI Manager policy decisions.

MODEL PROPOSES.
POLICY DECIDES WHETHER APPROVAL IS REQUIRED.
USER/APPLICATION GRANTS OR DENIES APPROVAL.
AUTHORIZED APPLICATION SERVICE EXECUTES.

The model MUST NEVER grant its own approval.

SECURITY BOUNDARY:
- This service does NOT execute capabilities.
- This service does NOT execute plans.
- This service does NOT call ToolExecutionService for generic approvals.
- This service does NOT bypass PermissionEngine.
- This service does NOT bypass PlanManager.
- Approval is a separate application-controlled authorization input.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta
from typing import Any

from app.ai_manager.approval_models import (
    ApprovalStatus,
    CapabilityApproval,
    CapabilityApprovalRequest,
    compute_scope_fingerprint,
)
from app.ai_manager.approval_repository import CapabilityApprovalRepository
from app.ai_manager.errors import AIManagerError
from app.ai_manager.models import RiskLevel
from app.core.logging import get_logger

DEFAULT_APPROVAL_TTL = timedelta(hours=1)
MAX_APPROVAL_TTL = timedelta(hours=24)


class CapabilityApprovalError(AIManagerError):
    """Base exception for capability approval errors."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Capability approval error: {reason}")


class CapabilityApprovalNotFoundError(CapabilityApprovalError):
    pass


class CapabilityApprovalStateError(CapabilityApprovalError):
    pass


class CapabilityApprovalExpiredError(CapabilityApprovalError):
    pass


class CapabilityApprovalService:
    """
    Application-owned approval service for AI Manager capability decisions.

    Enforces centralized state transitions and fail-closed semantics.
    """

    def __init__(
        self,
        repo: CapabilityApprovalRepository,
        ttl: timedelta = DEFAULT_APPROVAL_TTL,
        max_ttl: timedelta = MAX_APPROVAL_TTL,
        audit_service: Any = None,
    ) -> None:
        self._repo = repo
        self._ttl = ttl
        self._max_ttl = max_ttl
        self._audit_service = audit_service
        self._logger = get_logger(__name__)

    async def request_approval(self, request: CapabilityApprovalRequest) -> CapabilityApproval:
        """
        Create a PENDING approval request.

        The model cannot grant its own approval. This only creates a request.
        """
        now = datetime.now(timezone.utc)
        approval_id = f"cap_approval_{secrets.token_hex(16)}"

        ttl = request.ttl_seconds if request.ttl_seconds is not None else self._ttl.total_seconds()
        ttl = max(0.0, min(ttl, self._max_ttl.total_seconds()))
        expires_at = now + timedelta(seconds=ttl)

        scope_fingerprint = compute_scope_fingerprint(
            intent_id=request.intent_id,
            capabilities=request.capabilities,
            effective_risk=request.effective_risk,
            operation_type=request.operation_type,
            project_id=request.project_id,
        )

        approval = CapabilityApproval(
            approval_id=approval_id,
            intent_id=request.intent_id,
            operation_type=request.operation_type,
            scope_fingerprint=scope_fingerprint,
            effective_risk=request.effective_risk,
            status=ApprovalStatus.PENDING,
            requested_by=request.requested_by,
            request_reason=request.request_reason,
            created_at=now,
            expires_at=expires_at,
        )

        result = await self._repo.create(approval)
        await self._safe_audit(
            "APPROVAL_REQUESTED",
            approval_id=result.approval_id,
            intent_id=result.intent_id,
            reason=f"Capability approval requested for intent '{result.intent_id}'.",
            effective_risk=result.effective_risk.value,
        )
        return result

    async def get_approval(self, approval_id: str) -> CapabilityApproval | None:
        """Get an approval by ID. Returns None if not found."""
        return await self._repo.get(approval_id)

    async def approve(
        self,
        approval_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> CapabilityApproval:
        """
        Transition PENDING -> APPROVED.

        Only an explicit application-controlled approval action may grant approval.
        """
        pending = await self._get_valid_pending(approval_id)
        if pending is None:
            raise CapabilityApprovalNotFoundError(f"Approval '{approval_id}' not found or no longer pending.")

        updated = await self._repo.mark_approved(approval_id, approved_by=approved_by, reason=reason)
        if updated is None:
            raise CapabilityApprovalStateError(
                f"Approval '{approval_id}' was already processed by a concurrent request."
            )

        await self._safe_audit(
            "APPROVAL_APPROVED",
            approval_id=approval_id,
            intent_id=updated.intent_id,
            reason=f"Capability approval '{approval_id}' granted by '{approved_by}'.",
            effective_risk=updated.effective_risk.value,
        )
        return updated

    async def deny(
        self,
        approval_id: str,
        denied_by: str,
        reason: str | None = None,
    ) -> CapabilityApproval:
        """Transition PENDING -> DENIED."""
        pending = await self._get_valid_pending(approval_id)
        if pending is None:
            raise CapabilityApprovalNotFoundError(f"Approval '{approval_id}' not found or no longer pending.")

        updated = await self._repo.mark_denied(approval_id, denied_by=denied_by, reason=reason)
        if updated is None:
            raise CapabilityApprovalStateError(
                f"Approval '{approval_id}' was already processed by a concurrent request."
            )

        await self._safe_audit(
            "APPROVAL_DENIED",
            approval_id=approval_id,
            intent_id=updated.intent_id,
            reason=f"Capability approval '{approval_id}' denied by '{denied_by}'.",
            effective_risk=updated.effective_risk.value,
        )
        return updated

    async def cancel(self, approval_id: str, reason: str | None = None) -> CapabilityApproval:
        """Transition PENDING -> CANCELLED."""
        pending = await self._get_valid_pending(approval_id)
        if pending is None:
            raise CapabilityApprovalNotFoundError(f"Approval '{approval_id}' not found or no longer pending.")

        updated = await self._repo.mark_cancelled(approval_id, reason=reason)
        if updated is None:
            raise CapabilityApprovalStateError(
                f"Approval '{approval_id}' was already processed by a concurrent request."
            )

        await self._safe_audit(
            "APPROVAL_CANCELLED",
            approval_id=approval_id,
            intent_id=updated.intent_id,
            reason=f"Capability approval '{approval_id}' cancelled.",
            effective_risk=updated.effective_risk.value,
        )
        return updated

    async def consume(self, approval_id: str) -> CapabilityApproval:
        """
        Atomically transition APPROVED -> CONSUMED.

        Critical invariant: an approval cannot be consumed twice.
        Concurrent consumers result in exactly one successful consumption.
        """
        approval = await self._repo.get(approval_id)
        if approval is None:
            raise CapabilityApprovalNotFoundError(f"Approval '{approval_id}' not found.")

        if approval.status == ApprovalStatus.EXPIRED:
            raise CapabilityApprovalExpiredError(f"Approval '{approval_id}' has expired.")

        if approval.status != ApprovalStatus.APPROVED:
            raise CapabilityApprovalStateError(
                f"Approval '{approval_id}' is in state '{approval.status.value}', expected APPROVED."
            )

        updated = await self._repo.mark_consumed(approval_id)
        if updated is None:
            raise CapabilityApprovalStateError(
                f"Approval '{approval_id}' was already consumed by a concurrent request."
            )

        await self._safe_audit(
            "APPROVAL_CONSUMED",
            approval_id=approval_id,
            intent_id=updated.intent_id,
            reason=f"Capability approval '{approval_id}' consumed.",
            effective_risk=updated.effective_risk.value,
        )
        return updated

    async def _get_valid_pending(self, approval_id: str) -> CapabilityApproval | None:
        """Get a PENDING approval that is not expired. Lazy expiration."""
        pending = await self._repo.get_pending(approval_id)
        if pending is None:
            return None
        if pending.is_expired():
            await self._repo.mark_expired(approval_id)
            await self._safe_audit(
                "APPROVAL_EXPIRED",
                approval_id=approval_id,
                intent_id=pending.intent_id,
                reason=f"Capability approval '{approval_id}' expired before processing.",
                effective_risk=pending.effective_risk.value,
            )
            return None
        return pending

    async def _safe_audit(self, event_type: str, **kwargs: Any) -> None:
        """Write audit event. Fail-safe: never raises."""
        if self._audit_service is None:
            return
        try:
            await self._audit_service.record(event_type, reason=kwargs.pop("reason", ""), **kwargs)
        except Exception as exc:
            self._logger.error(
                "Audit log write failed in CapabilityApprovalService",
                event_type=event_type,
                error=str(exc),
                exc_info=True,
            )
