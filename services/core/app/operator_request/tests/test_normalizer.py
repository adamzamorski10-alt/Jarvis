"""
Operator Request — Normalizer Tests.

Tests for RequestNormalizer deterministic normalization.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.identity.models import ActorType, RequestContext
from app.operator_request.errors import NormalizationFailedError
from app.operator_request.models import OperatorRequest, OperatorRequestCreate, OperatorRequestState
from app.operator_request.normalizer import RequestNormalizer


def _make_request_context() -> RequestContext:
    return RequestContext(
        user_id="test_user",
        actor_type=ActorType.USER,
        actor_id="test_actor",
        session_id="test_session",
    )


class TestRequestNormalizer:
    """Tests for RequestNormalizer."""

    def setup_method(self):
        self.normalizer = RequestNormalizer()

    def test_same_client_message_id_produces_stable_request_id(self):
        first = self.normalizer.normalize(
            OperatorRequestCreate(
                user_request="Do it",
                message_id="msg_123",
                conversation_id="conv_123",
                request_context=_make_request_context(),
            )
        )
        second = self.normalizer.normalize(
            OperatorRequestCreate(
                user_request="Do it again",
                message_id="msg_123",
                conversation_id="conv_123",
                request_context=_make_request_context(),
            )
        )
        assert first.request_id == second.request_id

    def test_same_message_id_in_different_conversation_is_distinct(self):
        first = self.normalizer.normalize(
            OperatorRequestCreate(
                user_request="Do it",
                message_id="msg_123",
                conversation_id="conv_a",
                request_context=_make_request_context(),
            )
        )
        second = self.normalizer.normalize(
            OperatorRequestCreate(
                user_request="Do it",
                message_id="msg_123",
                conversation_id="conv_b",
                request_context=_make_request_context(),
            )
        )
        assert first.request_id != second.request_id

    def test_basic_normalization(self):
        create = OperatorRequestCreate(
            user_request="Hello, world!",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert result.user_request == "Hello, world!"
        assert result.normalized_request == "Hello, world!"
        assert result.state == OperatorRequestState.NORMALIZED

    def test_generates_request_id(self):
        create = OperatorRequestCreate(
            user_request="Hello",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert result.request_id.startswith("req_")
        assert len(result.request_id) > 4

    def test_generates_correlation_id(self):
        create = OperatorRequestCreate(
            user_request="Hello",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert result.correlation_id is not None
        assert result.correlation_id.startswith("corr_")

    def test_uses_existing_correlation_id(self):
        create = OperatorRequestCreate(
            user_request="Hello",
            correlation_id="corr_existing",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert result.correlation_id == "corr_existing"

    def test_uses_causation_id(self):
        create = OperatorRequestCreate(
            user_request="Hello",
            causation_id="causation_123",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert result.causation_id == "causation_123"

    def test_normalizes_unicode_whitespace(self):
        create = OperatorRequestCreate(
            user_request="Hello\u00a0\u00a0world",  # non-breaking spaces
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert "  " not in result.normalized_request

    def test_collapses_multiple_spaces(self):
        create = OperatorRequestCreate(
            user_request="Hello     world    !",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert "     " not in result.normalized_request

    def test_collapses_multiple_newlines(self):
        create = OperatorRequestCreate(
            user_request="Hello\n\n\n\nworld",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert "\n\n\n" not in result.normalized_request

    def test_strips_leading_trailing_whitespace(self):
        create = OperatorRequestCreate(
            user_request="   Hello, world!   ",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert result.normalized_request == "Hello, world!"

    def test_removes_control_characters(self):
        create = OperatorRequestCreate(
            user_request="Hello\x00\x01\x02world",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert "\x00" not in result.normalized_request
        assert "Hello" in result.normalized_request
        assert "world" in result.normalized_request

    def test_preserves_newlines_and_tabs(self):
        create = OperatorRequestCreate(
            user_request="Hello\n\tWorld",
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert "\n" in result.normalized_request
        # Tabs are collapsed to spaces (horizontal whitespace normalization)
        assert "\t" not in result.normalized_request
        assert "Hello" in result.normalized_request
        assert "World" in result.normalized_request

    def test_bounds_constraints(self):
        # OperatorRequestCreate already bounds at model level (max 50)
        constraints = {f"key_{i}": f"value_{i}" for i in range(60)}
        with pytest.raises(ValidationError):
            OperatorRequestCreate(
                user_request="Hello",
                constraints=constraints,
                request_context=_make_request_context(),
            )

    def test_bounds_metadata(self):
        # OperatorRequestCreate already bounds at model level (max 50)
        metadata = {f"key_{i}": f"value_{i}" for i in range(60)}
        with pytest.raises(ValidationError):
            OperatorRequestCreate(
                user_request="Hello",
                metadata=metadata,
                request_context=_make_request_context(),
            )

    def test_unique_request_ids(self):
        create1 = OperatorRequestCreate(
            user_request="Hello",
            request_context=_make_request_context(),
        )
        create2 = OperatorRequestCreate(
            user_request="World",
            request_context=_make_request_context(),
        )
        result1 = self.normalizer.normalize(create1)
        result2 = self.normalizer.normalize(create2)
        assert result1.request_id != result2.request_id

    def test_preserves_original_user_request(self):
        original = "   Hello   world   "
        create = OperatorRequestCreate(
            user_request=original,
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert result.user_request == original

    def test_normalized_differs_from_original(self):
        original = "   Hello   world   "
        create = OperatorRequestCreate(
            user_request=original,
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        assert result.normalized_request == "Hello world"
        assert result.user_request == original

    def test_nfc_unicode_normalization(self):
        # Café with combining accent
        original = "Cafe\u0301"  # e + combining acute accent
        create = OperatorRequestCreate(
            user_request=original,
            request_context=_make_request_context(),
        )
        result = self.normalizer.normalize(create)
        # NFC normalizes to single character: é
        assert "\u0301" not in result.normalized_request

    def test_empty_string_normalization(self):
        # Empty string is rejected at OperatorRequestCreate model level
        with pytest.raises(ValidationError):
            OperatorRequestCreate(
                user_request="",
                request_context=_make_request_context(),
            )


__all__ = ["TestRequestNormalizer"]
