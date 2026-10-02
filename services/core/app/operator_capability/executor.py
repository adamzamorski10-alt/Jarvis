"""
Operator Capability — Executor.

Executes resolved capabilities through the appropriate application-owned path.

SECURITY:
  - OperatorCapabilityExecutor is NOT a new permission system.
  - Authority remains in: RequestContext, PermissionEngine, ApprovalService,
    ToolExecutionService, existing application services.
  - Model output is untrusted and cannot grant permissions or approvals.
"""
from __future__ import annotations

from typing import Any

from app.operator_capability.models import (
    CapabilityExecutionError,
    CapabilityExecutionResult,
    CapabilityKind,
    CapabilityResolution,
    UnsupportedCapabilityError,
)
from app.operator_request.models import OperatorRequest, ReadinessState
from app.tools.models import ToolCall


class OperatorCapabilityExecutor:
    """
    Application-owned executor for resolved capabilities.

    This executor routes capabilities to the appropriate application-owned path:
    - TOOL → ToolExecutionService
    - SERVICE → existing application services
    - ANSWER_ONLY → no execution
    """

    def __init__(
        self,
        tool_execution_service: Any = None,
        task_repository: Any = None,
        autonomy_mode: Any = None,
    ) -> None:
        self._tool_execution_service = tool_execution_service
        self._task_repository = task_repository
        self._autonomy_mode = autonomy_mode

    async def execute(
        self,
        request: OperatorRequest,
        resolution: CapabilityResolution,
    ) -> CapabilityExecutionResult:
        """
        Execute a resolved capability.

        Args:
            request: The operator request (application-owned, verified).
            resolution: The capability resolution (application-owned).

        Returns:
            CapabilityExecutionResult with structured result.

        Raises:
            CapabilityExecutionError: If execution fails.
        """
        # 1. Re-verify readiness
        if request.readiness_state != ReadinessState.SUFFICIENT:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=resolution.capability_kind,
                capability_id=resolution.capability_id,
                success=False,
                state="BLOCKED",
                user_message="Request is not ready for execution.",
                result_metadata={"readiness_state": request.readiness_state.value},
                error_category="READINESS_NOT_SUFFICIENT",
                error_detail=f"Readiness state is {request.readiness_state.value}, expected SUFFICIENT",
            )

        # 2. Route to appropriate executor
        if resolution.capability_kind == CapabilityKind.TOOL:
            return await self._execute_tool(request, resolution)
        elif resolution.capability_kind == CapabilityKind.SERVICE:
            return await self._execute_service(request, resolution)
        elif resolution.capability_kind == CapabilityKind.ANSWER_ONLY:
            return self._execute_answer_only(request, resolution)
        else:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=resolution.capability_kind,
                capability_id=resolution.capability_id,
                success=False,
                state="BLOCKED",
                user_message="Unsupported capability kind.",
                result_metadata={},
                error_category="UNSUPPORTED_CAPABILITY",
                error_detail=f"Capability kind {resolution.capability_kind} is not supported",
            )

    async def _execute_tool(
        self,
        request: OperatorRequest,
        resolution: CapabilityResolution,
    ) -> CapabilityExecutionResult:
        """Execute a tool-backed capability via ToolExecutionService."""
        if self._tool_execution_service is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.TOOL,
                capability_id=resolution.capability_id,
                success=False,
                state="FAILED",
                user_message="Tool execution service is not available.",
                result_metadata={},
                error_category="SERVICE_UNAVAILABLE",
                error_detail="ToolExecutionService is not configured",
            )

        try:
            # Execution parameters must be concrete. Never pass resolver schema
            # placeholders such as "string (required)" into a real tool.
            required = {
                "files.read_file": {"path"},
                "files.write_file": {"path", "content"},
                "files.delete_file": {"path"},
            }.get(resolution.capability_id or "", set())
            parameters = resolution.parameters or {}
            missing = sorted(required - set(parameters))
            if missing:
                return CapabilityExecutionResult(
                    request_id=request.request_id,
                    operation_type=request.operation_type,
                    capability_kind=CapabilityKind.TOOL,
                    capability_id=resolution.capability_id,
                    success=False,
                    state="BLOCKED",
                    user_message="Required capability parameters are missing.",
                    result_metadata={"missing_parameters": missing},
                    error_category="MISSING_CAPABILITY_PARAMETERS",
                    error_detail=f"Missing parameters: {', '.join(missing)}",
                )

            # Build tool call from validated resolution parameters
            tool_call = ToolCall(
                id=f"cap_{request.request_id}",
                tool_id=resolution.capability_id or "",
                arguments=resolution.parameters or {},
            )

            # Execute via ToolExecutionService
            # This is the ONLY authorized path for tool execution
            result = await self._tool_execution_service.execute_tool_call(
                tool_call=tool_call,
                action="execute",
                autonomy_mode=self._autonomy_mode,
                request_id=request.request_id,
                context={
                    "request_id": request.request_id,
                    "conversation_id": request.conversation_id,
                    "project_id": request.project_id,
                },
                actor_id=request.request_context.actor_id if request.request_context else None,
            )

            # Map result to CapabilityExecutionResult
            success = result.success if hasattr(result, 'success') else False
            tool_output = result.output if hasattr(result, 'output') else None
            error = result.error if hasattr(result, 'error') else None

            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.TOOL,
                capability_id=resolution.capability_id,
                success=success,
                state="EXECUTION_COMPLETED" if success else "EXECUTION_FAILED",
                user_message=self._format_tool_result_message(resolution, success, tool_output),
                result_metadata=self._extract_tool_metadata(result),
                error_category=str(error) if error else None,
                error_detail=str(result) if not success else None,
                tool_output=tool_output,
                tool_output_truncated=result.truncated if hasattr(result, 'truncated') else False,
            )
        except Exception as exc:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.TOOL,
                capability_id=resolution.capability_id,
                success=False,
                state="EXECUTION_FAILED",
                user_message="Tool execution failed.",
                result_metadata={},
                error_category="EXECUTION_ERROR",
                error_detail=str(exc),
            )

    async def _execute_service(
        self,
        request: OperatorRequest,
        resolution: CapabilityResolution,
    ) -> CapabilityExecutionResult:
        """Execute a service-backed capability via existing application services."""
        capability_id = resolution.capability_id or ""

        try:
            if capability_id == "task.create":
                return await self._execute_task_create(request, resolution)
            elif capability_id == "task.update":
                return await self._execute_task_update(request, resolution)
            elif capability_id == "plan.create":
                return await self._execute_plan_create(request, resolution)
            elif capability_id == "plan.execute":
                return await self._execute_plan_execute(request, resolution)
            else:
                return CapabilityExecutionResult(
                    request_id=request.request_id,
                    operation_type=request.operation_type,
                    capability_kind=CapabilityKind.SERVICE,
                    capability_id=capability_id,
                    success=False,
                    state="BLOCKED",
                    user_message=f"Service capability '{capability_id}' is not implemented.",
                    result_metadata={},
                    error_category="UNSUPPORTED_SERVICE",
                    error_detail=f"Service capability '{capability_id}' is not implemented",
                )
        except Exception as exc:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id=capability_id,
                success=False,
                state="EXECUTION_FAILED",
                user_message="Service execution failed.",
                result_metadata={},
                error_category="EXECUTION_ERROR",
                error_detail=str(exc),
            )

    async def _execute_task_create(self, request: OperatorRequest, resolution: CapabilityResolution) -> CapabilityExecutionResult:
        """Persist a development task through the application-owned repository."""
        if self._task_repository is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.create",
                success=False,
                state="FAILED",
                user_message="Task service is not available.",
                result_metadata={},
                error_category="SERVICE_UNAVAILABLE",
                error_detail="TaskRepository is not configured",
            )

        from app.tasks.models import DevelopmentTask, TaskStatus

        task = DevelopmentTask(
            project_id=request.project_id or "default",
            title=request.user_request[:200].strip(),
            description=request.user_request.strip(),
            status=TaskStatus.PENDING,
            metadata={
                "request_id": request.request_id,
                "conversation_id": request.conversation_id,
                "correlation_id": request.correlation_id,
                "operation_type": request.operation_type,
                "constraints": request.constraints,
            },
        )
        created = await self._task_repository.create_task(task)

        return CapabilityExecutionResult(
            request_id=request.request_id,
            operation_type=request.operation_type,
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.create",
            success=True,
            state="TASK_CREATED",
            user_message="Task created successfully.",
            result_metadata={"task_id": created.task_id},
            service_result=created.model_dump(mode="json"),
        )

    async def _execute_task_update(self, request: OperatorRequest, resolution: CapabilityResolution) -> CapabilityExecutionResult:
        """Update a task via existing TaskRepository."""
        return CapabilityExecutionResult(
            request_id=request.request_id,
            operation_type=request.operation_type,
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.update",
            success=False,
            state="BLOCKED",
            user_message="Task update requires explicit task_id.",
            result_metadata={},
            error_category="MISSING_TASK_ID",
            error_detail="task.update requires request.task_id to be set",
        )

    async def _execute_plan_create(self, request: OperatorRequest, resolution: CapabilityResolution) -> CapabilityExecutionResult:
        """Create a plan via existing PlanManager."""
        return CapabilityExecutionResult(
            request_id=request.request_id,
            operation_type=request.operation_type,
            capability_kind=CapabilityKind.SERVICE,
            capability_id="plan.create",
            success=False,
            state="BLOCKED",
            user_message="Plan creation requires AI Manager decision.",
            result_metadata={},
            error_category="REQUIRES_AI_MANAGER",
            error_detail="plan.create should be triggered by AI Manager decision",
        )

    async def _execute_plan_execute(self, request: OperatorRequest, resolution: CapabilityResolution) -> CapabilityExecutionResult:
        """Execute a plan via existing ExecutionBridge."""
        return CapabilityExecutionResult(
            request_id=request.request_id,
            operation_type=request.operation_type,
            capability_kind=CapabilityKind.SERVICE,
            capability_id="plan.execute",
            success=False,
            state="BLOCKED",
            user_message="Plan execution requires ExecutionBridge.",
            result_metadata={},
            error_category="REQUIRES_EXECUTION_BRIDGE",
            error_detail="plan.execute should be routed through ExecutionBridge",
        )

    def _execute_answer_only(
        self,
        request: OperatorRequest,
        resolution: CapabilityResolution,
    ) -> CapabilityExecutionResult:
        """No execution required; return answer-only result."""
        return CapabilityExecutionResult(
            request_id=request.request_id,
            operation_type=request.operation_type,
            capability_kind=CapabilityKind.ANSWER_ONLY,
            capability_id=None,
            success=True,
            state="ANSWERED",
            user_message="Request processed without capability execution.",
            result_metadata={},
        )

    def _format_tool_result_message(
        self,
        resolution: CapabilityResolution,
        success: bool,
        tool_output: Any,
    ) -> str:
        """Format a user-safe message from tool result."""
        if success:
            if tool_output is None:
                return f"{resolution.capability_id} executed successfully."
            output_str = str(tool_output)
            if len(output_str) > 500:
                return f"{resolution.capability_id} executed successfully. Result: {output_str[:500]}..."
            return f"{resolution.capability_id} executed successfully. Result: {output_str}"
        else:
            return f"{resolution.capability_id or 'Tool'} execution failed."

    def _extract_tool_metadata(self, result: Any) -> dict[str, Any]:
        """Extract bounded metadata from tool result."""
        if result is None:
            return {}
        metadata = {}
        if hasattr(result, 'success'):
            metadata["success"] = result.success
        if hasattr(result, 'error'):
            metadata["error"] = str(result.error) if result.error else None
        if hasattr(result, 'truncated'):
            metadata["truncated"] = result.truncated
        return metadata


__all__ = ["OperatorCapabilityExecutor"]
