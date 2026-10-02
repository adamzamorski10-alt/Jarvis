"""
Tool implementations for AI Operator.
"""
from datetime import datetime, timezone

from app.tools.base import BaseTool
from app.tools.models import ToolCategory, ToolDefinition, ToolExecutionContext, ToolExecutionResult, ToolExecutionStatus, RiskLevel


class SystemGetTime(BaseTool):
    """Returns the current UTC time in ISO 8601 format."""

    @property
    def tool_id(self) -> str:
        return "system.get_time"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            tool_id="system.get_time",
            action="get_time",
            name="Get System Time",
            description="Returns the current UTC time in ISO 8601 format.",
            version="1.0.0",
            category=ToolCategory.SYSTEM,
            risk_level=RiskLevel.SAFE,
            parameters_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
            result_schema={
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                },
                "required": ["timestamp"],
            },
        )

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> Any:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
