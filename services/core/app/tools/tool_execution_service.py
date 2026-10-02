"""
Central Tool Execution Service.

Orchestrates the full tool execution lifecycle:
  ToolCall → ToolDefinition lookup → enabled check → argument validation →
  PermissionEngine → (ALLOW) → ToolExecutor → result validation → Audit

Guarantees:
- PermissionEngine is ALWAYS evaluated before any execution.
- DENY / ASK_USER / invalid arguments / unknown tool / disabled tool
  NEVER reach ToolExecutor.execute().
- Audit logging is fail-soft and never changes a permission decision.
- No dynamic imports, subprocess, eval, exec, or shell execution.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.logging import get_logger
from app.tools.audit_models import AuditEventType, make_audit_event
from app.tools.audit_service import AuditLogService
from app.tools.base import BaseTool
from app.tools.executor import ToolExecutor
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.models import (
    AutonomyMode,
    PermissionDecisionType,
    PermissionEvaluationRequest,
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    WorkspaceContext,
)
from app.tools.permission_engine import PermissionEngine
from app.tools.registry import ToolRegistry
from app.tools.validation import ValidationError as ArgsValidationError
from app.tools.validation import validate_arguments, validate_result

logger = get_logger(__name__)

MAX_EXECUTION_TIME_MS = 30_000
MAX_ARGUMENTS_SIZE_BYTES = 64 * 1024
MAX_RESULT_SIZE_BYTES = 256 * 1024
MAX_TOOL_CALLS_PER_REQUEST = 10


class ToolExecutionService:
    """
    Central orchestrator for secure tool execution.

    This is the ONLY production component that should call both
    PermissionEngine.evaluate() and ToolExecutor.execute() for a given
    ToolCall.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        impl_registry: ToolImplementationRegistry,
        permission_engine: PermissionEngine,
        audit_service: AuditLogService,
        executor: ToolExecutor,
        *,
        max_execution_time_ms: float = MAX_EXECUTION_TIME_MS,
        max_arguments_size_bytes: int = MAX_ARGUMENTS_SIZE_BYTES,
        max_result_size_bytes: int = MAX_RESULT_SIZE_BYTES,
        max_tool_calls_per_request: int = MAX_TOOL_CALLS_PER_REQUEST,
    ) -> None:
        self._registry = registry
        self._impl_registry = impl_registry
        self._permission_engine = permission_engine
        self._audit_service = audit_service
        self._executor = executor
        self._max_execution_time_ms = max_execution_time_ms
        self._max_arguments_size_bytes = max_arguments_size_bytes
        self._max_result_size_bytes = max_result_size_bytes
        self._max_tool_calls_per_request = max_tool_calls_per_request

    def get_tool_definition(self, tool_id: str) -> ToolDefinition | None:
        """Return the canonical ToolDefinition for a tool_id, or None if not registered."""
        return self._registry.get(tool_id)

    async def execute_tool_call(
        self,
        tool_call: ToolCall,
        *,
        action: str | None = None,
        autonomy_mode: AutonomyMode,
        request_id: str | None = None,
        context: dict[str, Any] | None = None,
        approval_context: Any = None,
        actor_id: str | None = None,
        workspace_path: str | None = None,
    ) -> ToolExecutionResult:
        """
        Execute a single tool call through the full security pipeline.

        Order is enforced by code structure:
          1. definition lookup + enabled check
          2. argument validation
          3. PermissionEngine.evaluate()
          4. ONLY if ALLOW → ToolExecutor.execute()
          5. result validation
          6. audit

        When ``action`` is not supplied, it is resolved from the canonical
        ``ToolDefinition.action`` field looked up in the registry — never
        from the caller or from ``tool_id.split('.')``.
        """
        tool_id = tool_call.tool_id
        definition = self._registry.get(tool_id)

        # 1. Tool existence
        if definition is None:
            resolved_action = action or "unknown"
            await self._safe_audit(
                AuditEventType.TOOL_CALL_REQUESTED,
                request_id=request_id,
                tool_id=tool_id,
                action=resolved_action,
                reason="Tool is not registered in ToolRegistry.",
                arguments=tool_call.arguments,
            )
            return ToolExecutionResult(
                success=False,
                tool_id=tool_id,
                request_id=request_id,
                tool_call_id=tool_call.tool_call_id,
                status=ToolExecutionStatus.DENIED,
                error_code="tool_not_found",
                error_message=f"Tool '{tool_id}' is not registered.",
            )

        # Resolve canonical action from ToolDefinition
        resolved_action = action if action is not None else definition.action

        # 2. Enabled check
        if not self._registry.is_enabled(tool_id):
            await self._safe_audit(
                AuditEventType.TOOL_CALL_REQUESTED,
                request_id=request_id,
                tool_id=tool_id,
                action=resolved_action,
                reason="Tool is currently disabled.",
                arguments=tool_call.arguments,
            )
            return ToolExecutionResult(
                success=False,
                tool_id=tool_id,
                request_id=request_id,
                tool_call_id=tool_call.tool_call_id,
                status=ToolExecutionStatus.DENIED,
                error_code="tool_disabled",
                error_message=f"Tool '{tool_id}' is currently disabled.",
            )

        # 3. Argument validation
        try:
            validate_arguments(
                tool_call.arguments,
                definition.parameters_schema,
                max_size_bytes=definition.max_arguments_size_bytes or self._max_arguments_size_bytes,
            )
        except (ArgsValidationError, ValueError) as exc:
            await self._safe_audit(
                AuditEventType.TOOL_CALL_REQUESTED,
                request_id=request_id,
                tool_id=tool_id,
                action=resolved_action,
                reason=f"Invalid arguments: {exc}",
                arguments=tool_call.arguments,
            )
            return ToolExecutionResult(
                success=False,
                tool_id=tool_id,
                request_id=request_id,
                tool_call_id=tool_call.tool_call_id,
                status=ToolExecutionStatus.ERROR,
                error_code="invalid_arguments",
                error_message=str(exc),
            )

        # 4. Permission evaluation
        permission_request = PermissionEvaluationRequest(
            tool_id=tool_id,
            risk_level=definition.risk_level,
            action=resolved_action,
            autonomy_mode=autonomy_mode,
            arguments=tool_call.arguments,
            context=context or {"request_id": request_id} if request_id else (context or {}),
            approval_context=approval_context,
            actor_id=actor_id,
        )

        decision = await self._permission_engine.evaluate(permission_request)

        if decision.decision == PermissionDecisionType.DENY:
            await self._safe_audit(
                AuditEventType.TOOL_CALL_DENIED,
                request_id=request_id,
                tool_id=tool_id,
                action=resolved_action,
                reason=decision.reason,
                arguments=tool_call.arguments,
            )
            return self._build_permission_denied_result(
                tool_call, resolved_action, request_id, decision
            )

        if decision.decision == PermissionDecisionType.ASK_USER:
            await self._safe_audit(
                AuditEventType.APPROVAL_REQUESTED,
                request_id=request_id,
                tool_id=tool_id,
                action=resolved_action,
                reason=decision.reason,
                arguments=tool_call.arguments,
            )
            return self._build_ask_user_result(
                tool_call, resolved_action, request_id, decision
            )

        # 5. ALLOW → execution
        await self._safe_audit(
            AuditEventType.TOOL_EXECUTION_STARTED,
            request_id=request_id,
            tool_id=tool_id,
            action=resolved_action,
            reason="Execution started after permission grant.",
            arguments=tool_call.arguments,
        )

        execution_start = time.perf_counter()
        try:
            execution_context = self._build_execution_context(
                request_id=request_id or tool_call.request_id,
                actor_id=actor_id,
                tool_call_id=tool_call.tool_call_id,
                approval_context=approval_context,
                context=context,
                workspace_path=workspace_path,
            )

            raw_result = await asyncio.wait_for(
                self._executor.execute(tool_call, context=execution_context),
                timeout=self._max_execution_time_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - execution_start) * 1000.0
            await self._safe_audit(
                AuditEventType.TOOL_EXECUTION_TIMEOUT,
                request_id=request_id,
                tool_id=tool_id,
                action=resolved_action,
                reason=f"Tool execution exceeded {self._max_execution_time_ms}ms timeout.",
                success=False,
                error_code="timeout",
                execution_time_ms=elapsed_ms,
            )
            return ToolExecutionResult(
                success=False,
                tool_id=tool_id,
                request_id=request_id,
                tool_call_id=tool_call.tool_call_id,
                status=ToolExecutionStatus.TIMEOUT,
                error_code="timeout",
                error_message=f"Tool execution exceeded {self._max_execution_time_ms}ms timeout.",
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - execution_start) * 1000.0
            await self._safe_audit(
                AuditEventType.TOOL_EXECUTION_FAILED,
                request_id=request_id,
                tool_id=tool_id,
                action=resolved_action,
                reason=str(exc),
                success=False,
                error_code=getattr(exc, "code", "execution_error") or "execution_error",
                error_message=str(exc),
                execution_time_ms=elapsed_ms,
            )
            return ToolExecutionResult(
                success=False,
                tool_id=tool_id,
                request_id=request_id,
                tool_call_id=tool_call.tool_call_id,
                status=ToolExecutionStatus.ERROR,
                error_code=getattr(exc, "code", "execution_error") or "execution_error",
                error_message=str(exc),
                execution_time_ms=elapsed_ms,
            )

        elapsed_ms = (time.perf_counter() - execution_start) * 1000.0

        # 6. Result size check
        try:
            validate_result(
                raw_result.result if raw_result.success else None,
                definition.result_schema,
                max_size_bytes=self._max_result_size_bytes,
            )
        except (ArgsValidationError, ValueError) as exc:
            logger.warning(
                "Tool execution result validation failed",
                tool_id=tool_id,
                error=str(exc),
            )

        # 7. Audit success
        await self._safe_audit(
            AuditEventType.TOOL_EXECUTION_SUCCEEDED if raw_result.success else AuditEventType.TOOL_EXECUTION_FAILED,
            request_id=request_id,
            tool_id=tool_id,
            action=resolved_action,
            reason="Tool execution completed." if raw_result.success else raw_result.error_message or "Tool execution failed.",
            success=raw_result.success,
            error_code=raw_result.error_code,
            error_message=raw_result.error_message,
            execution_time_ms=elapsed_ms,
            result=raw_result.result if raw_result.success else None,
        )

        return raw_result

    async def execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        *,
        action: str | None = None,
        autonomy_mode: AutonomyMode,
        request_id: str | None = None,
        context: dict[str, Any] | None = None,
        approval_context: Any = None,
        actor_id: str | None = None,
    ) -> list[ToolExecutionResult]:
        """
        Execute multiple tool calls with a per-request limit.

        Each call still goes through the full security pipeline individually.
        """
        if len(tool_calls) > self._max_tool_calls_per_request:
            return [
                ToolExecutionResult(
                    success=False,
                    tool_id=call.tool_id,
                    request_id=request_id,
                    tool_call_id=call.tool_call_id,
                    status=ToolExecutionStatus.ERROR,
                    error_code="too_many_tool_calls",
                    error_message=(
                        f"Request exceeds maximum of {self._max_tool_calls_per_request} "
                        f"tool calls ({len(tool_calls)} requested)."
                    ),
                )
                for call in tool_calls
            ]

        results: list[ToolExecutionResult] = []
        for call in tool_calls:
            result = await self.execute_tool_call(
                call,
                action=action,
                autonomy_mode=autonomy_mode,
                request_id=request_id,
                context=context,
                approval_context=approval_context,
                actor_id=actor_id,
            )
            results.append(result)

            if result.status in (
                ToolExecutionStatus.DENIED,
                ToolExecutionStatus.CANCELLED,
                ToolExecutionStatus.TIMEOUT,
            ):
                logger.warning(
                    "Tool call stopped by security boundary",
                    tool_id=call.tool_id,
                    status=result.status.value,
                    error_code=result.error_code,
                )

        return results

    def _build_execution_context(
        self,
        *,
        request_id: str | None,
        actor_id: str | None,
        tool_call_id: str | None,
        approval_context: Any,
        context: dict[str, Any] | None,
        workspace_path: str | None,
    ) -> ToolExecutionContext:
        ctx_kwargs: dict[str, Any] = {
            "request_id": request_id,
            "actor_id": actor_id,
            "tool_call_id": tool_call_id,
            "approval_context": approval_context,
        }

        if context:
            ctx_kwargs.setdefault("request_id", context.get("request_id"))
            ctx_kwargs.setdefault("tool_call_id", context.get("tool_call_id"))
            ctx_kwargs.setdefault("actor_id", context.get("actor_id"))
            if workspace_path is None:
                workspace_path = context.get("workspace_path")

        if workspace_path:
            ctx_kwargs["workspace"] = WorkspaceContext(workspace_path=workspace_path)

        return ToolExecutionContext(**ctx_kwargs)

    def _build_permission_denied_result(
        self,
        tool_call: ToolCall,
        action: str,
        request_id: str | None,
        decision: Any,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            success=False,
            tool_id=tool_call.tool_id,
            request_id=request_id,
            tool_call_id=tool_call.tool_call_id,
            status=ToolExecutionStatus.DENIED,
            error_code="permission_denied",
            error_message=decision.reason,
        )

    def _build_ask_user_result(
        self,
        tool_call: ToolCall,
        action: str,
        request_id: str | None,
        decision: Any,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            success=False,
            tool_id=tool_call.tool_id,
            request_id=request_id,
            tool_call_id=tool_call.tool_call_id,
            status=ToolExecutionStatus.CANCELLED,
            error_code="ask_user",
            error_message=decision.reason,
        )

    async def _safe_audit(self, event_type: AuditEventType, **kwargs: Any) -> None:
        """Write audit event without ever raising to the caller."""
        try:
            await self._audit_service.record(event_type, **kwargs)
        except Exception as exc:
            logger.error(
                "Audit log write failed in ToolExecutionService",
                event_type=str(event_type),
                tool_id=kwargs.get("tool_id"),
                error=str(exc),
                exc_info=True,
            )
