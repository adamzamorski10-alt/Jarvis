"""Tests for Kimi provider tool support."""
import json
import pytest
import httpx

from app.ai.providers.kimi import KimiProvider


ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def mock_async_client(monkeypatch, handler):
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: ORIGINAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), timeout=k.get("timeout")),
    )


@pytest.mark.asyncio
async def test_kimi_chat_with_tools(monkeypatch):
    async def handler(request):
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(await request.aread())
            assert "tools" in body
            assert body["tools"][0]["function"]["name"] == "files.read_file"
            return httpx.Response(200, json={
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_kimi_1",
                                "type": "function",
                                "function": {
                                    "name": "files.read_file",
                                    "arguments": {"path": "test.txt"},
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }]
            })
        return httpx.Response(200, json={"data": []})

    mock_async_client(monkeypatch, handler)
    provider = KimiProvider("https://api.moonshot.ai/v1", "sk-test")
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
    response = await provider.chat("kimi-k3", [{"role": "user", "content": "Read test.txt"}], tools=tools)
    assert response.content is None
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_id == "files.read_file"
    assert response.tool_calls[0].arguments == {"path": "test.txt"}
    assert response.tool_calls[0].request_id == "call_kimi_1"
    assert response.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_kimi_chat_without_tools(monkeypatch):
    async def handler(request):
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(await request.aread())
            assert "tools" not in body
            return httpx.Response(200, json={
                "choices": [{
                    "message": {"role": "assistant", "content": "Hello from Kimi"},
                    "finish_reason": "stop",
                }]
            })
        return httpx.Response(200, json={"data": []})

    mock_async_client(monkeypatch, handler)
    provider = KimiProvider("https://api.moonshot.ai/v1", "sk-test")
    response = await provider.chat("kimi-k3", [{"role": "user", "content": "Hi"}])
    assert response.content == "Hello from Kimi"
    assert response.tool_calls is None
    assert response.finish_reason == "stop"
