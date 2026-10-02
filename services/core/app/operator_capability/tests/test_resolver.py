"""
Operator Capability — Resolver Tests.

Tests for CapabilityResolver deterministic mapping.
"""
from __future__ import annotations

import pytest

from app.identity.models import ActorType, RequestContext
from app.operator_capability.models import CapabilityKind, CapabilityResolution
from app.operator_capability.resolver import CapabilityResolver
from app.operator_request.models import OperatorRequest, OperatorRequestState, ReadinessState


def _make_request(operation_type: str) -> OperatorRequest:
    return OperatorRequest(
        request_id="req_test",
        user_request="Test request",
        operation_type=operation_type,
        correlation_id="corr_test",
        request_context=RequestContext(
            user_id="test_user",
            actor_type=ActorType.USER,
            actor_id="test_actor",
            session_id="test_session",
        ),
    )


class TestCapabilityResolver:
    """Tests for CapabilityResolver."""

    def setup_method(self):
        self.resolver = CapabilityResolver()

    # ── Tool-backed operations ─────────────────────────────────────────────────

    def test_information_request_maps_to_system_get_time(self):
        resolution = self.resolver.resolve(_make_request("INFORMATION_REQUEST"))
        assert resolution.capability_kind == CapabilityKind.TOOL
        assert resolution.capability_id == "system.get_time"
        assert resolution.requires_permission is True
        assert resolution.requires_approval is False

    def test_file_read_maps_to_files_read_file(self):
        resolution = self.resolver.resolve(_make_request("FILE_READ"))
        assert resolution.capability_kind == CapabilityKind.TOOL
        assert resolution.capability_id == "files.read_file"
        assert resolution.requires_permission is True
        assert resolution.requires_approval is False

    def test_file_write_maps_to_files_write_file(self):
        resolution = self.resolver.resolve(_make_request("FILE_WRITE"))
        assert resolution.capability_kind == CapabilityKind.TOOL
        assert resolution.capability_id == "files.write_file"
        assert resolution.requires_approval is True
        assert resolution.requires_permission is True

    def test_file_delete_maps_to_files_delete_file(self):
        resolution = self.resolver.resolve(_make_request("FILE_DELETE"))
        assert resolution.capability_kind == CapabilityKind.TOOL
        assert resolution.capability_id == "files.delete_file"
        assert resolution.requires_approval is True
        assert resolution.requires_permission is True


    def test_generic_information_request_is_answer_only(self):
        request = _make_request("INFORMATION_REQUEST")
        request = request.model_copy(update={"user_request": "What is Python?"})
        resolution = self.resolver.resolve(request)
        assert resolution.capability_kind == CapabilityKind.ANSWER_ONLY
        assert resolution.capability_id is None
        assert resolution.requires_permission is False

    def test_time_information_request_maps_to_time_tool(self):
        request = _make_request("INFORMATION_REQUEST")
        request = request.model_copy(update={"user_request": "What time is it?"})
        resolution = self.resolver.resolve(request)
        assert resolution.capability_kind == CapabilityKind.TOOL
        assert resolution.capability_id == "system.get_time"

    def test_file_read_uses_only_explicit_constraint_parameter(self):
        request = _make_request("FILE_READ")
        request = request.model_copy(update={"constraints": {"path": "config.json"}})
        resolution = self.resolver.resolve(request)
        assert resolution.parameters == {"path": "config.json"}

    def test_file_read_does_not_emit_placeholder_parameters(self):
        request = _make_request("FILE_READ")
        resolution = self.resolver.resolve(request)
        assert resolution.parameters == {}
        assert "string (required)" not in str(resolution.parameters)

    # ── Service-backed operations ──────────────────────────────────────────────

    def test_task_create_maps_to_task_service(self):
        resolution = self.resolver.resolve(_make_request("TASK_CREATE"))
        assert resolution.capability_kind == CapabilityKind.SERVICE
        assert resolution.capability_id == "task.create"
        assert resolution.requires_permission is True
        assert resolution.requires_approval is False

    def test_plan_create_maps_to_plan_service(self):
        resolution = self.resolver.resolve(_make_request("PLAN_CREATE"))
        assert resolution.capability_kind == CapabilityKind.SERVICE
        assert resolution.capability_id == "plan.create"
        assert resolution.requires_permission is True
        assert resolution.requires_approval is False

    def test_plan_execute_maps_to_plan_service(self):
        resolution = self.resolver.resolve(_make_request("PLAN_EXECUTE"))
        assert resolution.capability_kind == CapabilityKind.SERVICE
        assert resolution.capability_id == "plan.execute"
        assert resolution.requires_approval is True
        assert resolution.requires_permission is True

    # ── Answer-only operations ─────────────────────────────────────────────────

    def test_chat_is_answer_only(self):
        resolution = self.resolver.resolve(_make_request("CHAT"))
        assert resolution.capability_kind == CapabilityKind.ANSWER_ONLY
        assert resolution.capability_id is None
        assert resolution.requires_permission is False
        assert resolution.requires_approval is False

    def test_unknown_is_answer_only(self):
        resolution = self.resolver.resolve(_make_request("UNKNOWN"))
        assert resolution.capability_kind == CapabilityKind.ANSWER_ONLY
        assert resolution.capability_id is None

    def test_other_is_answer_only(self):
        resolution = self.resolver.resolve(_make_request("OTHER"))
        assert resolution.capability_kind == CapabilityKind.ANSWER_ONLY
        assert resolution.capability_id is None

    # ── Unsupported operations ─────────────────────────────────────────────────
    # Note: OperatorRequest model validates operation_type at creation time,
    # so all valid operation_types are mapped in the resolver.
    # The resolver's is_supported() method can be used to check support.

    def test_is_supported_returns_false_for_invalid(self):
        """is_supported returns False for operation types not in mapping."""
        assert self.resolver.is_supported("INVALID_OP") is False

    # ── Helper methods ─────────────────────────────────────────────────────────

    def test_is_supported_tool(self):
        assert self.resolver.is_supported("FILE_READ") is True
        assert self.resolver.is_supported("FILE_WRITE") is True
        assert self.resolver.is_supported("INFORMATION_REQUEST") is True

    def test_is_supported_service(self):
        assert self.resolver.is_supported("TASK_CREATE") is True
        assert self.resolver.is_supported("PLAN_CREATE") is True

    def test_is_supported_answer_only(self):
        assert self.resolver.is_supported("CHAT") is True
        assert self.resolver.is_supported("UNKNOWN") is True

    def test_is_not_supported(self):
        assert self.resolver.is_supported("UNSUPPORTED_OP") is False

    def test_is_tool_backed(self):
        assert self.resolver.is_tool_backed("FILE_READ") is True
        assert self.resolver.is_tool_backed("FILE_WRITE") is True
        assert self.resolver.is_tool_backed("CHAT") is False

    def test_is_service_backed(self):
        assert self.resolver.is_service_backed("TASK_CREATE") is True
        assert self.resolver.is_service_backed("PLAN_CREATE") is True
        assert self.resolver.is_service_backed("FILE_READ") is False

    def test_is_answer_only(self):
        assert self.resolver.is_answer_only("CHAT") is True
        assert self.resolver.is_answer_only("UNKNOWN") is True
        assert self.resolver.is_answer_only("FILE_READ") is False

    # ── Case insensitivity ─────────────────────────────────────────────────────

    def test_lowercase_operation_type(self):
        resolution = self.resolver.resolve(_make_request("file_read"))
        assert resolution.capability_kind == CapabilityKind.TOOL
        assert resolution.capability_id == "files.read_file"

    # ── Confidence ─────────────────────────────────────────────────────────────

    def test_deterministic_confidence(self):
        resolution = self.resolver.resolve(_make_request("FILE_READ"))
        assert resolution.confidence == 1.0
        assert resolution.reason == "deterministic-tool-mapping"


__all__ = ["TestCapabilityResolver"]
