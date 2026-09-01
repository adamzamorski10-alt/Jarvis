"""Tool definition and tool call normalization."""
import json
from typing import List, Union

from app.tools.models import ToolCall, ToolDefinition


class ToolDefinitionNormalizationError(ValueError):
    pass


class ToolCallNormalizationError(ValueError):
    pass


def normalize_tool_definitions(
    tools: List[Union[dict, ToolDefinition]],
) -> List[dict]:
    normalized = []
    for tool in tools:
        if isinstance(tool, ToolDefinition):
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.tool_id,
                        "description": tool.description,
                        "parameters": tool.parameters_schema or {},
                    },
                }
            )
        elif isinstance(tool, dict):
            if tool.get("type") != "function":
                raise ToolDefinitionNormalizationError(
                    f"Tool definition must have type 'function', got {tool.get('type')!r}"
                )
            function = tool.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ToolDefinitionNormalizationError(
                    "Tool definition must have a string 'function.name'"
                )
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "description": function.get("description", ""),
                        "parameters": function.get("parameters", {}),
                    },
                }
            )
        else:
            raise ToolDefinitionNormalizationError(
                f"Unsupported tool definition type: {type(tool).__name__}"
            )
    return normalized


def normalize_tool_calls(raw_calls: List[dict]) -> List[ToolCall]:
    normalized = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            raise ToolCallNormalizationError(f"Tool call must be a dict, got {type(raw).__name__}")
        if raw.get("type") != "function":
            raise ToolCallNormalizationError(
                f"Tool call must have type 'function', got {raw.get('type')!r}"
            )
        function = raw.get("function")
        if not isinstance(function, dict):
            raise ToolCallNormalizationError("Tool call must have a 'function' dict")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ToolCallNormalizationError("Tool call function name must be a non-empty string")
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ToolCallNormalizationError(
                    f"Invalid JSON in tool call arguments: {exc}"
                ) from exc
        if not isinstance(arguments, dict):
            raise ToolCallNormalizationError(
                f"Tool call arguments must be a dict or JSON string, got {type(arguments).__name__}"
            )
        raw_id = raw.get("id")
        normalized.append(
            ToolCall(tool_id=name, arguments=arguments, request_id=raw_id if isinstance(raw_id, str) else None)
        )
    return normalized


def format_assistant_tool_call_message(tool_calls: List[ToolCall]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.tool_call_id,
                "type": "function",
                "function": {
                    "name": call.tool_id,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in tool_calls
        ],
    }


def format_tool_result_message(
    tool_call: ToolCall,
    result: dict,
    *,
    provider_name: str = "openai",
) -> dict:
    safe_content = json.dumps(result, ensure_ascii=False, default=str)
    message: dict = {
        "role": "tool",
        "content": safe_content,
        "tool_call_id": tool_call.tool_call_id,
    }
    if provider_name == "ollama":
        message["tool_name"] = tool_call.tool_id
    return message


def serialize_tool_result_for_provider(
    execution_result: ToolExecutionResult,
) -> dict:
    if execution_result.success:
        return {
            "status": "success",
            "result": execution_result.result,
        }
    return {
        "status": "error",
        "error_code": execution_result.error_code or "unknown_error",
        "error_message": execution_result.error_message or "Tool execution failed.",
    }

