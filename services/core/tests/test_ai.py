"""Tests for AI provider foundation."""
import pytest
import httpx

from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.kimi import KimiProvider, KimiProviderError


ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def mock_async_client(monkeypatch, handler):
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: ORIGINAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), timeout=k.get("timeout")),
    )


@pytest.mark.asyncio
async def test_ollama_health_success(monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={"version": "0.1.0"})

    mock_async_client(monkeypatch, handler)
    health = await OllamaProvider("http://ollama.test").health_check()
    assert health.status == "online"
    assert health.version == "0.1.0"


@pytest.mark.asyncio
async def test_ai_status_unreachable(client, monkeypatch):
    async def handler(request):
        raise httpx.ConnectError("offline", request=request)

    mock_async_client(monkeypatch, handler)
    response = await client.get("/api/ai/status")
    assert response.status_code == 200
    assert response.json()["provider_status"] == "unreachable"
    assert response.json()["ai_ready"] is False


@pytest.mark.asyncio
async def test_ai_models_success(client, monkeypatch):
    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.0"})
        return httpx.Response(200, json={"models": [{"name": "qwen:test", "size": 10}]})

    mock_async_client(monkeypatch, handler)
    response = await client.get("/api/ai/models")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "qwen:test"


@pytest.mark.asyncio
async def test_ai_config_and_selected_model_persistence(client):
    response = await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "selected_model": "qwen:test",
    })
    assert response.status_code == 200
    assert response.json()["selected_model"] == "qwen:test"
    assert (await client.get("/api/ai/config")).json()["selected_model"] == "qwen:test"


@pytest.mark.asyncio
async def test_chat_without_selected_model(client):
    response = await client.post("/api/ai/chat", json={"message": "Hello"})
    assert response.status_code == 409
    assert "not ready" in response.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_chat_success_and_conversation_persistence(client, monkeypatch):
    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen:test"}]})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "Czesc!"}})

    mock_async_client(monkeypatch, handler)
    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "selected_model": "qwen:test",
    })
    response = await client.post("/api/ai/chat", json={"message": "Czesc"})
    assert response.status_code == 200
    body = response.json()
    assert body["message"]["content"] == "Czesc!"
    conversation_id = body["conversation"]["id"]
    loaded = await client.get(f"/api/ai/conversations/{conversation_id}")
    assert len(loaded.json()["messages"]) == 2


@pytest.mark.asyncio
async def test_invalid_conversation_id(client):
    response = await client.get("/api/ai/conversations/missing")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_kimi_not_configured():
    health = await KimiProvider("https://api.moonshot.ai/v1", None).health_check()
    assert health.status == "not_configured"
    assert health.configured is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "invalid_api_key"),
        (403, "unauthorized"),
        (404, "model_not_found"),
        (429, "rate_limited"),
        (500, "server_error"),
    ],
)
async def test_kimi_http_error_mapping(monkeypatch, status_code, expected):
    async def handler(request):
        return httpx.Response(status_code, json={"error": {"message": "secret failure"}})

    mock_async_client(monkeypatch, handler)
    with pytest.raises(KimiProviderError) as exc:
        await KimiProvider("https://api.moonshot.ai/v1", "sk-test").list_models()
    assert exc.value.status == expected
    assert "sk-test" not in str(exc.value)


@pytest.mark.asyncio
async def test_kimi_chat_success(monkeypatch):
    seen = {}

    async def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello from kimi"}}]})

    mock_async_client(monkeypatch, handler)
    result = await KimiProvider("https://api.moonshot.ai/v1", "sk-test").chat("kimi-k3", [{"role": "user", "content": "hi"}])
    assert result == "hello from kimi"
    assert seen["auth"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_kimi_config_does_not_leak_api_key(client):
    response = await client.put("/api/ai/config", json={
        "provider": "kimi",
        "kimi_base_url": "https://api.moonshot.ai/v1",
        "kimi_api_key": "sk-secret-1234",
        "selected_model": "kimi-k3",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["kimi_api_key_configured"] is True
    assert body["kimi_api_key_hint"] == "****1234"
    assert "sk-secret-1234" not in str(body)

    loaded = (await client.get("/api/ai/config")).json()
    assert "kimi_api_key" not in loaded
    assert "sk-secret-1234" not in str(loaded)


@pytest.mark.asyncio
async def test_kimi_status_and_model_listing(client, monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={"data": [{"id": "kimi-k3"}]})

    mock_async_client(monkeypatch, handler)
    await client.put("/api/ai/config", json={
        "provider": "kimi",
        "kimi_base_url": "https://api.moonshot.ai/v1",
        "kimi_api_key": "sk-secret-1234",
        "selected_model": "kimi-k3",
    })
    status = (await client.get("/api/ai/status")).json()
    assert status["provider"] == "kimi"
    assert status["provider_status"] == "ready"
    assert status["model_status"] == "ready"
    assert status["ai_ready"] is True

    models = (await client.get("/api/ai/models")).json()
    assert models[0]["name"] == "kimi-k3"
