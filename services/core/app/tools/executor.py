"""
Central Tool Executor.

Executes only registered, enabled tool implementations.
Does NOT perform permission checks or audit logging.
Does NOT import modules dynamically based on tool_id.
"""
from __future__ import annotations

import time
from typing import Any

from app.tools.base import BaseTool
from app.tools.implementation_registry import ToolImplementationRegistry
from app.tools.models import ToolCall, ToolExecutionResult, ToolExecutionStatus
from app.tools.registry import ToolRegistry


class ToolExecutor:
    """
    Secure executor for pre-registered tool implementations.

    Guarantees:
    - Only tools registered in ToolRegistry can be executed.
    - Only tools with a registered BaseTool implementation can be executed.
    - Disabled tools are rejected.
    - No dynamic imports, shell execution, eval, or exec.
    - No permission checks or audit logging (handled by caller).
    """

    def __init__(
        self,
        registry: ToolRegistry,
        impl_registry: ToolImplementationRegistry,
    ) -> None:
        self._registry = registry
        self._impl_registry = impl_registry

    async def execute(
        self,
        tool_call: ToolCall,
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        """
        Execute a tool call safely.

        Args:
            tool_call: Validated ToolCall from the AI model.
            context: Optional execution context payload.

        Returns:
            ToolExecutionResult describing the outcome.
        """
        start = time.perf_counter()

        # 1. Validate tool existence
        definition = self._registry.get(tool_call.tool_id)
        if definition is None:
            return ToolExecutionResult(
                success=False,
                tool_id=tool_call.tool_id,
                request_id=tool_call.request_id,
                status=ToolExecutionStatus.ERROR,
                error_code="tool_not_found",
                error_message=f"Tool '{tool_call.tool_id}' is not registered.",
            )

        # 2. Validate enabled state
        if not self._registry.is_enabled(tool_call.tool_id):
            return ToolExecutionResult(
                success=False,
                tool_id=tool_call.tool_id,
                request_id=tool_call.request_id,
                status=ToolExecutionStatus.DENIED,
                error_code="tool_disabled",
                error_message=f"Tool '{tool_call.tool_id}' is currently disabled.",
            )

        # 3. Get implementation
        try:
            impl = self._impl_registry.get_implementation(tool_call.tool_id)
        except KeyError:
            return ToolExecutionResult(
                success=False,
                tool_id=tool_call.tool_id,
                request_id=tool_call.request_id,
                status=ToolExecutionStatus.ERROR,
                error_code="implementation_missing",
                error_message=f"No implementation registered for tool '{tool_call.tool_id}'.",
            )

        # 4. Execute
        try:
            exec_context = {"request_id": tool_call.request_id}
            if context:
                exec_context.update(context)

            raw_result = await impl.execute(
                arguments=tool_call.arguments,
                context=exec_context,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0

            return ToolExecutionResult(
                success=True,
                tool_id=tool_call.tool_id,
                request_id=tool_call.request_id,
                status=ToolExecutionStatus.SUCCESS,
                result=raw_result,
                execution_time_ms=elapsed_ms,
            )

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            error_code = getattr(exc, "code", "execution_error") or "execution_error"
            error_message = str(exc)

            return ToolExecutionResult(
                success=False,
                tool_id=tool_call.tool_id,
                request_id=tool_call.request_id,
                status=ToolExecutionStatus.ERROR,
                error_code=error_code,
                error_message=error_message,
                execution_time_ms=elapsed_ms,
            )
