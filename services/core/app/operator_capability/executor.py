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

import hashlib
from typing import Any, Awaitable, Callable

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
        task_state_machine: Any = None,
        manager_planning_service: Any = None,
        plan_service: Any = None,
        execution_bridge: Any = None,
        execution_bridge_factory: Callable[[], Awaitable[Any]] | None = None,
        web_search_service: Any = None,
        web_research_service: Any = None,
        research_synthesis_adapter: Any = None,
        permission_engine: Any = None,
        autonomy_mode: Any = None,
    ) -> None:
        self._tool_execution_service = tool_execution_service
        self._task_repository = task_repository
        self._task_state_machine = task_state_machine
        self._manager_planning_service = manager_planning_service
        self._plan_service = plan_service
        self._execution_bridge = execution_bridge
        self._execution_bridge_factory = execution_bridge_factory
        self._web_search_service = web_search_service
        self._web_research_service = web_research_service
        self._research_synthesis_adapter = research_synthesis_adapter
        self._permission_engine = permission_engine
        self._autonomy_mode = autonomy_mode
        self.ensure_web_search_permission_definition()

    @staticmethod
    def ensure_web_search_permission_definition() -> None:
        from app.tools.models import RiskLevel, ToolCategory, ToolDefinition
        from app.tools.registry import tool_registry
        if not tool_registry.exists("web.search"):
            tool_registry.register(
                ToolDefinition(
                    tool_id="web.search",
                    action="search",
                    name="Web Search",
                    description="Search the public web and return ranked links and snippets.",
                    category=ToolCategory.NETWORK,
                    risk_level=RiskLevel.SAFE,
                    requires_confirmation=False,
                    enabled=True,
                    parameters_schema={"type": "object", "properties": {"query": {"type": "string", "maxLength": 600}}},
                    result_schema={"type": "object"},
                )
            )

    async def execute(
        self,
        request: OperatorRequest,
        resolution: CapabilityResolution,
        manager_result: Any = None,
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
            return await self._execute_service(request, resolution, manager_result)
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
        manager_result: Any = None,
    ) -> CapabilityExecutionResult:
        """Execute a service-backed capability via existing application services."""
        capability_id = resolution.capability_id or ""

        try:
            if capability_id == "task.create":
                return await self._execute_task_create(request, resolution)
            elif capability_id == "task.update":
                return await self._execute_task_update(request, resolution)
            elif capability_id == "plan.create":
                return await self._execute_plan_create(request, resolution, manager_result)
            elif capability_id == "plan.execute":
                return await self._execute_plan_execute(request, resolution, manager_result)
            elif capability_id == "web.search":
                return await self._execute_web_search(request, resolution)
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

    async def _execute_web_search(self, request: OperatorRequest, resolution: CapabilityResolution) -> CapabilityExecutionResult:
        if self._web_search_service is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="web.search",
                success=False,
                state="FAILED",
                user_message="Web search is not configured.",
                result_metadata={},
                error_category="SERVICE_UNAVAILABLE",
                error_detail="WebSearchService is not configured",
            )

        from app.web_search.models import WebSearchRequest

        query = str(resolution.parameters.get("query") or request.user_request).strip()
        if not query:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="web.search",
                success=False,
                state="BLOCKED",
                user_message="A search query is required.",
                result_metadata={},
                error_category="MISSING_CAPABILITY_PARAMETERS",
                error_detail="Missing search query",
            )
        if self._permission_engine is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="web.search",
                success=False,
                state="BLOCKED",
                user_message="Web search is not currently permitted.",
                result_metadata={},
                error_category="PERMISSION_ENGINE_UNAVAILABLE",
                error_detail="PermissionEngine is required for web.search execution",
            )

        from app.tools.models import AutonomyMode, PermissionDecisionType, PermissionEvaluationRequest, RiskLevel
        mode = self._autonomy_mode if isinstance(self._autonomy_mode, AutonomyMode) else AutonomyMode.MANUAL
        decision = await self._permission_engine.evaluate(
            PermissionEvaluationRequest(
                tool_id="web.search",
                risk_level=RiskLevel.SAFE,
                action="search",
                autonomy_mode=mode,
                arguments={"query_length": len(query)},
                context={"request_id": request.request_id},
                actor_id=request.request_context.actor_id if request.request_context else None,
            )
        )
        if decision.decision != PermissionDecisionType.ALLOW:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="web.search",
                success=False,
                state="BLOCKED",
                user_message="Web search is not currently permitted.",
                result_metadata={"permission_decision": decision.decision.value, "rule_id": decision.rule_id},
                error_category="PERMISSION_DENIED",
                error_detail=decision.reason,
            )
        try:
            if self._is_explicit_research_request(request.user_request):
                if self._web_research_service is None or self._research_synthesis_adapter is None:
                    return CapabilityExecutionResult(
                        request_id=request.request_id,
                        operation_type=request.operation_type,
                        capability_kind=CapabilityKind.SERVICE,
                        capability_id="web.search",
                        success=False,
                        state="FAILED",
                        user_message="Research is not configured. Ordinary web search remains available.",
                        result_metadata={},
                        error_category="RESEARCH_UNAVAILABLE",
                        error_detail="Research retrieval/synthesis dependencies are not configured",
                    )
                from app.web_research.models import ResearchRequest
                bundle = await self._web_research_service.collect(
                    ResearchRequest(query=query, max_sources=3),
                    request_id=request.request_id,
                )
                research_response = await self._research_synthesis_adapter.synthesize(
                    bundle,
                    question=query,
                )
                return CapabilityExecutionResult(
                    request_id=request.request_id,
                    operation_type=request.operation_type,
                    capability_kind=CapabilityKind.SERVICE,
                    capability_id="web.search",
                    success=True,
                    state="EXECUTION_COMPLETED",
                    user_message=(
                        research_response.answer
                        if research_response.synthesized
                        else "I could not synthesize an answer because no usable source evidence was retrieved."
                    ),
                    result_metadata={
                        "research": True,
                        "source_count": len(research_response.sources),
                        "citation_count": len(research_response.citations),
                        "synthesized": research_response.synthesized,
                        "limitations": research_response.limitations,
                    },
                    service_result=research_response.model_dump(mode="json"),
                )
            result = await self._web_search_service.search(
                WebSearchRequest(query=query),
                request_id=request.request_id,
            )
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="web.search",
                success=True,
                state="EXECUTION_COMPLETED",
                user_message=self._format_web_search_message(result),
                result_metadata={
                    "provider": result.provider,
                    "result_count": len(result.results),
                    "more_results_available": result.more_results_available,
                },
                service_result=result.model_dump(mode="json"),
            )
        except Exception as exc:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="web.search",
                success=False,
                state="EXECUTION_FAILED",
                user_message="Web search failed. Please try again.",
                result_metadata={},
                error_category=getattr(exc, "code", exc.__class__.__name__),
                error_detail=str(exc),
            )

    @staticmethod
    def _is_explicit_research_request(message: str) -> bool:
        """Use the deeper research workflow only for explicit research phrasing."""
        import re
        return bool(re.search(
            r"\b(deep\s+research|research|researchuj|zrób\s+research|zrob\s+research|"
            r"przeprowadź\s+analizę\s+źródeł|przeprowadz\s+analize\s+zrodel)\b",
            message,
            re.IGNORECASE,
        ))

    @staticmethod
    def _format_web_search_message(result: Any) -> str:
        if not result.results:
            return f"No web results found for: {result.query}"
        lines = [f"Search results for: {result.query}"]
        for item in result.results:
            snippet = f" — {item.snippet}" if item.snippet else ""
            lines.append(f"{item.rank}. {item.title} — {item.url}{snippet}")
        return "\n".join(lines)

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

        # Request IDs are application-owned and stable for retries. Reusing a
        # request must not silently create duplicate development tasks.
        deterministic_task_id = "task_" + hashlib.sha256(
            f"operator-request:{request.request_id}".encode("utf-8")
        ).hexdigest()
        existing = await self._task_repository.get_task(deterministic_task_id)
        if existing is not None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.create",
                success=True,
                state="TASK_ALREADY_EXISTS",
                user_message="Task already exists for this request.",
                result_metadata={
                    "task_id": existing.task_id,
                    "idempotent_replay": True,
                },
                service_result=existing.model_dump(mode="json"),
            )

        task = DevelopmentTask(
            task_id=deterministic_task_id,
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
        """Update a task status through the application-owned repository."""
        if self._task_repository is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.update",
                success=False,
                state="FAILED",
                user_message="Task service is not available.",
                result_metadata={},
                error_category="SERVICE_UNAVAILABLE",
                error_detail="TaskRepository is not configured",
            )

        task_id = request.task_id
        if not task_id:
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

        status_value = request.constraints.get("status")
        expected_status_value = request.constraints.get("expected_status")
        if not isinstance(status_value, str) or not status_value.strip():
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.update",
                success=False,
                state="BLOCKED",
                user_message="Task update requires an explicit status.",
                result_metadata={},
                error_category="MISSING_TASK_STATUS",
                error_detail="task.update requires constraints.status",
            )
        if not isinstance(expected_status_value, str) or not expected_status_value.strip():
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.update",
                success=False,
                state="BLOCKED",
                user_message="Task update requires the current task status.",
                result_metadata={},
                error_category="MISSING_EXPECTED_TASK_STATUS",
                error_detail="task.update requires constraints.expected_status for an atomic state transition",
            )
        if self._task_state_machine is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.update",
                success=False,
                state="FAILED",
                user_message="Task state service is not available.",
                result_metadata={},
                error_category="SERVICE_UNAVAILABLE",
                error_detail="TaskStateMachine is not configured",
            )

        from app.tasks.models import TaskStatus

        try:
            status = TaskStatus(status_value.strip().upper())
            expected_status = TaskStatus(expected_status_value.strip().upper())
        except ValueError as exc:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.update",
                success=False,
                state="BLOCKED",
                user_message="Task update contains an invalid status.",
                result_metadata={"status": status_value, "expected_status": expected_status_value},
                error_category="INVALID_TASK_STATUS",
                error_detail=str(exc),
            )

        current = await self._task_repository.get_task(task_id)
        if current is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.update",
                success=False,
                state="FAILED",
                user_message="Task was not found.",
                result_metadata={"task_id": task_id},
                error_category="TASK_NOT_FOUND",
                error_detail=f"No task found for task_id={task_id}",
            )

        if request.project_id and current.project_id != request.project_id:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.update",
                success=False,
                state="BLOCKED",
                user_message="Task does not belong to the requested project.",
                result_metadata={"task_id": task_id},
                error_category="PROJECT_SCOPE_MISMATCH",
                error_detail="Task project does not match request project",
            )

        try:
            transition = await self._task_state_machine.transition(
                task_id=task_id,
                expected_status=expected_status,
                new_status=status,
                reason=request.constraints.get("reason") if isinstance(request.constraints.get("reason"), str) else None,
            )
        except Exception as exc:
            from app.tasks.exceptions import (
                InvalidStateTransitionError,
                StaleStateTransitionError,
                TaskNotFoundError,
            )
            if isinstance(exc, TaskNotFoundError):
                category = "TASK_NOT_FOUND"
            elif isinstance(exc, StaleStateTransitionError):
                category = "STALE_TASK_STATE"
            elif isinstance(exc, InvalidStateTransitionError):
                category = "INVALID_TASK_TRANSITION"
            else:
                category = "TASK_TRANSITION_FAILED"
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.update",
                success=False,
                state="BLOCKED" if category in {"STALE_TASK_STATE", "INVALID_TASK_TRANSITION"} else "FAILED",
                user_message="Task status could not be updated safely.",
                result_metadata={"task_id": task_id, "expected_status": expected_status.value, "status": status.value},
                error_category=category,
                error_detail=str(exc),
            )

        updated = transition.task
        if updated is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="task.update",
                success=False,
                state="FAILED",
                user_message="Task transition produced no updated task.",
                result_metadata={"task_id": task_id},
                error_category="TASK_TRANSITION_INCOMPLETE",
                error_detail="State transition reported success without a task",
            )

        return CapabilityExecutionResult(
            request_id=request.request_id,
            operation_type=request.operation_type,
            capability_kind=CapabilityKind.SERVICE,
            capability_id="task.update",
            success=True,
            state="EXECUTION_COMPLETED",
            user_message="Task updated successfully.",
            result_metadata={
                "task_id": updated.task_id,
                "previous_status": transition.previous_status.value,
                "status": updated.status.value,
            },
            service_result=updated.model_dump(mode="json"),
        )

    async def _execute_plan_create(
        self,
        request: OperatorRequest,
        resolution: CapabilityResolution,
        manager_result: Any = None,
    ) -> CapabilityExecutionResult:
        """Create and persist a validated plan from a trusted ManagerResult."""
        if manager_result is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="plan.create",
                success=False,
                state="BLOCKED",
                user_message="Plan creation requires a trusted AI Manager decision.",
                result_metadata={},
                error_category="REQUIRES_AI_MANAGER",
                error_detail="No ManagerResult was supplied by the Operator runtime",
            )
        if self._manager_planning_service is None or self._plan_service is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="plan.create",
                success=False,
                state="BLOCKED",
                user_message="Plan creation is not fully configured.",
                result_metadata={},
                error_category="REQUIRES_PLAN_SERVICES",
                error_detail="ManagerPlanningService and PlanService are required",
            )
        try:
            project_id = request.project_id or "default"
            planning_result = await self._manager_planning_service.plan_decision(
                manager_result,
                project_id=project_id,
                execution_authorized=False,
            )
            tasks = await self._plan_service.persist_manager_plan_result(
                planning_result.manager_plan_result
            )
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="plan.create",
                success=True,
                state="PLAN_CREATED",
                user_message="Plan created successfully.",
                result_metadata={
                    "plan_id": planning_result.plan_id,
                    "project_id": planning_result.project_id,
                    "task_count": len(tasks),
                },
                service_result={
                    "plan_id": planning_result.plan_id,
                    "tasks": [task.model_dump(mode="json") for task in tasks],
                },
            )
        except Exception as exc:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="plan.create",
                success=False,
                state="EXECUTION_FAILED",
                user_message="Plan creation failed.",
                result_metadata={},
                error_category="PLAN_CREATION_FAILED",
                error_detail=str(exc),
            )

    async def _execute_plan_execute(
        self,
        request: OperatorRequest,
        resolution: CapabilityResolution,
        manager_result: Any = None,
    ) -> CapabilityExecutionResult:
        """Execute through the single application-owned AI Manager bridge."""
        if manager_result is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="plan.execute",
                success=False,
                state="BLOCKED",
                user_message="Plan execution requires a trusted AI Manager decision.",
                result_metadata={},
                error_category="REQUIRES_AI_MANAGER",
                error_detail="No ManagerResult was supplied by the Operator runtime",
            )

        execution_bridge = self._execution_bridge
        if execution_bridge is None and self._execution_bridge_factory is not None:
            try:
                execution_bridge = await self._execution_bridge_factory()
            except Exception as exc:
                return CapabilityExecutionResult(
                    request_id=request.request_id,
                    operation_type=request.operation_type,
                    capability_kind=CapabilityKind.SERVICE,
                    capability_id="plan.execute",
                    success=False,
                    state="FAILED",
                    user_message="Plan execution could not initialize the controlled execution layer.",
                    result_metadata={},
                    error_category="EXECUTION_BRIDGE_INITIALIZATION_FAILED",
                    error_detail=str(exc),
                )

        if execution_bridge is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="plan.execute",
                success=False,
                state="BLOCKED",
                user_message="Plan execution is not fully configured.",
                result_metadata={},
                error_category="REQUIRES_EXECUTION_BRIDGE",
                error_detail="ExecutionBridge is not configured",
            )

        project_id = request.project_id or "default"
        approval_id = None
        if request.constraints:
            candidate = request.constraints.get("approval_id")
            if isinstance(candidate, str) and candidate.strip():
                approval_id = candidate.strip()

        try:
            execution = await execution_bridge.execute(
                result=manager_result,
                project_id=project_id,
                approval_id=approval_id,
            )
        except Exception as exc:
            from app.ai_manager.execution_bridge import (
                ExecutionAuthorizationError,
                ExecutionBridgeError,
                ExecutionNeedsApprovalError,
            )

            if isinstance(exc, ExecutionNeedsApprovalError):
                approval = exc.approval
                return CapabilityExecutionResult(
                    request_id=request.request_id,
                    operation_type=request.operation_type,
                    capability_kind=CapabilityKind.SERVICE,
                    capability_id="plan.execute",
                    success=False,
                    state="BLOCKED",
                    user_message="Plan execution requires user approval before it can start.",
                    result_metadata={
                        "approval_required": True,
                        "approval_id": approval.approval_id,
                        "intent_id": exc.intent_id,
                    },
                    error_category="APPROVAL_REQUIRED",
                    error_detail=str(exc),
                )

            if isinstance(exc, ExecutionAuthorizationError):
                return CapabilityExecutionResult(
                    request_id=request.request_id,
                    operation_type=request.operation_type,
                    capability_kind=CapabilityKind.SERVICE,
                    capability_id="plan.execute",
                    success=False,
                    state="BLOCKED",
                    user_message="Plan execution was blocked by application authorization.",
                    result_metadata={},
                    error_category="EXECUTION_NOT_AUTHORIZED",
                    error_detail=str(exc),
                )

            if isinstance(exc, ExecutionBridgeError):
                return CapabilityExecutionResult(
                    request_id=request.request_id,
                    operation_type=request.operation_type,
                    capability_kind=CapabilityKind.SERVICE,
                    capability_id="plan.execute",
                    success=False,
                    state="EXECUTION_FAILED",
                    user_message="Plan execution failed in the controlled execution layer.",
                    result_metadata={},
                    error_category="PLAN_EXECUTION_FAILED",
                    error_detail=str(exc),
                )

            return CapabilityExecutionResult(
                request_id=request.request_id,
                operation_type=request.operation_type,
                capability_kind=CapabilityKind.SERVICE,
                capability_id="plan.execute",
                success=False,
                state="EXECUTION_FAILED",
                user_message="Plan execution failed.",
                result_metadata={},
                error_category="PLAN_EXECUTION_FAILED",
                error_detail=str(exc),
            )

        result = execution.execution_result
        return CapabilityExecutionResult(
            request_id=request.request_id,
            operation_type=request.operation_type,
            capability_kind=CapabilityKind.SERVICE,
            capability_id="plan.execute",
            success=result.terminal_state.value == "COMPLETED",
            state=f"PLAN_{result.terminal_state.value}",
            user_message=(
                "Plan executed successfully."
                if result.terminal_state.value == "COMPLETED"
                else f"Plan execution ended with state {result.terminal_state.value}."
            ),
            result_metadata={
                "plan_id": execution.plan_id,
                "project_id": execution.project_id,
                "terminal_state": result.terminal_state.value,
                "total_tasks": result.total_tasks,
                "committed_tasks": result.committed_tasks,
                "failed_tasks": result.failed_tasks,
                "reason": result.reason,
            },
            service_result=result.model_dump(mode="json"),
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
