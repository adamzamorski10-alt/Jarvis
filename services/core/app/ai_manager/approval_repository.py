"""
AI Manager — Capability Approval Repository.

Data access layer for application-owned capability approval requests.
"""
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_manager.approval_models import ApprovalStatus, CapabilityApproval
from app.ai_manager.models import RiskLevel
from app.db.models.capability_approval import CapabilityApprovalModel


class CapabilityApprovalRepository:
    """Repository for CapabilityApproval persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, approval: CapabilityApproval) -> CapabilityApproval:
        model = CapabilityApprovalModel(
            approval_id=approval.approval_id,
            intent_id=approval.intent_id,
            operation_type=approval.operation_type,
            scope_fingerprint=approval.scope_fingerprint,
            effective_risk=approval.effective_risk.value,
            requested_by=approval.requested_by,
            approved_by=approval.approved_by,
            denied_by=approval.denied_by,
            request_reason=approval.request_reason,
            decision_reason=approval.decision_reason,
            status=approval.status.value,
            created_at=approval.created_at,
            expires_at=approval.expires_at,
            consumed_at=approval.consumed_at,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_domain(model)

    async def get(self, approval_id: str) -> CapabilityApproval | None:
        result = await self._session.execute(
            select(CapabilityApprovalModel).where(CapabilityApprovalModel.approval_id == approval_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_pending(self, approval_id: str) -> CapabilityApproval | None:
        result = await self._session.execute(
            select(CapabilityApprovalModel)
            .where(
                CapabilityApprovalModel.approval_id == approval_id,
                CapabilityApprovalModel.status == ApprovalStatus.PENDING.value,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_for_approval(self, approval_id: str) -> CapabilityApproval | None:
        result = await self._session.execute(
            select(CapabilityApprovalModel)
            .where(
                CapabilityApprovalModel.approval_id == approval_id,
                CapabilityApprovalModel.status.in_(
                    [
                        ApprovalStatus.PENDING.value,
                        ApprovalStatus.APPROVED.value,
                    ]
                ),
            )
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def mark_approved(self, approval_id: str, approved_by: str, reason: str | None = None) -> CapabilityApproval | None:
        now = datetime.now(timezone.utc)
        stmt = (
            update(CapabilityApprovalModel)
            .where(
                CapabilityApprovalModel.approval_id == approval_id,
                CapabilityApprovalModel.status == ApprovalStatus.PENDING.value,
                CapabilityApprovalModel.expires_at > now,
            )
            .values(status=ApprovalStatus.APPROVED.value, approved_by=approved_by, decision_reason=reason)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get(approval_id)

    async def mark_denied(self, approval_id: str, denied_by: str, reason: str | None = None) -> CapabilityApproval | None:
        stmt = (
            update(CapabilityApprovalModel)
            .where(
                CapabilityApprovalModel.approval_id == approval_id,
                CapabilityApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(status=ApprovalStatus.DENIED.value, denied_by=denied_by, decision_reason=reason)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get(approval_id)

    async def mark_cancelled(self, approval_id: str, reason: str | None = None) -> CapabilityApproval | None:
        stmt = (
            update(CapabilityApprovalModel)
            .where(
                CapabilityApprovalModel.approval_id == approval_id,
                CapabilityApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(status=ApprovalStatus.CANCELLED.value, decision_reason=reason)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get(approval_id)

    async def mark_expired(self, approval_id: str) -> CapabilityApproval | None:
        stmt = (
            update(CapabilityApprovalModel)
            .where(
                CapabilityApprovalModel.approval_id == approval_id,
                CapabilityApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(status=ApprovalStatus.EXPIRED.value)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get(approval_id)

    async def mark_consumed(self, approval_id: str) -> CapabilityApproval | None:
        now = datetime.now(timezone.utc)
        stmt = (
            update(CapabilityApprovalModel)
            .where(
                CapabilityApprovalModel.approval_id == approval_id,
                CapabilityApprovalModel.status == ApprovalStatus.APPROVED.value,
            )
            .values(status=ApprovalStatus.CONSUMED.value, consumed_at=now)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get(approval_id)

    async def list_pending_for_intent(self, intent_id: str) -> list[CapabilityApproval]:
        result = await self._session.execute(
            select(CapabilityApprovalModel)
            .where(
                CapabilityApprovalModel.intent_id == intent_id,
                CapabilityApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .order_by(CapabilityApprovalModel.created_at.desc())
        )
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models]

    @staticmethod
    def _to_domain(model: CapabilityApprovalModel | None) -> CapabilityApproval | None:
        if model is None:
            return None
        return CapabilityApproval(
            approval_id=model.approval_id,
            intent_id=model.intent_id,
            operation_type=model.operation_type,
            scope_fingerprint=model.scope_fingerprint,
            effective_risk=RiskLevel(model.effective_risk),
            status=ApprovalStatus(model.status),
            requested_by=model.requested_by,
            approved_by=model.approved_by,
            denied_by=model.denied_by,
            request_reason=model.request_reason,
            decision_reason=model.decision_reason,
            created_at=model.created_at,
            expires_at=model.expires_at,
            consumed_at=model.consumed_at,
        )
