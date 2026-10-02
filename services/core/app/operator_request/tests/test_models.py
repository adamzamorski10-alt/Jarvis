"""
Operator Request — Model Tests.

Tests for OperatorRequest contract validation, bounds, and invariants.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.identity.models import ActorType, RequestContext
from app.operator_request.models import (
    InvalidOperatorRequestError,
    MalformedRequestError,
    OperatorRequest,
    OperatorRequestCreate,
    OperatorRequestState,
    OperatorResponseState,
    ReadinessState,
)


def _make_request_context() -> RequestContext:
    return RequestContext(
        user_id="test_user",
        actor_type=ActorType.USER,
        actor_id="test_actor",
        session_id="test_session",
    )


class TestOperatorRequestCreate:
    """Tests for OperatorRequestCreate validation."""

    def test_valid_create(self):
        create = OperatorRequestCreate(
            user_request="Hello, world!",
            request_context=_make_request_context(),
        )
        assert create.user_request == "Hello, world!"

    def test_empty_user_request_rejected(self):
        with pytest.raises(ValidationError):
            OperatorRequestCreate(
                user_request="",
                request_context=_make_request_context(),
            )

    def test_whitespace_user_request_rejected(self):
        with pytest.raises(ValidationError):
            OperatorRequestCreate(
                user_request="   ",
                request_context=_make_request_context(),
            )

    def test_oversized_user_request_rejected(self):
        with pytest.raises(ValidationError):
            OperatorRequestCreate(
                user_request="x" * 65_537,
                request_context=_make_request_context(),
            )


class TestOperatorRequest:
    """Tests for OperatorRequest validation."""

    def test_valid_request(self):
        req = OperatorRequest(
            request_id="req_abc123",
            user_request="Hello",
            correlation_id="corr_abc123",
            request_context=_make_request_context(),
        )
        assert req.request_id == "req_abc123"
        assert req.state == OperatorRequestState.RECEIVED
        assert req.readiness_state == ReadinessState.UNKNOWN

    def test_invalid_request_id_rejected(self):
        with pytest.raises(ValidationError):
            OperatorRequest(
                request_id="req abc!",  # invalid chars
                user_request="Hello",
                correlation_id="corr_abc123",
            )

    def test_invalid_operation_type_rejected(self):
        with pytest.raises(ValidationError):
            OperatorRequest(
                request_id="req_abc",
                user_request="Hello",
                operation_type="INVALID_TYPE",
                correlation_id="corr_abc",
            )

    def test_valid_extended_operation_types(self):
        valid_types = [
            "DECISION", "PLANNING", "CODING", "CHAT",
            "INFORMATION_REQUEST", "FILE_READ", "FILE_WRITE", "FILE_DELETE",
            "DEVELOPMENT", "TASK_CREATE", "TASK_UPDATE",
            "PLAN_CREATE", "PLAN_EXECUTE",
            "SCHEDULE_CREATE",
            "MEMORY_QUERY", "MEMORY_CREATE",
            "OTHER", "UNKNOWN",
        ]
        for op_type in valid_types:
            req = OperatorRequest(
                request_id="req_abc",
                user_request="Hello",
                operation_type=op_type,
                correlation_id="corr_abc",
            )
            assert req.operation_type == op_type

    def test_invalid_interaction_mode_rejected(self):
        with pytest.raises(ValidationError):
            OperatorRequest(
                request_id="req_abc",
                user_request="Hello",
                interaction_mode="invalid_mode",
                correlation_id="corr_abc",
            )

    def test_valid_interaction_modes(self):
        valid_modes = ["conversational", "task", "planning", "execution", "clarification"]
        for mode in valid_modes:
            req = OperatorRequest(
                request_id="req_abc",
                user_request="Hello",
                interaction_mode=mode,
                correlation_id="corr_abc",
            )
            assert req.interaction_mode == mode

    def test_classification_confidence_bounds(self):
        # Valid bounds
        req = OperatorRequest(
            request_id="req_abc",
            user_request="Hello",
            classification_confidence=0.0,
            correlation_id="corr_abc",
        )
        assert req.classification_confidence == 0.0

        req = OperatorRequest(
            request_id="req_abc",
            user_request="Hello",
            classification_confidence=1.0,
            correlation_id="corr_abc",
        )
        assert req.classification_confidence == 1.0

        # Out of bounds
        with pytest.raises(ValidationError):
            OperatorRequest(
                request_id="req_abc",
                user_request="Hello",
                classification_confidence=-0.1,
                correlation_id="corr_abc",
            )
        with pytest.raises(ValidationError):
            OperatorRequest(
                request_id="req_abc",
                user_request="Hello",
                classification_confidence=1.1,
                correlation_id="corr_abc",
            )

    def test_constraints_bounded(self):
        constraints = {f"key_{i}": f"value_{i}" for i in range(51)}
        with pytest.raises(ValidationError):
            OperatorRequest(
                request_id="req_abc",
                user_request="Hello",
                constraints=constraints,
                correlation_id="corr_abc",
            )

    def test_requested_capabilities_bounded(self):
        capabilities = [f"cap_{i}" for i in range(51)]
        with pytest.raises(ValidationError):
            OperatorRequest(
                request_id="req_abc",
                user_request="Hello",
                requested_capabilities=capabilities,
                correlation_id="corr_abc",
            )

    def test_frozen_model(self):
        req = OperatorRequest(
            request_id="req_abc",
            user_request="Hello",
            correlation_id="corr_abc",
        )
        with pytest.raises(ValidationError):
            req.request_id = "req_def"

    def test_state_transitions(self):
        req = OperatorRequest(
            request_id="req_abc",
            user_request="Hello",
            correlation_id="corr_abc",
        )
        assert req.state == OperatorRequestState.RECEIVED

        updated = req.model_copy(
            update={"state": OperatorRequestState.NORMALIZED}
        )
        assert updated.state == OperatorRequestState.NORMALIZED

    def test_response_states(self):
        for state in OperatorResponseState:
            assert isinstance(state.value, str)
            assert len(state.value) > 0


class TestReadinessState:
    """Tests for ReadinessState."""

    def test_all_states_present(self):
        assert ReadinessState.SUFFICIENT.value == "SUFFICIENT"
        assert ReadinessState.INSUFFICIENT.value == "INSUFFICIENT"
        assert ReadinessState.AMBIGUOUS.value == "AMBIGUOUS"
        assert ReadinessState.UNKNOWN.value == "UNKNOWN"

    def test_unknown_is_not_sufficient(self):
        assert ReadinessState.UNKNOWN != ReadinessState.SUFFICIENT


class TestOperatorRequestState:
    """Tests for OperatorRequestState."""

    def test_all_states_present(self):
        expected = {
            "RECEIVED", "NORMALIZED", "CLASSIFIED", "READY",
            "CLARIFICATION_REQUIRED", "BLOCKED", "REJECTED",
            "TASK_CREATED", "PLAN_CREATED", "APPROVAL_REQUIRED",
            "EXECUTION_STARTED", "EXECUTION_COMPLETED", "EXECUTION_FAILED",
            "COMPLETED", "FAILED",
        }
        actual = {s.value for s in OperatorRequestState}
        assert actual == expected


__all__ = [
    "TestOperatorRequestCreate",
    "TestOperatorRequest",
    "TestReadinessState",
    "TestOperatorRequestState",
]
