"""
Operator Request — Classifier Tests.

Tests for OperationClassifier deterministic and AI-assisted classification.
"""
from __future__ import annotations

import pytest

from app.identity.models import ActorType, RequestContext
from app.operator_request.classifier import OperationClassifier
from app.operator_request.errors import ClassificationFailedError
from app.operator_request.models import (
    OperatorRequest,
    OperatorRequestState,
    ReadinessState,
)


def _make_request_context() -> RequestContext:
    return RequestContext(
        user_id="test_user",
        actor_type=ActorType.USER,
        actor_id="test_actor",
        session_id="test_session",
    )


def _make_request(text: str) -> OperatorRequest:
    return OperatorRequest(
        request_id="req_test",
        user_request=text,
        normalized_request=text.strip().lower(),
        correlation_id="corr_test",
        request_context=_make_request_context(),
    )


class TestOperationClassifier:
    """Tests for OperationClassifier."""

    def setup_method(self):
        self.classifier = OperationClassifier()

    # ── Deterministic Classification ───────────────────────────────────────────

    def test_file_read_classification(self):
        req = _make_request("Read the file config.json")
        result = self.classifier.classify(req)
        assert result.operation_type == "FILE_READ"
        assert result.state == OperatorRequestState.CLASSIFIED
        assert result.classification_confidence >= 0.8

    def test_file_write_classification(self):
        req = _make_request("Write a new file called output.txt")
        result = self.classifier.classify(req)
        assert result.operation_type == "FILE_WRITE"

    def test_file_delete_classification(self):
        req = _make_request("Delete the file temp.txt")
        result = self.classifier.classify(req)
        assert result.operation_type == "FILE_DELETE"

    def test_information_request_classification(self):
        req = _make_request("What is the capital of France?")
        result = self.classifier.classify(req)
        assert result.operation_type == "INFORMATION_REQUEST"

    def test_information_request_question_mark(self):
        req = _make_request("Tell me about this codebase.")
        result = self.classifier.classify(req)
        assert result.operation_type == "INFORMATION_REQUEST"

    def test_task_create_classification(self):
        req = _make_request("Create a new task to fix the bug")
        result = self.classifier.classify(req)
        assert result.operation_type == "TASK_CREATE"

    def test_plan_create_classification(self):
        req = _make_request("Create a plan for the migration")
        result = self.classifier.classify(req)
        assert result.operation_type == "PLAN_CREATE"

    def test_plan_execute_classification(self):
        req = _make_request("Execute the migration plan")
        result = self.classifier.classify(req)
        assert result.operation_type == "PLAN_EXECUTE"

    def test_memory_query_classification(self):
        req = _make_request("What do you remember about my preferences?")
        result = self.classifier.classify(req)
        assert result.operation_type == "MEMORY_QUERY"

    def test_memory_create_classification(self):
        req = _make_request("Remember that I prefer dark mode")
        result = self.classifier.classify(req)
        assert result.operation_type == "MEMORY_CREATE"

    def test_schedule_create_classification(self):
        req = _make_request("Schedule a meeting for tomorrow")
        result = self.classifier.classify(req)
        assert result.operation_type == "SCHEDULE_CREATE"

    def test_development_pattern_classification(self):
        req = _make_request("Implement a new authentication system")
        result = self.classifier.classify(req)
        assert result.operation_type == "DEVELOPMENT"

    def test_development_pattern_fix_bug(self):
        req = _make_request("Fix the bug in the login module")
        result = self.classifier.classify(req)
        assert result.operation_type == "DEVELOPMENT"

    def test_chat_default_classification(self):
        req = _make_request("Hello, how are you today?")
        result = self.classifier.classify(req)
        # Should classify as INFORMATION_REQUEST or CHAT
        assert result.operation_type in {"INFORMATION_REQUEST", "CHAT", "UNKNOWN"}

    def test_unknown_classification_fallback(self):
        req = _make_request("xyznonexistentpattern123")
        result = self.classifier.classify(req)
        assert result.operation_type == "UNKNOWN"
        assert result.classification_confidence == 0.0

    # ── AI-Assisted Classification ──────────────────────────────────────────────

    def test_ai_assisted_classification(self):
        """AI classifier is called when deterministic confidence < 0.8."""
        calls = []

        def mock_ai_classifier(user_request: str, normalized_request: str, constraints: dict) -> dict:
            calls.append(user_request)
            return {"operation_type": "DEVELOPMENT", "confidence": 0.9, "reason": "ai"}

        classifier = OperationClassifier(ai_classifier=mock_ai_classifier)
        req = _make_request("Some ambiguous request that needs AI help")
        result = classifier.classify(req)
        assert result.operation_type == "DEVELOPMENT"
        assert result.classification_confidence == 0.9
        assert len(calls) == 1

    def test_ai_classifier_invalid_type_rejected(self):
        """AI-proposed invalid operation types are rejected."""

        def mock_ai_classifier(user_request: str, normalized_request: str, constraints: dict) -> dict:
            return {"operation_type": "INVALID_TYPE", "confidence": 0.9, "reason": "ai"}

        classifier = OperationClassifier(ai_classifier=mock_ai_classifier)
        req = _make_request("Some request")
        result = classifier.classify(req)
        # Should fall back to UNKNOWN since AI type is invalid
        assert result.operation_type == "UNKNOWN"

    def test_ai_classifier_none_result(self):
        """AI classifier returning None falls back gracefully."""

        def mock_ai_classifier(user_request: str, normalized_request: str, constraints: dict) -> dict:
            return None

        classifier = OperationClassifier(ai_classifier=mock_ai_classifier)
        req = _make_request("Some request")
        result = classifier.classify(req)
        assert result.operation_type == "UNKNOWN"

    def test_ai_classifier_exception_falls_through(self):
        """AI classifier exceptions fall through to default."""

        def mock_ai_classifier(user_request: str, normalized_request: str, constraints: dict) -> dict:
            raise RuntimeError("AI failed")

        classifier = OperationClassifier(ai_classifier=mock_ai_classifier)
        req = _make_request("Some request")
        result = classifier.classify(req)
        assert result.operation_type == "UNKNOWN"

    # ── Interaction Mode Mapping ────────────────────────────────────────────────

    def test_execution_mode_for_file_write(self):
        req = _make_request("Write a file")
        result = self.classifier.classify(req)
        assert result.interaction_mode == "execution"

    def test_execution_mode_for_development(self):
        req = _make_request("Implement a feature")
        result = self.classifier.classify(req)
        assert result.interaction_mode == "execution"

    def test_planning_mode_for_planning(self):
        req = _make_request("Create a plan")
        result = self.classifier.classify(req)
        assert result.interaction_mode == "planning"

    def test_conversational_mode_for_chat(self):
        req = _make_request("Hello there")
        result = self.classifier.classify(req)
        assert result.interaction_mode == "conversational"

    # ── Classification Metadata ─────────────────────────────────────────────────

    def test_classification_reason_set(self):
        req = _make_request("Read the file config.json")
        result = self.classifier.classify(req)
        assert result.classification_reason is not None
        assert len(result.classification_reason) > 0

    def test_classification_confidence_set(self):
        req = _make_request("Read the file config.json")
        result = self.classifier.classify(req)
        assert result.classification_confidence is not None
        assert 0.0 <= result.classification_confidence <= 1.0

    # ── State Transitions ───────────────────────────────────────────────────────

    def test_initial_state_is_received(self):
        req = OperatorRequest(
            request_id="req_test",
            user_request="Hello",
            correlation_id="corr_test",
            request_context=_make_request_context(),
        )
        assert req.state.value == "RECEIVED"

    def test_classified_state_after_classification(self):
        req = _make_request("Read the file config.json")
        result = self.classifier.classify(req)
        assert result.state == OperatorRequestState.CLASSIFIED


__all__ = ["TestOperationClassifier"]
