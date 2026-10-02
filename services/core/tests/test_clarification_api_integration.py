"""
Stage 19.7 — Clarification API Integration Tests.

Tests the real API path for the clarification loop.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.ai.services.ai_service import AIService
from app.clarification.models import ClarificationAnswer, ClarificationOutcome, ClarificationStatus
from app.clarification.service import ClarificationService
from app.db.database import Base, get_db
from app.dependencies import get_ai_service
from app.identity.bootstrap import BootstrapSecret
from app.identity.session import SessionStore
from app.information_sufficiency.models import SufficiencyOutcome
from app.information_sufficiency.service import InformationSufficiencyService
from app.main import app
from app.services.settings_service import SettingsService


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with eng.begin() as conn:
        from app.db.models import settings, activity, conversation, pending_clarification  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def client(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autocommit=False)

    async def override_get_db():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    async with factory() as s:
        svc = SettingsService(s)
        await svc.ensure_defaults()
        await s.commit()

    test_session_store = SessionStore()
    test_bootstrap_secret = BootstrapSecret()
    app.state.session_store = test_session_store
    app.state.bootstrap_secret = test_bootstrap_secret

    test_session = await test_session_store.create(user_id="test_user")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ac.headers["Authorization"] = f"Bearer {test_session.session_id}"
        yield ac

    app.dependency_overrides.clear()
    app.state.session_store = None
    app.state.bootstrap_secret = None


def _make_mock_ai(sufficiency_outcome=SufficiencyOutcome.SUFFICIENT):
    """Create a mock AIService that returns the specified outcome."""
    mock_ai = AsyncMock(spec=AIService)

    mock_conversation = AsyncMock()
    mock_conversation.id = "conv1"
    mock_conversation.title = "Test Conversation"
    mock_conversation.provider = "ollama"
    mock_conversation.model = "qwen:test"
    mock_conversation.created_at = datetime.now(timezone.utc)
    mock_conversation.updated_at = datetime.now(timezone.utc)
    mock_conversation.messages = []

    mock_message = AsyncMock()
    mock_message.id = 1
    mock_message.conversation_id = "conv1"
    mock_message.role = "assistant"
    mock_message.content = "OK"
    mock_message.created_at = datetime.now(timezone.utc)

    async def chat_side_effect(*args, **kwargs):
        if sufficiency_outcome == SufficiencyOutcome.SUFFICIENT:
            return {
                "conversation": mock_conversation,
                "message": mock_message,
                "agent_loop": {"done": True},
            }
        return {
            "conversation": mock_conversation,
            "message": None,
            "clarification_required": True,
            "clarification_session_id": "clar_123",
            "clarification_questions": [
                {
                    "question_id": "q1",
                    "question": "What project?",
                    "reason": "Missing project",
                    "category": "REQUIRED",
                    "blocking": True,
                    "priority": 50,
                    "expected_answer_type": "text",
                }
            ],
        }

    mock_ai.chat.side_effect = chat_side_effect
    mock_ai._conversations = AsyncMock()
    mock_ai._conversations.get.return_value = mock_conversation
    return mock_ai


@pytest.mark.asyncio
async def test_insufficient_request_returns_clarification(client):
    """CASE A: insufficient request → clarification_required=true"""
    mock_ai = _make_mock_ai(sufficiency_outcome=SufficiencyOutcome.INSUFFICIENT)
    app.dependency_overrides[get_ai_service] = lambda: mock_ai

    try:
        response = await client.post("/api/ai/chat", json={"message": "Prepare deployment"})
        assert response.status_code == 200
        body = response.json()
        assert body.get("clarification_required") is True
        assert body.get("clarification_session_id") == "clar_123"
        assert len(body.get("clarification_questions", [])) == 1
    finally:
        app.dependency_overrides.pop(get_ai_service, None)


@pytest.mark.asyncio
async def test_sufficient_request_proceeds_normally(client):
    """CASE C: sufficient request → normal AI path"""
    mock_ai = _make_mock_ai(sufficiency_outcome=SufficiencyOutcome.SUFFICIENT)
    app.dependency_overrides[get_ai_service] = lambda: mock_ai

    try:
        response = await client.post("/api/ai/chat", json={"message": "Hello"})
        assert response.status_code == 200
        body = response.json()
        assert body.get("clarification_required") is not True
        assert body.get("message") is not None
    finally:
        app.dependency_overrides.pop(get_ai_service, None)


@pytest.mark.asyncio
async def test_clarification_failure_returns_error(client):
    """CASE D: clarification service failure → error response"""
    failing_svc = AsyncMock()
    failing_svc.list_pending_for_conversation.side_effect = Exception("DB down")

    ai = AIService(
        providers=AsyncMock(),
        conversations=AsyncMock(),
        activity=AsyncMock(),
        settings=AsyncMock(),
        tool_execution_service=AsyncMock(),
        approval_service=AsyncMock(),
        clarification_service=failing_svc,
        information_sufficiency_service=AsyncMock(),
    )
    app.dependency_overrides[get_ai_service] = lambda: ai

    try:
        response = await client.post("/api/ai/chat", json={"message": "Hello"})
        assert response.status_code == 409
        body = response.json()
        assert "clarification_lookup_failed" in body["detail"]["status"]
    finally:
        app.dependency_overrides.pop(get_ai_service, None)


@pytest.mark.asyncio
async def test_sufficiency_failure_returns_error(client):
    """CASE E: sufficiency service failure → error response"""
    failing_svc = AsyncMock()
    failing_svc.assess.side_effect = Exception("Evaluation failed")
    clarification_svc = AsyncMock()
    clarification_svc.list_pending_for_conversation.return_value = []

    ai = AIService(
        providers=AsyncMock(),
        conversations=AsyncMock(),
        activity=AsyncMock(),
        settings=AsyncMock(),
        tool_execution_service=AsyncMock(),
        approval_service=AsyncMock(),
        clarification_service=clarification_svc,
        information_sufficiency_service=failing_svc,
    )
    app.dependency_overrides[get_ai_service] = lambda: ai

    try:
        response = await client.post("/api/ai/chat", json={"message": "Hello"})
        assert response.status_code == 409
        body = response.json()
        assert "information_sufficiency_failed" in body["detail"]["status"]
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
