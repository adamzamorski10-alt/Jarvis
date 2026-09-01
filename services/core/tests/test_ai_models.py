"""Tests for AI models."""
import pytest

from app.ai.models import AIResponse
from app.tools.models import ToolCall


def test_ai_response_empty():
    response = AIResponse()
    assert response.content is None
    assert response.tool_calls is None
    assert response.finish_reason is None


def test_ai_response_with_content():
    response = AIResponse(content="Hello")
    assert response.content == "Hello"
    assert response.tool_calls is None
    assert response.finish_reason is None


def test_ai_response_with_tool_calls():
    tool_calls = [
        ToolCall(tool_id="files.read_file", arguments={"path": "test.txt"}, request_id="req-1")
    ]
    response = AIResponse(content=None, tool_calls=tool_calls, finish_reason="tool_calls")
    assert response.content is None
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_id == "files.read_file"
    assert response.finish_reason == "tool_calls"


def test_ai_response_serialization():
    response = AIResponse(content="Hi", finish_reason="stop")
    data = response.model_dump()
    assert data["content"] == "Hi"
    assert data["finish_reason"] == "stop"
    assert data["tool_calls"] is None
