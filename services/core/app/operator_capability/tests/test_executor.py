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
        self.task_repository.get_task = AsyncMock()
        self.task_state_machine = MagicMock()
        self.task_state_machine.transition = AsyncMock()
        self.manager_planning_service = MagicMock()
        self.manager_planning_service.plan_decision = AsyncMock()
        self.plan_service = MagicMock()
        self.plan_service.persist_manager_plan_result = AsyncMock()
        self.execution_bridge = MagicMock()
        self.execution_bridge.execute = AsyncMock()
        self.executor = OperatorCapabilityExecutor(
            tool_execution_service=self.tool_execution_service,
            task_repository=self.task_repository,
            task_state_machine=self.task_state_machine,
            manager_planning_service=self.manager_planning_service,
            plan_service=self.plan_service,
            execution_bridge=self.execution_bridge,
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
    async def test_task_create_is_idempotent_for_same_request(self):
        request = _make_request("TASK_CREATE")
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            operation_type="TASK_CREATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.create",
        )

        existing = MagicMock()
        existing.task_id = "task_existing"
        existing.model_dump.return_value = {"task_id": "task_existing"}
        self.task_repository.get_task.return_value = existing

        result = await self.executor.execute(request, resolution)

        assert result.success is True
        assert result.state == "TASK_ALREADY_EXISTS"
        assert result.result_metadata["idempotent_replay"] is True
        self.task_repository.create_task.assert_not_awaited()

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
    async def test_task_update_persists_status(self):
        request = _make_request("TASK_UPDATE")
        request = request.model_copy(update={
            "readiness_state": ReadinessState.SUFFICIENT,
            "task_id": "task_123",
            "project_id": "project_1",
            "constraints": {"expected_status": "PENDING", "status": "RUNNING"},
        })
        resolution = _make_resolution(
            operation_type="TASK_UPDATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.update",
        )

        current_task = MagicMock()
        current_task.project_id = "project_1"
        self.task_repository.get_task.return_value = current_task
        updated_task = MagicMock()
        updated_task.task_id = "task_123"
        updated_task.status.value = "RUNNING"
        updated_task.model_dump.return_value = {"task_id": "task_123", "status": "RUNNING"}
        transition = MagicMock()
        transition.task = updated_task
        transition.previous_status.value = "PENDING"
        self.task_state_machine.transition.return_value = transition

        result = await self.executor.execute(request, resolution)

        assert result.success is True
        assert result.state == "EXECUTION_COMPLETED"
        assert result.result_metadata == {
            "task_id": "task_123",
            "previous_status": "PENDING",
            "status": "RUNNING",
        }
        self.task_state_machine.transition.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_task_update_requires_expected_status(self):
        request = _make_request("TASK_UPDATE")
        request = request.model_copy(update={
            "readiness_state": ReadinessState.SUFFICIENT,
            "task_id": "task_123",
            "constraints": {"status": "RUNNING"},
        })
        resolution = _make_resolution(
            operation_type="TASK_UPDATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.update",
        )
        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.error_category == "MISSING_EXPECTED_TASK_STATUS"
        self.task_state_machine.transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_update_rejects_project_scope_mismatch(self):
        request = _make_request("TASK_UPDATE")
        request = request.model_copy(update={
            "readiness_state": ReadinessState.SUFFICIENT,
            "task_id": "task_123",
            "project_id": "project_request",
            "constraints": {"expected_status": "PENDING", "status": "RUNNING"},
        })
        resolution = _make_resolution(
            operation_type="TASK_UPDATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.update",
        )
        current_task = MagicMock()
        current_task.project_id = "different_project"
        self.task_repository.get_task.return_value = current_task

        result = await self.executor.execute(request, resolution)
        assert result.success is False
        assert result.error_category == "PROJECT_SCOPE_MISMATCH"
        self.task_state_machine.transition.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_update_uses_atomic_state_transition(self):
        request = _make_request("TASK_UPDATE")
        request = request.model_copy(update={
            "readiness_state": ReadinessState.SUFFICIENT,
            "task_id": "task_123",
            "project_id": "project_1",
            "constraints": {"expected_status": "PENDING", "status": "RUNNING"},
        })
        resolution = _make_resolution(
            operation_type="TASK_UPDATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.update",
        )
        current_task = MagicMock(project_id="project_1")
        self.task_repository.get_task.return_value = current_task
        updated_task = MagicMock(
            task_id="task_123",
            status=MagicMock(value="RUNNING"),
        )
        updated_task.model_dump.return_value = {"task_id": "task_123", "status": "RUNNING"}
        transition = MagicMock(
            task=updated_task,
            previous_status=MagicMock(value="PENDING"),
        )
        self.task_state_machine.transition.return_value = transition

        result = await self.executor.execute(request, resolution)
        assert result.success is True
        self.task_state_machine.transition.assert_awaited_once_with(
            task_id="task_123",
            expected_status=__import__("app.tasks.models", fromlist=["TaskStatus"]).TaskStatus.PENDING,
            new_status=__import__("app.tasks.models", fromlist=["TaskStatus"]).TaskStatus.RUNNING,
            reason=None,
        )

    async def test_task_update_missing_status_is_blocked(self):
        request = _make_request("TASK_UPDATE")
        request = request.model_copy(update={
            "readiness_state": ReadinessState.SUFFICIENT,
            "task_id": "task_123",
            "constraints": {},
        })
        resolution = _make_resolution(
            operation_type="TASK_UPDATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.update",
        )

        result = await self.executor.execute(request, resolution)

        assert result.success is False
        assert result.state == "BLOCKED"
        assert result.error_category == "MISSING_TASK_STATUS"

    @pytest.mark.asyncio
    async def test_plan_create_fails_closed_without_manager_result(self):
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
        assert result.error_category == "REQUIRES_PLAN_SERVICES"

    @pytest.mark.asyncio
    async def test_plan_create_persists_real_plan(self):
        request = _make_request("PLAN_CREATE")
        request = request.model_copy(update={
            "readiness_state": ReadinessState.SUFFICIENT,
            "project_id": "project_1",
        })
        resolution = _make_resolution(
            operation_type="PLAN_CREATE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="plan.create",
        )
        manager_result = MagicMock()
        planning_result = MagicMock()
        planning_result.plan_id = "plan_123"
        planning_result.project_id = "project_1"
        planning_result.manager_plan_result = MagicMock()
        self.manager_planning_service.plan_decision.return_value = planning_result
        task = MagicMock()
        task.model_dump.return_value = {"task_id": "task_1"}
        self.plan_service.persist_manager_plan_result.return_value = [task]

        result = await self.executor.execute(request, resolution, manager_result=manager_result)

        assert result.success is True
        assert result.state == "PLAN_CREATED"
        assert result.result_metadata["plan_id"] == "plan_123"
        assert result.result_metadata["task_count"] == 1
        self.manager_planning_service.plan_decision.assert_awaited_once_with(
            manager_result,
            project_id="project_1",
            execution_authorized=False,
        )
        self.plan_service.persist_manager_plan_result.assert_awaited_once_with(
            planning_result.manager_plan_result
        )

    @pytest.mark.asyncio
    async def test_plan_execute_requires_manager_result(self):
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
        assert result.error_category == "REQUIRES_AI_MANAGER"
        self.execution_bridge.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_plan_execute_uses_controlled_execution_bridge(self):
        request = _make_request("PLAN_EXECUTE")
        request = request.model_copy(update={
            "readiness_state": ReadinessState.SUFFICIENT,
            "project_id": "project_1",
            "constraints": {"approval_id": "approval_123"},
        })
        resolution = _make_resolution(
            operation_type="PLAN_EXECUTE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="plan.execute",
        )
        manager_result = MagicMock()

        execution_result = MagicMock()
        execution_result.plan_id = "plan_123"
        execution_result.project_id = "project_1"
        execution_result.execution_result.terminal_state.value = "COMPLETED"
        execution_result.execution_result.total_tasks = 2
        execution_result.execution_result.committed_tasks = 2
        execution_result.execution_result.failed_tasks = 0
        execution_result.execution_result.reason = None
        execution_result.execution_result.model_dump.return_value = {"plan_id": "plan_123"}

        self.execution_bridge.execute.return_value = execution_result

        result = await self.executor.execute(
            request,
            resolution,
            manager_result=manager_result,
        )

        assert result.success is True
        assert result.state == "PLAN_COMPLETED"
        assert result.result_metadata["plan_id"] == "plan_123"
        self.execution_bridge.execute.assert_awaited_once_with(
            result=manager_result,
            project_id="project_1",
            approval_id="approval_123",
        )

    @pytest.mark.asyncio
    async def test_plan_execute_blocks_when_bridge_requires_approval(self):
        from app.ai_manager.execution_bridge import ExecutionNeedsApprovalError
        from app.ai_manager.approval_models import ApprovalStatus, CapabilityApproval

        request = _make_request("PLAN_EXECUTE")
        request = request.model_copy(update={"readiness_state": ReadinessState.SUFFICIENT})
        resolution = _make_resolution(
            operation_type="PLAN_EXECUTE",
            capability_kind=CapabilityKind.SERVICE,
            capability_id="plan.execute",
        )
        manager_result = MagicMock()
        approval = CapabilityApproval(
            approval_id="approval_pending",
            intent_id="req_test",
            operation_type="plan_execution",
            scope_fingerprint="scope",
            effective_risk=__import__("app.ai_manager.models", fromlist=["RiskLevel"]).RiskLevel.HIGH,
            status=ApprovalStatus.PENDING,
            created_at=__import__("datetime", fromlist=["datetime"]).datetime.now(__import__("datetime", fromlist=["timezone"]).timezone.utc),
            expires_at=__import__("datetime", fromlist=["datetime"]).datetime.now(__import__("datetime", fromlist=["timezone"]).timezone.utc),
        )
        self.execution_bridge.execute.side_effect = ExecutionNeedsApprovalError(
            intent_id="req_test",
            approval=approval,
            auth_decision=MagicMock(),
        )

        result = await self.executor.execute(
            request,
            resolution,
            manager_result=manager_result,
        )

        assert result.success is False
        assert result.error_category == "APPROVAL_REQUIRED"
        assert result.result_metadata["approval_id"] == "approval_pending"

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
