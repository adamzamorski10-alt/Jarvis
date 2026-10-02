"""
Clarification Loop — Service.

Application-owned service for creating, responding to, expiring, and
auditing pending clarification sessions.

SECURITY BOUNDARY:
  - This service does NOT grant permissions.
  - This service does NOT approve actions.
  - This service does NOT execute tools.
  - This service does NOT modify memory or task state directly.
  - Answers are untrusted input and do not automatically resolve gaps.
  - Information Sufficiency remains authoritative after every answer.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta
from typing import Any

from app.clarification.models import (
    ClarificationAnswer,
    ClarificationCancelRequest,
    ClarificationCreateRequest,
    ClarificationExpiredError,
    ClarificationNotFoundError,
    ClarificationOutcome,
    ClarificationRespondRequest,
    ClarificationResponse,
    ClarificationSession,
    ClarificationStateError,
    ClarificationStatus,
    ClarificationValidationError,
)
from app.clarification.repository import ClarificationRepository
from app.core.logging import get_logger
from app.events.schemas import EventType

DEFAULT_CLARIFICATION_TTL = timedelta(minutes=30)
MAX_CLARIFICATION_TTL = timedelta(hours=24)


class ClarificationService:
    """
    Application-owned service for managing clarification sessions.

    Enforces centralized state transitions and fail-closed semantics.
    """

    def __init__(
        self,
        repo: ClarificationRepository,
        ttl: timedelta = DEFAULT_CLARIFICATION_TTL,
        max_ttl: timedelta = MAX_CLARIFICATION_TTL,
        event_emitter: Any | None = None,
    ) -> None:
        self._repo = repo
        self._ttl = ttl
        self._max_ttl = max_ttl
        self._event_emitter = event_emitter
        self._logger = get_logger(__name__)

    async def _emit(self, event_type: Any, session: ClarificationSession, **extra: Any) -> None:
        if self._event_emitter is None:
            return
        try:
            payload = {
                "clarification_id": session.session_id,
                "conversation_id": session.conversation_id,
                "status": session.status.value,
                "round_number": session.round_number,
                **extra,
            }
            await self._event_emitter.emit(
                event_type=event_type,
                source="clarification",
                correlation_id=session.correlation_id or session.session_id,
                payload=payload,
            )
        except Exception as exc:
            self._logger.warning("Clarification event emission failed: %s", exc)

    async def create(self, request: ClarificationCreateRequest) -> ClarificationResponse:
        """
        Create a new PENDING clarification session.

        The session_id is server-generated and immutable.
        """
        now = datetime.now(timezone.utc)
        session_id = f"clar_{secrets.token_hex(16)}"

        ttl = request.ttl_seconds if request.ttl_seconds is not None else self._ttl.total_seconds()
        ttl = max(0.0, min(ttl, self._max_ttl.total_seconds()))
        expires_at = now + timedelta(seconds=ttl)

        session = ClarificationSession(
            session_id=session_id,
            conversation_id=request.conversation_id,
            task_id=request.task_id,
            plan_id=request.plan_id,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            user_id=request.user_id,
            actor_id=request.actor_id,
            session_token_id=request.session_token_id,
            questions=request.questions,
            status=ClarificationStatus.PENDING,
            created_at=now,
            expires_at=expires_at,
            metadata=request.metadata,
        )

        created = await self._repo.create(session)
        self._logger.info(
            "clarification.created",
            extra={
                "clarification_id": created.session_id,
                "conversation_id": created.conversation_id,
                "question_count": len(created.questions),
                "expires_at": created.expires_at.isoformat(),
            },
        )
        await self._emit(
            EventType.CLARIFICATION_REQUESTED,
            created,
            question_count=len(created.questions),
        )
        return ClarificationResponse(session=created, message="Clarification requested.")

    async def respond(
        self,
        request: ClarificationRespondRequest,
        user_id: str | None = None,
        actor_id: str | None = None,
        session_token_id: str | None = None,
    ) -> ClarificationResponse:
        """
        Respond to a pending clarification session.

        Transitions: PENDING → RESPONDED
        """
        existing = await self._repo.get(request.session_id)
        if existing is None:
            raise ClarificationNotFoundError(f"Clarification session not found: {request.session_id}")

        if existing.user_id is not None and user_id is not None and existing.user_id != user_id:
            raise ClarificationNotFoundError(f"Clarification session not found: {request.session_id}")
        if existing.actor_id is not None and actor_id is not None and existing.actor_id != actor_id:
            raise ClarificationNotFoundError(f"Clarification session not found: {request.session_id}")
        if existing.session_token_id is not None and session_token_id is not None and existing.session_token_id != session_token_id:
            raise ClarificationNotFoundError(f"Clarification session not found: {request.session_id}")

        if existing.status != ClarificationStatus.PENDING:
            raise ClarificationStateError(
                f"Clarification session is not pending (status={existing.status.value}): {request.session_id}"
            )

        if existing.is_expired():
            await self._repo.mark_expired(request.session_id)
            raise ClarificationExpiredError(f"Clarification session expired: {request.session_id}")

        answered_question_ids = {a.question_id for a in request.answers}
        expected_ids = {q.question_id for q in existing.questions}
        missing = expected_ids - answered_question_ids
        if missing:
            raise ClarificationValidationError(
                f"Missing answers for question IDs: {sorted(missing)}"
            )

        now = datetime.now(timezone.utc)
        updated = await self._repo.mark_responded(
            request.session_id,
            request.answers,
            responded_at=now,
        )
        if updated is None:
            raise ClarificationStateError(
                f"Failed to transition clarification to RESPONDED: {request.session_id}"
            )

        await self._emit(
            EventType.CLARIFICATION_RESPONDED,
            updated,
            answer_count=len(request.answers),
        )
        self._logger.info(
            "clarification.responded",
            extra={
                "clarification_id": updated.session_id,
                "conversation_id": updated.conversation_id,
                "answer_count": len(request.answers),
            },
        )
        return ClarificationResponse(session=updated, message="Response recorded.")

    async def cancel(self, request: ClarificationCancelRequest) -> ClarificationResponse:
        """
        Cancel a pending clarification session.

        Transitions: PENDING → CANCELLED
        """
        session = await self._repo.get(request.session_id)
        if session is None:
            raise ClarificationNotFoundError(f"Clarification session not found: {request.session_id}")

        if session.status != ClarificationStatus.PENDING:
            raise ClarificationStateError(
                f"Cannot cancel clarification in status {session.status.value}: {request.session_id}"
            )

        if session.is_expired():
            raise ClarificationExpiredError(f"Clarification session expired: {request.session_id}")

        updated = await self._repo.mark_cancelled(request.session_id)
        if updated is None:
            raise ClarificationStateError(
                f"Failed to cancel clarification: {request.session_id}"
            )

        await self._emit(
            EventType.CLARIFICATION_CANCELLED,
            updated,
            reason=request.reason,
        )
        self._logger.info(
            "clarification.cancelled",
            extra={
                "clarification_id": updated.session_id,
                "conversation_id": updated.conversation_id,
                "reason": request.reason,
            },
        )
        return ClarificationResponse(session=updated, message="Clarification cancelled.")

    async def expire(self, clarification_id: str) -> ClarificationSession | None:
        """
        Expire a pending clarification session.

        Transitions: PENDING → EXPIRED
        Returns None if the session is not PENDING.
        """
        session = await self._repo.get(clarification_id)
        if session is None:
            return None

        if session.status != ClarificationStatus.PENDING:
            return None

        if not session.is_expired():
            return None

        updated = await self._repo.mark_expired(clarification_id)
        if updated is None:
            return None

        await self._emit(
            EventType.CLARIFICATION_EXPIRED,
            updated,
        )
        self._logger.info(
            "clarification.expired",
            extra={
                "clarification_id": updated.session_id,
                "conversation_id": updated.conversation_id,
            },
        )
        return updated

    async def mark_satisfied(
        self,
        clarification_id: str,
        outcome: ClarificationOutcome,
        re_evaluation_questions: list,
    ) -> ClarificationSession | None:
        """
        Mark a responded clarification as satisfied.

        Transitions: RESPONDED → SATISFIED
        This is called by the pipeline after re-evaluating Information Sufficiency.
        """
        updated = await self._repo.mark_satisfied(
            clarification_id,
            outcome,
            re_evaluation_questions,
        )
        if updated is None:
            return None

        await self._emit(
            EventType.CLARIFICATION_SATISFIED,
            updated,
            outcome=outcome.value,
            remaining_questions=len(re_evaluation_questions),
        )
        self._logger.info(
            "clarification.satisfied",
            extra={
                "clarification_id": updated.session_id,
                "outcome": outcome.value,
                "remaining_questions": len(re_evaluation_questions),
            },
        )
        return updated

    async def advance_round(
        self,
        clarification_id: str,
        next_questions: list,
    ) -> ClarificationSession | None:
        """
        Advance a responded clarification to the next round.

        Transitions: RESPONDED → PENDING with incremented round_number.
        Returns None if the clarification cannot be advanced.
        """
        session = await self._repo.get(clarification_id)
        if session is None:
            return None
        if session.status != ClarificationStatus.RESPONDED:
            return None
        if session.round_number >= session.max_rounds:
            self._logger.warning(
                "clarification.max_rounds_reached",
                extra={"clarification_id": clarification_id, "round": session.round_number},
            )
            return None

        updated = await self._repo.advance_round(
            clarification_id=clarification_id,
            next_questions=next_questions,
            next_round=session.round_number + 1,
        )
        if updated is None:
            return None

        await self._emit(
            EventType.CLARIFICATION_RESPONDED,
            updated,
            round_advanced=True,
            next_round=updated.round_number,
        )
        self._logger.info(
            "clarification.round_advanced",
            extra={
                "clarification_id": updated.session_id,
                "round": updated.round_number,
                "max_rounds": updated.max_rounds,
            },
        )
        return updated

    async def get(self, clarification_id: str) -> ClarificationSession | None:
        """Get a clarification session by ID."""
        return await self._repo.get(clarification_id)

    async def list_pending_for_conversation(self, conversation_id: str) -> list[ClarificationSession]:
        """List pending clarification sessions for a conversation."""
        return await self._repo.list_pending_for_conversation(conversation_id)

    async def list_pending(self, limit: int = 50, offset: int = 0) -> tuple[list[ClarificationSession], int]:
        """List pending clarification sessions with pagination."""
        return await self._repo.list_pending(limit=limit, offset=offset)

    async def count_pending(self) -> int:
        """Count pending clarification sessions."""
        return await self._repo.count_pending()
