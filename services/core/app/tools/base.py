"""
Base interface for tool implementations.

Each concrete tool must provide:
- tool_id: str
- definition: ToolDefinition
- execute(arguments, context) -> Any

Implementations must not perform permission checks or audit logging.
"""
from abc import ABC, abstractmethod
from typing import Any

from app.tools.models import ToolDefinition, ToolExecutionContext


class BaseTool(ABC):
    """Abstract base class for all tool implementations."""

    @property
    @abstractmethod
    def tool_id(self) -> str:
        """Unique tool identifier matching the registered ToolDefinition.tool_id."""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Immutable ToolDefinition describing this tool."""

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> Any:
        """
        Execute the tool with the given arguments.

        Args:
            arguments: Key-value arguments from the AI-generated ToolCall.
            context: Typed execution context (request_id, actor_id, workspace, etc.).

        Returns:
            Arbitrary result payload. ToolExecutor will normalize this into
            a ToolExecutionResult.

        Raises:
            Any exception is caught by ToolExecutor and converted to
            ToolExecutionResult(status=ERROR).
        """
