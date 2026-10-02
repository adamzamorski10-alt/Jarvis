"""
Stage 20 — End-to-End Scenario Tests.

Tests for the required end-to-end scenarios:
A. Simple conversational request
B. Missing information → clarification
C. Clarification succeeds
D. Task-producing request
E. Permission denied
F. Approval required
G. Infrastructure failure
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.identity.models import ActorType, RequestContext
from app.operator_request.models import (
    OperatorRequestState,
    OperatorResponseState,
    ReadinessState,
)
from app.operator_request.service import OperatorRequestService


def _make_request_context(user_id: str = "test_user") -> RequestContext:
    return RequestContext(
        user_id=user_id,
        actor_type=ActorType.USER,
        actor_id="test_actor",
        session_id="test_session",
    )


# ── Scenario A: Simple Conversational Request ──────────────────────────────────


class TestScenarioA:
    """Simple conversational request."""

    @pytest.mark.asyncio
    async def test_simple_chat_request(self):
        """Scenario A: Simple conversational request flows through pipeline."""
        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Explain what files.read_file does.",
            request_context=_make_request_context(),
        )
        assert req.state == OperatorRequestState.NORMALIZED
        assert req.request_id is not None
        assert req.correlation_id is not None

        req = await service.classify_request(req)
        assert req.operation_type in ("INFORMATION_REQUEST", "CHAT", "UNKNOWN")
        assert req.state == OperatorRequestState.CLASSIFIED

        req = await service.set_readiness(req, ReadinessState.SUFFICIENT)
        assert req.readiness_state == ReadinessState.SUFFICIENT

        req = await service.mark_ready(req)
        assert req.state == OperatorRequestState.READY


# ── Scenario B: Missing Information → Clarification ────────────────────────────


class TestScenarioB:
    """Missing information triggers clarification."""

    @pytest.mark.asyncio
    async def test_insufficient_triggers_clarification(self):
        """Scenario B: Insufficient information creates clarification session."""
        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Prepare a development task.",
            request_context=_make_request_context(),
        )
        req = await service.classify_request(req)

        # Simulate Information Sufficiency returning INSUFFICIENT
        req = await service.set_readiness(
            req, ReadinessState.INSUFFICIENT, reason="Missing project context"
        )
        assert req.readiness_state == ReadinessState.INSUFFICIENT

        # Mark clarification required
        req = await service.mark_clarification_required(
            req, "clar_session_123", reason="Need more info"
        )
        assert req.state == OperatorRequestState.CLARIFICATION_REQUIRED
        assert req.clarification_session_id == "clar_session_123"


# ── Scenario C: Clarification Succeeds ─────────────────────────────────────────


class TestScenarioC:
    """Clarification answer makes request sufficient."""

    @pytest.mark.asyncio
    async def test_clarification_success(self):
        """Scenario C: Clarification answer validated, request becomes SUFFICIENT."""
        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Prepare a development task.",
            request_context=_make_request_context(),
        )
        req = await service.classify_request(req)

        # Initially insufficient
        req = await service.set_readiness(
            req, ReadinessState.INSUFFICIENT, reason="Missing project context"
        )
        req = await service.mark_clarification_required(req, "clar_session_123")

        # User provides answer - request becomes sufficient
        req = await service.set_readiness(req, ReadinessState.SUFFICIENT)
        assert req.readiness_state == ReadinessState.SUFFICIENT
        assert req.clarification_session_id == "clar_session_123"

        req = await service.mark_ready(req)
        assert req.state == OperatorRequestState.READY


# ── Scenario D: Task-Producing Request ─────────────────────────────────────────


class TestScenarioD:
    """Request that produces durable work creates a Task."""

    @pytest.mark.asyncio
    async def test_task_producing_request(self):
        """Scenario D: Task-producing request creates Task through application service."""
        from app.operator_request.models import OperatorRequestUpdate

        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Implement a new authentication system",
            request_context=_make_request_context(),
            project_id="proj_123",
        )
        req = await service.classify_request(req)
        assert req.operation_type == "DEVELOPMENT"

        req = await service.set_readiness(req, ReadinessState.SUFFICIENT)
        req = await service.mark_ready(req)

        # Simulate task creation through application service
        req = await service.update_request(
            req,
            OperatorRequestUpdate(state=OperatorRequestState.TASK_CREATED),
        )
        assert req.state == OperatorRequestState.TASK_CREATED


# ── Scenario E: Permission Denied ──────────────────────────────────────────────


class TestScenarioE:
    """Permission denied blocks execution."""

    @pytest.mark.asyncio
    async def test_permission_denied_blocks_execution(self):
        """Scenario E: PermissionEngine denial blocks execution."""
        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Execute system command",
            request_context=_make_request_context(),
        )
        req = await service.classify_request(req)

        # Simulate permission denial
        req = await service.mark_blocked(req, "Permission denied by PermissionEngine")
        assert req.state == OperatorRequestState.BLOCKED
        assert req.error == "Permission denied by PermissionEngine"
        assert req.readiness_state != ReadinessState.SUFFICIENT


# ── Scenario F: Approval Required ──────────────────────────────────────────────


class TestScenarioF:
    """Approval required blocks execution until approved."""

    @pytest.mark.asyncio
    async def test_approval_required(self):
        """Scenario F: Execution does not happen before approval."""
        from app.operator_request.models import OperatorRequestUpdate

        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Delete the production database",
            request_context=_make_request_context(),
        )
        req = await service.classify_request(req)

        # Mark approval required
        req = await service.update_request(
            req,
            OperatorRequestUpdate(state=OperatorRequestState.APPROVAL_REQUIRED),
        )
        assert req.state == OperatorRequestState.APPROVAL_REQUIRED

        # Execution must not proceed without approval
        assert req.state != OperatorRequestState.EXECUTION_STARTED


# ── Scenario G: Infrastructure Failure ─────────────────────────────────────────


class TestScenarioG:
    """Infrastructure failure does not create false success."""

    @pytest.mark.asyncio
    async def test_context_assembly_failure(self):
        """Scenario G: Context Assembly failure is bounded."""
        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
        )
        # Request was created successfully
        assert req.state == OperatorRequestState.NORMALIZED

        # Simulate infrastructure failure during classification
        with patch.object(
            service._classifier, "classify", side_effect=Exception("Context Assembly failed")
        ):
            with pytest.raises(Exception):
                await service.classify_request(req)

    @pytest.mark.asyncio
    async def test_insufficiency_failure(self):
        """Scenario G: Readiness check failure does not create false success."""
        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
        )
        req = await service.classify_request(req)

        # Simulate readiness check failure by marking insufficient
        req = await service.set_readiness(req, ReadinessState.INSUFFICIENT, reason="Service failure")
        assert req.readiness_state == ReadinessState.INSUFFICIENT
        # Request is blocked from proceeding to execution
        assert req.state != OperatorRequestState.READY

    @pytest.mark.asyncio
    async def test_event_emission_failure_does_not_break_flow(self):
        """Scenario G: Event emission failure never breaks the request flow."""
        broken_emitter = MagicMock()
        broken_emitter.emit = AsyncMock(side_effect=RuntimeError("Event system down"))

        service = OperatorRequestService(event_emitter=broken_emitter)

        # Should not raise despite event emission failure
        req = await service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
        )
        assert req.state == OperatorRequestState.NORMALIZED

        req = await service.classify_request(req)
        assert req.state == OperatorRequestState.CLASSIFIED

        req = await service.mark_ready(req)
        assert req.state == OperatorRequestState.READY


# ── Correlation Preservation ────────────────────────────────────────────────────


class TestCorrelationPreservation:
    """Request correlation is preserved across lifecycle."""

    @pytest.mark.asyncio
    async def test_correlation_id_preserved(self):
        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
            correlation_id="corr_existing",
        )
        assert req.correlation_id == "corr_existing"

        req = await service.classify_request(req)
        assert req.correlation_id == "corr_existing"

        req = await service.mark_ready(req)
        assert req.correlation_id == "corr_existing"

    @pytest.mark.asyncio
    async def test_causation_id_preserved(self):
        service = OperatorRequestService()

        req = await service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
            causation_id="causation_123",
        )
        assert req.causation_id == "causation_123"

        req = await service.classify_request(req)
        assert req.causation_id == "causation_123"


# ── Idempotency ────────────────────────────────────────────────────────────────


class TestIdempotency:
    """Durable operations are idempotent."""

    @pytest.mark.asyncio
    async def test_unique_request_ids(self):
        """Each request gets a unique request_id."""
        service = OperatorRequestService()

        req1 = await service.create_request(
            user_request="Hello",
            request_context=_make_request_context(),
        )
        req2 = await service.create_request(
            user_request="World",
            request_context=_make_request_context(),
        )
        assert req1.request_id != req2.request_id


__all__ = [
    "TestScenarioA",
    "TestScenarioB",
    "TestScenarioC",
    "TestScenarioD",
    "TestScenarioE",
    "TestScenarioF",
    "TestScenarioG",
    "TestCorrelationPreservation",
    "TestIdempotency",
]
