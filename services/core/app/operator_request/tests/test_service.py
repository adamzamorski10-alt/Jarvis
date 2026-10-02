"""
Operator Request — Service Tests.

Tests for OperatorRequestService lifecycle management.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.identity.models import ActorType, RequestContext
from app.operator_request.models import (
    OperatorRequest,
    OperatorRequestCreate,
    OperatorRequestState,
    OperatorRequestUpdate,
    OperatorResponseState,
    ReadinessState,
)
from app.operator_request.service import OperatorRequestService


def _make_request_context() -> RequestContext:
    return RequestContext(
        user_id="test_user",
        actor_type=ActorType.USER,
        actor_id="test_actor",
        session_id="test_session",
    )


class TestOperatorRequestService:
    """Tests for OperatorRequestService."""

    def setup_method(self):
        self.event_emitter = MagicMock()
        self.event_emitter.emit = AsyncMock()
        self.service = OperatorRequestService(
            event_emitter=self.event_emitter,
        )

    # ── Create Request ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_request_basic(self):
        req = await self.service.create_request(
            user_request="Hello, world!",
            request_context=_make_request_context(),
        )
        assert req.user_request == "Hello, world!"
        assert req.state == OperatorRequestState.NORMALIZED
        assert req.correlation_id is not None
        assert req.request_id is not None
        assert req.readiness_state == ReadinessState.UNKNOWN

    @pytest.mark.asyncio
    async def test_create_request_with_context(self):
        req = await self.service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
            conversation_id="conv_123",
            message_id="msg_456",
            project_id="proj_789",
            task_id="task_abc",
        )
        assert req.conversation_id == "conv_123"
        assert req.message_id == "msg_456"
        assert req.project_id == "proj_789"
        assert req.task_id == "task_abc"

    @pytest.mark.asyncio
    async def test_create_request_uses_correlation_id(self):
        req = await self.service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
            correlation_id="corr_existing",
        )
        assert req.correlation_id == "corr_existing"

    @pytest.mark.asyncio
    async def test_create_request_uses_causation_id(self):
        req = await self.service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
            causation_id="causation_123",
        )
        assert req.causation_id == "causation_123"

    @pytest.mark.asyncio
    async def test_create_request_requires_context(self):
        with pytest.raises(Exception):
            await self.service.create_request(
                user_request="Hello",
                request_context=None,
            )

    @pytest.mark.asyncio
    async def test_create_request_emits_event(self):
        req = await self.service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
        )
        self.event_emitter.emit.assert_called_once()
        call_args = self.event_emitter.emit.call_args
        assert call_args.kwargs["event_type"] == "operator.request.received"
        assert call_args.kwargs["request_id"] == req.request_id
        assert call_args.kwargs["correlation_id"] == req.correlation_id

    # ── Classify Request ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_classify_request(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Read the file config.json",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        result = await self.service.classify_request(req)
        assert result.operation_type == "FILE_READ"
        assert result.state == OperatorRequestState.CLASSIFIED
        self.event_emitter.emit.assert_called()

    # ── Update Request ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_update_request(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        update = OperatorRequestUpdate(
            state=OperatorRequestState.READY,
            readiness_state=ReadinessState.SUFFICIENT,
        )
        result = await self.service.update_request(req, update)
        assert result.state == OperatorRequestState.READY
        assert result.readiness_state == ReadinessState.SUFFICIENT

    @pytest.mark.asyncio
    async def test_update_request_noop(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        update = OperatorRequestUpdate()  # no fields set
        result = await self.service.update_request(req, update)
        assert result.request_id == req.request_id

    # ── Readiness State Management ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_set_readiness_sufficient(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        result = await self.service.set_readiness(req, ReadinessState.SUFFICIENT)
        assert result.readiness_state == ReadinessState.SUFFICIENT

    @pytest.mark.asyncio
    async def test_set_readiness_insufficient_with_reason(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        result = await self.service.set_readiness(
            req, ReadinessState.INSUFFICIENT, reason="Missing project context"
        )
        assert result.readiness_state == ReadinessState.INSUFFICIENT
        assert result.readiness_reason == "Missing project context"

    @pytest.mark.asyncio
    async def test_set_readiness_ambiguous(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        result = await self.service.set_readiness(req, ReadinessState.AMBIGUOUS)
        assert result.readiness_state == ReadinessState.AMBIGUOUS

    # ── Clarification Management ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_mark_clarification_required(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        result = await self.service.mark_clarification_required(
            req, "clar_session_123", reason="Need more info"
        )
        assert result.state == OperatorRequestState.CLARIFICATION_REQUIRED
        assert result.readiness_state == ReadinessState.INSUFFICIENT
        assert result.clarification_session_id == "clar_session_123"
        assert result.readiness_reason == "Need more info"
        self.event_emitter.emit.assert_called()

    # ── Ready State ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_mark_ready(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        result = await self.service.mark_ready(req)
        assert result.state == OperatorRequestState.READY
        assert result.readiness_state == ReadinessState.SUFFICIENT

    # ── Blocked / Rejected ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_mark_blocked(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        result = await self.service.mark_blocked(req, "Permission denied")
        assert result.state == OperatorRequestState.BLOCKED
        assert result.error == "Permission denied"

    @pytest.mark.asyncio
    async def test_mark_rejected(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        result = await self.service.mark_rejected(req, "Not supported")
        assert result.state == OperatorRequestState.REJECTED
        assert result.error == "Not supported"

    # ── Event Emission ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_events_never_raise(self):
        """Event emission failures must never break the flow."""
        self.event_emitter.emit = AsyncMock(side_effect=RuntimeError("Event failed"))
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        # Should not raise
        result = await self.service.mark_ready(req)
        assert result.state == OperatorRequestState.READY


__all__ = ["TestOperatorRequestService"]
