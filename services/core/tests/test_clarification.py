"""
Clarification Loop — Service Tests.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

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
from app.clarification.service import ClarificationService
from app.db.database import Base
from app.information_sufficiency.models import (
    ClarifyingQuestion,
    MissingInformationCategory,
)


def _make_question(qid: str = "q1") -> ClarifyingQuestion:
    return ClarifyingQuestion(
        question_id=qid,
        question=f"Test question {qid}",
        reason="Missing information",
        category=MissingInformationCategory.REQUIRED,
        blocking=True,
        priority=50,
        expected_answer_type="text",
    )


def _make_create_request(
    conversation_id: str = "conv1",
    questions: list[ClarifyingQuestion] | None = None,
    ttl_seconds: float | None = None,
) -> ClarificationCreateRequest:
    if questions is None:
        questions = [_make_question()]
    return ClarificationCreateRequest(
        conversation_id=conversation_id,
        questions=questions,
        ttl_seconds=ttl_seconds,
    )


class TestClarificationServiceLifecycle:
    """Test core lifecycle transitions."""

    @pytest.fixture
    async def db_session(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            yield session
        await engine.dispose()

    @pytest.fixture
    async def repo(self, db_session: AsyncSession):
        return ClarificationRepository(db_session)

    @pytest.fixture
    async def service(self, repo: ClarificationRepository):
        return ClarificationService(repo)

    async def test_create_sets_pending_status(self, service: ClarificationService):
        resp = await service.create(_make_create_request())
        assert resp.session.status == ClarificationStatus.PENDING
        assert resp.session.session_id.startswith("clar_")
        assert resp.session.conversation_id == "conv1"
        assert len(resp.session.questions) == 1
        assert resp.session.answers == []

    async def test_create_with_custom_ttl(self, service: ClarificationService):
        resp = await service.create(_make_create_request(ttl_seconds=60))
        assert resp.session.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=65)
        assert resp.session.expires_at >= datetime.now(timezone.utc) + timedelta(seconds=55)

    async def test_create_uses_default_ttl_when_none(self, service: ClarificationService):
        resp = await service.create(_make_create_request(ttl_seconds=None))
        expected_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        assert resp.session.expires_at <= expected_expires + timedelta(seconds=5)
        assert resp.session.expires_at >= expected_expires - timedelta(seconds=5)

    async def test_respond_transitions_to_responded(self, service: ClarificationService):
        created = await service.create(_make_create_request())
        answers = [ClarificationAnswer(question_id="q1", answer="42")]
        resp = await service.respond(ClarificationRespondRequest(session_id=created.session.session_id, answers=answers))
        assert resp.session.status == ClarificationStatus.RESPONDED
        assert resp.session.responded_at is not None
        assert len(resp.session.answers) == 1
        assert resp.session.answers[0].answer == "42"

    async def test_respond_requires_all_questions(self, service: ClarificationService):
        questions = [_make_question("q1"), _make_question("q2")]
        created = await service.create(_make_create_request(questions=questions))
        answers = [ClarificationAnswer(question_id="q1", answer="yes")]
        with pytest.raises(ClarificationValidationError, match="Missing answers"):
            await service.respond(ClarificationRespondRequest(session_id=created.session.session_id, answers=answers))

    async def test_respond_rejects_duplicate_question_ids(self, service: ClarificationService):
        questions = [_make_question("q1")]
        created = await service.create(_make_create_request(questions=questions))
        answers = [
            ClarificationAnswer(question_id="q1", answer="a"),
            ClarificationAnswer(question_id="q1", answer="b"),
        ]
        with pytest.raises(Exception):  # Pydantic validation
            await service.respond(ClarificationRespondRequest(session_id=created.session.session_id, answers=answers))

    async def test_respond_to_missing_session_raises(self, service: ClarificationService):
        with pytest.raises(ClarificationNotFoundError):
            await service.respond(ClarificationRespondRequest(session_id="clar_nonexistent", answers=[ClarificationAnswer(question_id="q1", answer="x")]))

    async def test_respond_after_expire_raises(self, service: ClarificationService):
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        questions = [_make_question("q1")]
        session = ClarificationSession(
            session_id="clar_expired",
            conversation_id="conv1",
            questions=questions,
            status=ClarificationStatus.PENDING,
            created_at=past - timedelta(seconds=10),
            expires_at=past,
        )
        await service._repo.create(session)
        with pytest.raises(ClarificationExpiredError):
            await service.respond(ClarificationRespondRequest(session_id="clar_expired", answers=[ClarificationAnswer(question_id="q1", answer="x")]))

    async def test_cancel_transitions_to_cancelled(self, service: ClarificationService):
        created = await service.create(_make_create_request())
        resp = await service.cancel(ClarificationCancelRequest(session_id=created.session.session_id))
        assert resp.session.status == ClarificationStatus.CANCELLED

    async def test_cancel_non_pending_raises(self, service: ClarificationService):
        created = await service.create(_make_create_request())
        await service.respond(ClarificationRespondRequest(session_id=created.session.session_id, answers=[ClarificationAnswer(question_id="q1", answer="x")]))
        with pytest.raises(ClarificationStateError):
            await service.cancel(ClarificationCancelRequest(session_id=created.session.session_id))

    async def test_expire_marks_expired(self, service: ClarificationService):
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        questions = [_make_question("q1")]
        session = ClarificationSession(
            session_id="clar_expire",
            conversation_id="conv1",
            questions=questions,
            status=ClarificationStatus.PENDING,
            created_at=past - timedelta(seconds=10),
            expires_at=past,
        )
        await service._repo.create(session)
        result = await service.expire("clar_expire")
        assert result is not None
        assert result.status == ClarificationStatus.EXPIRED

    async def test_mark_satisfied_transitions(self, service: ClarificationService):
        created = await service.create(_make_create_request())
        await service.respond(ClarificationRespondRequest(session_id=created.session.session_id, answers=[ClarificationAnswer(question_id="q1", answer="x")]))
        result = await service.mark_satisfied(created.session.session_id, ClarificationOutcome.SUFFICIENT, [])
        assert result is not None
        assert result.status == ClarificationStatus.SATISFIED
        assert result.re_evaluation_outcome == ClarificationOutcome.SUFFICIENT

    async def test_get_returns_session(self, service: ClarificationService):
        created = await service.create(_make_create_request())
        fetched = await service.get(created.session.session_id)
        assert fetched is not None
        assert fetched.session_id == created.session.session_id

    async def test_list_pending_for_conversation(self, service: ClarificationService):
        await service.create(_make_create_request(conversation_id="conv_a"))
        await service.create(_make_create_request(conversation_id="conv_b"))
        pending_a = await service.list_pending_for_conversation("conv_a")
        assert len(pending_a) == 1
        assert pending_a[0].conversation_id == "conv_a"


class TestClarificationServiceIntegration:
    """Integration tests with Information Sufficiency."""

    @pytest.fixture
    async def db_session(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            yield session
        await engine.dispose()

    @pytest.fixture
    async def repo(self, db_session: AsyncSession):
        return ClarificationRepository(db_session)

    @pytest.fixture
    async def service(self, repo: ClarificationRepository):
        return ClarificationService(repo)

    async def test_full_create_respond_satisfy_flow(self, service: ClarificationService):
        questions = [
            _make_question("q1"),
            _make_question("q2"),
        ]
        created = await service.create(_make_create_request(questions=questions))
        assert created.session.status == ClarificationStatus.PENDING

        answers = [
            ClarificationAnswer(question_id="q1", answer="project foo"),
            ClarificationAnswer(question_id="q2", answer="main"),
        ]
        responded = await service.respond(ClarificationRespondRequest(session_id=created.session.session_id, answers=answers))
        assert responded.session.status == ClarificationStatus.RESPONDED
        assert responded.session.answers == answers

        satisfied = await service.mark_satisfied(
            responded.session.session_id,
            ClarificationOutcome.INSUFFICIENT,
            [_make_question("q3")],
        )
        assert satisfied.status == ClarificationStatus.SATISFIED
        assert satisfied.re_evaluation_outcome == ClarificationOutcome.INSUFFICIENT
        assert len(satisfied.re_evaluation_questions) == 1


class TestClarificationModels:
    """Pydantic model validation tests."""

    def test_session_requires_expires_at(self):
        questions = [_make_question("q1")]
        with pytest.raises(ValueError, match="validation error"):
            ClarificationSession(
                session_id="clar_1",
                conversation_id="conv1",
                questions=questions,
                status=ClarificationStatus.PENDING,
                created_at=datetime.now(timezone.utc),
            )

    def test_session_rejects_empty_question_list(self):
        with pytest.raises(ValueError):
            ClarificationSession(
                session_id="clar_1",
                conversation_id="conv1",
                questions=[],
                status=ClarificationStatus.PENDING,
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )

    def test_answer_rejects_empty_answer(self):
        with pytest.raises(ValueError):
            ClarificationAnswer(question_id="q1", answer="   ")

    def test_create_request_rejects_empty_conversation_id(self):
        questions = [_make_question("q1")]
        with pytest.raises(ValueError):
            ClarificationCreateRequest(conversation_id="   ", questions=questions)

    def test_respond_request_rejects_empty_answers(self):
        with pytest.raises(ValueError):
            ClarificationRespondRequest(session_id="clar_1", answers=[])
