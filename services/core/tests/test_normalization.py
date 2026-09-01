"""Tests for AI tool normalization."""
import pytest

from app.ai.normalization import (
    ToolCallNormalizationError,
    ToolDefinitionNormalizationError,
    normalize_tool_calls,
    normalize_tool_definitions,
)
from app.tools.models import ToolCall, ToolDefinition, ToolCategory, RiskLevel


def test_normalize_tool_definitions_from_tool_definition():
    tool = ToolDefinition(
        tool_id="files.read_file",
        name="Read File",
        description="Read a file from disk",
        category=ToolCategory.FILES,
        risk_level=RiskLevel.LOW,
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    result = normalize_tool_definitions([tool])
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "files.read_file"
    assert result[0]["function"]["description"] == "Read a file from disk"
    assert result[0]["function"]["parameters"] == {"type": "object", "properties": {"path": {"type": "string"}}}


def test_normalize_tool_definitions_from_raw_dict():
    raw = {
        "type": "function",
        "function": {
            "name": "system.exec",
            "description": "Execute a command",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        },
    }
    result = normalize_tool_definitions([raw])
    assert len(result) == 1
    assert result[0]["function"]["name"] == "system.exec"


def test_normalize_tool_definitions_missing_type():
    with pytest.raises(ToolDefinitionNormalizationError):
        normalize_tool_definitions([{"function": {"name": "x"}}])


def test_normalize_tool_definitions_missing_name():
    with pytest.raises(ToolDefinitionNormalizationError):
        normalize_tool_definitions([{"type": "function", "function": {}}])


def test_normalize_tool_definitions_invalid_type():
    with pytest.raises(ToolDefinitionNormalizationError):
        normalize_tool_definitions([{"type": "retrieval", "function": {"name": "x"}}])


def test_normalize_tool_definitions_unsupported_type():
    with pytest.raises(ToolDefinitionNormalizationError):
        normalize_tool_definitions(["not a dict"])


def test_normalize_tool_calls_openai_format():
    raw = [
        {
            "id": "call_abc",
            "type": "function",
            "function": {
                "name": "files.read_file",
                "arguments": '{"path": "test.txt"}',
            },
        }
    ]
    result = normalize_tool_calls(raw)
    assert len(result) == 1
    assert isinstance(result[0], ToolCall)
    assert result[0].tool_id == "files.read_file"
    assert result[0].arguments == {"path": "test.txt"}
    assert result[0].request_id == "call_abc"


def test_normalize_tool_calls_arguments_already_dict():
    raw = [
        {
            "id": "call_xyz",
            "type": "function",
            "function": {
                "name": "system.exec",
                "arguments": {"command": "ls"},
            },
        }
    ]
    result = normalize_tool_calls(raw)
    assert result[0].arguments == {"command": "ls"}


def test_normalize_tool_calls_invalid_json():
    raw = [
        {
            "type": "function",
            "function": {
                "name": "x",
                "arguments": "not json",
            },
        }
    ]
    with pytest.raises(ToolCallNormalizationError):
        normalize_tool_calls(raw)


def test_normalize_tool_calls_missing_type():
    with pytest.raises(ToolCallNormalizationError):
        normalize_tool_calls([{"function": {"name": "x", "arguments": {}}}])


def test_normalize_tool_calls_missing_name():
    with pytest.raises(ToolCallNormalizationError):
        normalize_tool_calls([{"type": "function", "function": {"arguments": {}}}])


def test_normalize_tool_calls_non_dict_arguments():
    raw = [
        {
            "type": "function",
            "function": {
                "name": "x",
                "arguments": 123,
            },
        }
    ]
    with pytest.raises(ToolCallNormalizationError):
        normalize_tool_calls(raw)
