"""AI domain models."""
from typing import List, Optional

from pydantic import BaseModel

from app.tools.models import ToolCall, ToolExecutionResult


class AIResponse(BaseModel):
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: Optional[str] = None


class AgentLoopLimitConfig(BaseModel):
    max_iterations: int = 10
    max_tool_calls_total: int = 20
    max_tool_calls_per_request: int = 10


class ApprovalRequiredResult(BaseModel):
    tool_call: ToolCall
    execution_result: ToolExecutionResult
    message: str = "This action requires user approval."


class AgentLoopResult(BaseModel):
    final_response: AIResponse
    approval_required: bool = False
    pending_tool_call: Optional[ToolCall] = None
    pending_tool_result: Optional[ToolExecutionResult] = None
    approval_id: Optional[str] = None
    iterations_used: int = 0
    total_tool_calls: int = 0
    limit_reached: bool = False
    limit_reason: Optional[str] = None


