"""
Repository — Pending Approvals
Data access layer for ASK_USER approval requests in SQLite.
"""
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.pending_approval import PendingApprovalModel
from app.tools.approval_models import ApprovalStatus, PendingApproval


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, approval: PendingApproval) -> PendingApproval:
        model = PendingApprovalModel(
            approval_id=approval.approval_id,
            conversation_id=approval.conversation_id,
            request_id=approval.request_id,
            tool_id=approval.tool_id,
            arguments=_serialize_arguments(approval.arguments),
            tool_call_id=approval.tool_call_id,
            status=approval.status.value,
            created_at=approval.created_at,
            expires_at=approval.expires_at,
            decision_reason=approval.decision_reason,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_domain(model)

    async def get(self, approval_id: str) -> PendingApproval | None:
        result = await self._session.execute(
            select(PendingApprovalModel).where(PendingApprovalModel.approval_id == approval_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_pending(self, approval_id: str) -> PendingApproval | None:
        result = await self._session.execute(
            select(PendingApprovalModel)
            .where(
                PendingApprovalModel.approval_id == approval_id,
                PendingApprovalModel.status == ApprovalStatus.PENDING.value,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def mark_executed(self, approval_id: str, executed_at: datetime | None = None) -> PendingApproval | None:
        now = executed_at or datetime.now(timezone.utc)
        stmt = (
            update(PendingApprovalModel)
            .where(
                PendingApprovalModel.approval_id == approval_id,
                PendingApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(status=ApprovalStatus.EXECUTED.value, executed_at=now)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get(approval_id)

    async def mark_rejected(self, approval_id: str, reason: str | None = None) -> PendingApproval | None:
        stmt = (
            update(PendingApprovalModel)
            .where(
                PendingApprovalModel.approval_id == approval_id,
                PendingApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(status=ApprovalStatus.REJECTED.value, decision_reason=reason)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get(approval_id)

    async def mark_expired(self, approval_id: str) -> PendingApproval | None:
        stmt = (
            update(PendingApprovalModel)
            .where(
                PendingApprovalModel.approval_id == approval_id,
                PendingApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .values(status=ApprovalStatus.EXPIRED.value)
        )
        await self._session.execute(stmt)
        await self._session.flush()
        return await self.get(approval_id)

    async def list_pending_for_conversation(self, conversation_id: str) -> list[PendingApproval]:
        result = await self._session.execute(
            select(PendingApprovalModel)
            .where(
                PendingApprovalModel.conversation_id == conversation_id,
                PendingApprovalModel.status == ApprovalStatus.PENDING.value,
            )
            .order_by(PendingApprovalModel.created_at.desc())
        )
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models]

    @staticmethod
    def _to_domain(model: PendingApprovalModel | None) -> PendingApproval | None:
        if model is None:
            return None
        return PendingApproval(
            approval_id=model.approval_id,
            conversation_id=model.conversation_id,
            request_id=model.request_id,
            tool_id=model.tool_id,
            arguments=_deserialize_arguments(model.arguments),
            tool_call_id=model.tool_call_id,
            status=ApprovalStatus(model.status),
            created_at=model.created_at,
            expires_at=model.expires_at,
            executed_at=model.executed_at,
            decision_reason=model.decision_reason,
        )


def _serialize_arguments(arguments: dict[str, Any]) -> str | None:
    if not arguments:
        return None
    import json
    return json.dumps(arguments, ensure_ascii=False, default=str)


def _deserialize_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    import json
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
