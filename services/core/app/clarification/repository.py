"""
Repository — Pending Clarifications.

Data access layer for clarification sessions awaiting user response.
"""
import json
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clarification.models import (
    ClarificationAnswer,
    ClarificationOutcome,
    ClarificationSession,
    ClarificationStatus,
)
from app.information_sufficiency.models import ClarifyingQuestion
from app.db.models.pending_clarification import PendingClarificationModel


class ClarificationRepository:
    """Data access for pending clarification sessions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, session: ClarificationSession) -> ClarificationSession:
        model = PendingClarificationModel(
            clarification_id=session.session_id,
            conversation_id=session.conversation_id,
            task_id=session.task_id,
            plan_id=session.plan_id,
            request_id=session.request_id,
            correlation_id=session.correlation_id,
            user_id=session.user_id,
            actor_id=session.actor_id,
            session_token_id=session.session_token_id,
            round_number=session.round_number,
            max_rounds=session.max_rounds,
            questions=_serialize_questions(session.questions),
            status=session.status.value,
            created_at=session.created_at,
            expires_at=session.expires_at,
            answers=_serialize_answers(session.answers),
            re_evaluation_outcome=session.re_evaluation_outcome.value if session.re_evaluation_outcome else None,
            re_evaluation_questions=_serialize_questions(session.re_evaluation_questions),
            session_metadata=_serialize_metadata(session.metadata),
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_domain(model)

    async def get(self, clarification_id: str) -> ClarificationSession | None:
        result = await self._session.execute(
            select(PendingClarificationModel).where(
                PendingClarificationModel.clarification_id == clarification_id
            )
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_pending(self, clarification_id: str) -> ClarificationSession | None:
        result = await self._session.execute(
            select(PendingClarificationModel)
            .where(
                PendingClarificationModel.clarification_id == clarification_id,
                PendingClarificationModel.status == ClarificationStatus.PENDING.value,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def mark_responded(
        self,
        clarification_id: str,
        answers: list[ClarificationAnswer],
        responded_at: datetime,
    ) -> ClarificationSession | None:
        stmt = (
            update(PendingClarificationModel)
            .where(
                PendingClarificationModel.clarification_id == clarification_id,
                PendingClarificationModel.status == ClarificationStatus.PENDING.value,
            )
            .values(
                status=ClarificationStatus.RESPONDED.value,
                responded_at=responded_at,
                answers=_serialize_answers(answers),
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get(clarification_id)

    async def mark_expired(self, clarification_id: str) -> ClarificationSession | None:
        stmt = (
            update(PendingClarificationModel)
            .where(
                PendingClarificationModel.clarification_id == clarification_id,
                PendingClarificationModel.status == ClarificationStatus.PENDING.value,
            )
            .values(status=ClarificationStatus.EXPIRED.value)
        )
        await self._session.execute(stmt)
        await self._session.flush()
        return await self.get(clarification_id)

    async def mark_cancelled(self, clarification_id: str) -> ClarificationSession | None:
        stmt = (
            update(PendingClarificationModel)
            .where(
                PendingClarificationModel.clarification_id == clarification_id,
                PendingClarificationModel.status == ClarificationStatus.PENDING.value,
            )
            .values(status=ClarificationStatus.CANCELLED.value)
        )
        await self._session.execute(stmt)
        await self._session.flush()
        return await self.get(clarification_id)

    async def mark_satisfied(
        self,
        clarification_id: str,
        outcome: ClarificationOutcome,
        re_evaluation_questions: list[ClarifyingQuestion],
    ) -> ClarificationSession | None:
        stmt = (
            update(PendingClarificationModel)
            .where(
                PendingClarificationModel.clarification_id == clarification_id,
                PendingClarificationModel.status == ClarificationStatus.RESPONDED.value,
            )
            .values(
                status=ClarificationStatus.SATISFIED.value,
                re_evaluation_outcome=outcome.value,
                re_evaluation_questions=_serialize_questions(re_evaluation_questions),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
        return await self.get(clarification_id)

    async def advance_round(
        self,
        clarification_id: str,
        next_questions: list[ClarifyingQuestion],
        next_round: int,
    ) -> ClarificationSession | None:
        stmt = (
            update(PendingClarificationModel)
            .where(
                PendingClarificationModel.clarification_id == clarification_id,
                PendingClarificationModel.status == ClarificationStatus.RESPONDED.value,
                PendingClarificationModel.round_number < PendingClarificationModel.max_rounds,
            )
            .values(
                status=ClarificationStatus.PENDING.value,
                questions=_serialize_questions(next_questions),
                answers=None,
                round_number=next_round,
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        if res.rowcount == 0:
            return None
        return await self.get(clarification_id)

    async def list_pending_for_conversation(self, conversation_id: str) -> list[ClarificationSession]:
        result = await self._session.execute(
            select(PendingClarificationModel)
            .where(
                PendingClarificationModel.conversation_id == conversation_id,
                PendingClarificationModel.status == ClarificationStatus.PENDING.value,
            )
            .order_by(PendingClarificationModel.created_at.desc())
        )
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models]

    async def list_pending(self, limit: int = 50, offset: int = 0) -> tuple[list[ClarificationSession], int]:
        total_result = await self._session.execute(
            select(PendingClarificationModel).where(
                PendingClarificationModel.status == ClarificationStatus.PENDING.value
            )
        )
        total = len(total_result.scalars().all())

        result = await self._session.execute(
            select(PendingClarificationModel)
            .where(PendingClarificationModel.status == ClarificationStatus.PENDING.value)
            .order_by(PendingClarificationModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models], total

    async def count_pending(self) -> int:
        result = await self._session.execute(
            select(PendingClarificationModel).where(
                PendingClarificationModel.status == ClarificationStatus.PENDING.value
            )
        )
        return len(result.scalars().all())

    @staticmethod
    def _to_domain(model: PendingClarificationModel | None) -> ClarificationSession | None:
        if model is None:
            return None
        return ClarificationSession(
            session_id=model.clarification_id,
            conversation_id=model.conversation_id,
            task_id=model.task_id,
            plan_id=model.plan_id,
            request_id=model.request_id,
            correlation_id=model.correlation_id,
            user_id=model.user_id,
            actor_id=model.actor_id,
            session_token_id=model.session_token_id,
            round_number=model.round_number,
            max_rounds=model.max_rounds,
            questions=_deserialize_questions(model.questions),
            status=ClarificationStatus(model.status),
            created_at=model.created_at,
            expires_at=model.expires_at,
            responded_at=model.responded_at,
            answers=_deserialize_answers(model.answers),
            re_evaluation_outcome=ClarificationOutcome(model.re_evaluation_outcome) if model.re_evaluation_outcome else None,
            re_evaluation_questions=_deserialize_questions(model.re_evaluation_questions),
            metadata=_deserialize_metadata(model.session_metadata),
        )


def _serialize_questions(questions: list) -> str | None:
    if not questions:
        return None
    from app.information_sufficiency.models import ClarifyingQuestion
    return json.dumps(
        [q.model_dump() if hasattr(q, "model_dump") else q.dict() for q in questions],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _deserialize_questions(raw: str | None) -> list:
    if not raw:
        return []
    from app.information_sufficiency.models import ClarifyingQuestion
    data = json.loads(raw)
    return [ClarifyingQuestion(**item) for item in data]


def _serialize_answers(answers: list[ClarificationAnswer]) -> str | None:
    if not answers:
        return None
    return json.dumps(
        [a.model_dump() if hasattr(a, "model_dump") else a.dict() for a in answers],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _deserialize_answers(raw: str | None) -> list[ClarificationAnswer]:
    if not raw:
        return []
    data = json.loads(raw)
    return [ClarificationAnswer(**item) for item in data]


def _serialize_metadata(metadata: dict[str, Any]) -> str | None:
    if not metadata:
        return None
    return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))


def _deserialize_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupted metadata JSON in database for clarification: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"Metadata in database for clarification is not a dict (got {type(result).__name__})")
    return result
