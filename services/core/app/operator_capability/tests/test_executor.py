"""
Operator Capability — Executor Tests.

Tests for OperatorCapabilityExecutor.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.identity.models import ActorType, RequestContext
from app.operator_capability.executor import OperatorCapabilityExecutor
from app.operator_capability.models import (
    CapabilityCategory,
    CapabilityExecutionResult,
    CapabilityKind,
    CapabilityResolution,
)
from app.operator_request.models import OperatorRequest, ReadinessState


def _make_request(operation_type: str = "FILE_READ") -> OperatorRequest:
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


def _make_resolution(
    operation_type: str = "FILE_READ",
    capability_kind: CapabilityKind = CapabilityKind.TOOL,
    capability_id: str = "files.read_file",
) -> CapabilityResolution:
    return CapabilityResolution(
        request_id="req_test",
        operation_type=operation_type,
        capability_kind=capability_kind,
        capability_id=capability_id,
        capability_category=CapabilityCategory.FILES,
        description="Test capability",
        parameters={"path": "/test/file.txt"},
        requires_approval=False,
        requires_permission=True,
        confidence=1.0,
        reason="test",
    )


class TestOperatorCapabilityExecutor:
    """Tests for OperatorCapabilityExecutor."""

    def setup_method(self):
        self.tool_execution_service = MagicMock()
        self.tool_execution_service.execute_tool_call = AsyncMock()
        self.task_repository = MagicMock()
        self.task_repository.create_task = AsyncMock()
        self.executor = OperatorCapabilityExecutor(
            tool_execution_service=self.tool_execution_service,
            task_repository=self.task_repository,
            autonomy_mode=None,
        )

    # ── Readiness checks ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_insufficient_readiness_blocks_execution(self):
        """INSUFFICIENT readiness blocks execution."""
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.INSUFFICIENT})
        resolution = _make_resolution()

        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "BLOCKED"
        assert "not ready" in result.user_message.lower()

    @pytest.mark.asyncio
    async def test_ambiguous_readiness_blocks_execution(self):
        """AMBIGUOUS readiness blocks execution."""
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.AMBIGUOUS})
        resolution = _make_resolution()

        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "BLOCKED"

    @pytest.mark.asyncio
    async def test_unknown_readiness_blocks_execution(self):
        """UNKNOWN readiness blocks execution."""
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.UNKNOWN})
        resolution = _make_resolution()

        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "BLOCKED"

    # ── Tool execution ─────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_file_read_missing_path_is_blocked_before_tool_execution(self):
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution()
        resolution = resolution.model_copy(update={"parameters": {}})

        result = await self.executor.execute(request, resolution)

        assert result.success is False
        assert result.state == "BLOCKED"
        assert result.error_category == "MISSING_CAPABILITY_PARAMETERS"
        self.tool_execution_service.execute_tool_call.assert_not_called()


    @pytest.mark.asyncio
    async def test_tool_execution_success(self):
        """Successful tool execution returns success result."""
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution()

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.output = "file content here"
        mock_result.error = None
        mock_result.truncated = False
        self.tool_execution_service.execute_tool_call = AsyncMock(return_value=mock_result)

        result = await self.executor.execute(request, resolution)
        assert result.success is True
        assert result.state == "EXECUTION_COMPLETED"
        assert result.capability_id == "files.read_file"

    @pytest.mark.asyncio
    async def test_tool_execution_failure(self):
        """Failed tool execution returns failure result."""
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution()

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.output = None
        mock_result.error = "Permission denied"
        mock_result.truncated = False
        self.tool_execution_service.execute_tool_call = AsyncMock(return_value=mock_result)

        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "EXECUTION_FAILED"

    @pytest.mark.asyncio
    async def test_tool_execution_service_unavailable(self):
        """Missing ToolExecutionService returns service unavailable."""
        executor = OperatorCapabilityExecutor(
            tool_execution_service=None,
            autonomy_mode=None,
        )
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution()

        result = await executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "FAILED"
        assert "not available" in result.user_message.lower()

    @pytest.mark.asyncio
    async def test_tool_execution_exception_handled(self):
        """Exceptions during tool execution are handled gracefully."""
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution()

        self.tool_execution_service.execute_tool_call = AsyncMock(side_effect=RuntimeError("Tool failed"))

        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "EXECUTION_FAILED"
        assert result.error_category == "EXECUTION_ERROR"

    # ── Service execution ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_service_task_create(self):
        """Service-backed task.create returns appropriate result."""
        request = _make_request("TASK_CREATE")
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            operation_type="TASK_CREATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.create",
        )

        created_task = MagicMock()
        created_task.task_id = "task_real_123"
        created_task.model_dump.return_value = {"task_id": "task_real_123"}
        self.task_repository.create_task.return_value = created_task

        result = await self.executor.execute(request, resolution)

        assert result.success is True
        assert result.state == "TASK_CREATED"
        assert result.capability_kind == CapabilityKind.SERVICE
        assert result.capability_id == "task.create"
        assert result.result_metadata["task_id"] == "task_real_123"
        self.task_repository.create_task.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_task_create_without_repository_fails_closed(self):
        request = _make_request("TASK_CREATE")
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            operation_type="TASK_CREATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.create",
        )

        executor = OperatorCapabilityExecutor(
            tool_execution_service=self.tool_execution_service,
            task_repository=None,
            autonomy_mode=None,
        )

        result = await executor.execute(request, resolution)

        assert result.success is False
        assert result.state == "FAILED"
        assert result.error_category == "SERVICE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_task_update_fails_closed_without_real_service(self):
        request = _make_request("TASK_UPDATE")
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            operation_type="TASK_UPDATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.update",
        )
        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "BLOCKED"
        assert result.error_category == "MISSING_TASK_ID"

    @pytest.mark.asyncio
    async def test_plan_create_fails_closed_without_manager_execution(self):
        request = _make_request("PLAN_CREATE")
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            operation_type="PLAN_CREATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="plan.create",
        )
        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "BLOCKED"
        assert result.error_category == "REQUIRES_AI_MANAGER"

    @pytest.mark.asyncio
    async def test_plan_execute_fails_closed_without_execution_bridge(self):
        request = _make_request("PLAN_EXECUTE")
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            operation_type="PLAN_EXECUTE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="plan.execute",
        )
        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "BLOCKED"
        assert result.error_category == "REQUIRES_EXECUTION_BRIDGE"

    @pytest.mark.asyncio
    async def test_service_unsupported_returns_blocked(self):
        """Unsupported service capability returns blocked."""
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            capability_kind=CapabilityKind.SERVICE,
            capability_id="unknown.service",
        )

        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "BLOCKED"

    # ── Answer-only execution ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_answer_only_execution(self):
        """Answer-only capability returns success without execution."""
        request = _make_request("CHAT")
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            operation_type="CHAT",
            capability_kind=CapabilityKind.ANSWER_ONLY,
            capability_id=None,
        )

        result = await self.executor.execute(request, resolution)
        assert result.success is True
        assert result.state == "ANSWERED"
        assert result.capability_kind == CapabilityKind.ANSWER_ONLY

    # ── Unsupported capability kind ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_unsupported_capability_kind(self):
        """Unsupported capability kind returns blocked."""
        request = _make_request()
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            capability_kind=CapabilityKind.UNSUPPORTED,
            capability_id=None,
        )

        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.state == "BLOCKED"
        assert "Unsupported capability kind" in result.user_message


__all__ = ["TestOperatorCapabilityExecutor"]
