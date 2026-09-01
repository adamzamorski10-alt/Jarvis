"""Tests for Ollama provider tool support."""
import json
import pytest
import httpx

from app.ai.providers.ollama import OllamaProvider


ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def mock_async_client(monkeypatch, handler):
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: ORIGINAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), timeout=k.get("timeout")),
    )


@pytest.mark.asyncio
async def test_ollama_chat_with_tools(monkeypatch):
    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen:test"}]})
        if request.url.path == "/api/chat":
            body = json.loads(await request.aread())
            assert "tools" in body
            assert body["tools"][0]["function"]["name"] == "files.read_file"
            return httpx.Response(200, json={
                "model": "qwen:test",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "files.read_file",
                                "arguments": '{"path": "test.txt"}',
                            },
                        }
                    ],
                },
                "done": True,
                "done_reason": "stop",
            })
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)
    provider = OllamaProvider("http://ollama.test")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "files.read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]
    response = await provider.chat("qwen:test", [{"role": "user", "content": "Read test.txt"}], tools=tools)
    assert response.content is None
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_id == "files.read_file"
    assert response.tool_calls[0].arguments == {"path": "test.txt"}
    assert response.tool_calls[0].request_id == "call_1"
    assert response.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_ollama_chat_without_tools(monkeypatch):
    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen:test"}]})
        if request.url.path == "/api/chat":
            body = json.loads(await request.aread())
            assert "tools" not in body
            return httpx.Response(200, json={
                "model": "qwen:test",
                "message": {"role": "assistant", "content": "Hello"},
                "done": True,
                "done_reason": "stop",
            })
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)
    provider = OllamaProvider("http://ollama.test")
    response = await provider.chat("qwen:test", [{"role": "user", "content": "Hi"}])
    assert response.content == "Hello"
    assert response.tool_calls is None
    assert response.finish_reason == "stop"
