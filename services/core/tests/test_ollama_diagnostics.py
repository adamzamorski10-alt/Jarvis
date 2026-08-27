"""Tests for Ollama diagnostic states and error mapping."""
import pytest
import httpx

from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.openrouter import OpenRouterProvider


ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def mock_async_client(monkeypatch, handler):
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: ORIGINAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), timeout=k.get("timeout")),
    )


@pytest.mark.asyncio
async def test_ollama_not_installed(monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: None)

    async def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    mock_async_client(monkeypatch, handler)
    health = await OllamaProvider("http://127.0.0.1:11434").health_check()

    assert health.status == "not_installed"
    assert health.installed is False
    assert health.service_running is False
    assert health.api_responding is False
    assert health.models_available is False


@pytest.mark.asyncio
async def test_ollama_service_stopped(monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\Users\test\AppData\Local\Programs\Ollama\ollama.exe")

    async def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    mock_async_client(monkeypatch, handler)
    health = await OllamaProvider("http://127.0.0.1:11434").health_check()

    assert health.status == "stopped"
    assert health.installed is True
    assert health.service_running is False
    assert health.api_responding is False
    assert health.models_available is False


@pytest.mark.asyncio
async def test_ollama_api_online_and_ready(monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\Users\test\AppData\Local\Programs\Ollama\ollama.exe")

    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:7b", "size": 4000000000}]})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)
    health = await OllamaProvider("http://127.0.0.1:11434").health_check()

    assert health.status == "ready"
    assert health.installed is True
    assert health.service_running is True
    assert health.api_responding is True
    assert health.models_available is True
    assert health.version == "0.5.1"


@pytest.mark.asyncio
async def test_ollama_online_but_no_models(monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\Users\test\AppData\Local\Programs\Ollama\ollama.exe")

    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)
    health = await OllamaProvider("http://127.0.0.1:11434").health_check()

    assert health.status == "no_models"
    assert health.installed is True
    assert health.service_running is True
    assert health.api_responding is True
    assert health.models_available is False


@pytest.mark.asyncio
async def test_ollama_chat_error_code_not_installed(client, monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: None)

    async def handler(request):
        raise httpx.ConnectError("Offline", request=request)

    mock_async_client(monkeypatch, handler)

    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "selected_model": "qwen2.5-coder:7b",
    })

    response = await client.post("/api/ai/chat", json={"message": "Hello"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "ollama_not_installed"
    assert detail["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_ollama_chat_error_code_service_stopped(client, monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\path\to\ollama.exe")

    async def handler(request):
        raise httpx.ConnectError("Offline", request=request)

    mock_async_client(monkeypatch, handler)

    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "selected_model": "qwen2.5-coder:7b",
    })

    response = await client.post("/api/ai/chat", json={"message": "Hello"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "ollama_service_stopped"
    assert detail["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_ollama_chat_error_code_no_models(client, monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\path\to\ollama.exe")

    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)

    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "selected_model": "qwen2.5-coder:7b",
    })

    response = await client.post("/api/ai/chat", json={"message": "Hello"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "ollama_no_models"
    assert detail["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_ollama_chat_error_code_model_missing(client, monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\path\to\ollama.exe")

    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3:latest"}]})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)

    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "selected_model": "nonexistent-model",
    })

    response = await client.post("/api/ai/chat", json={"message": "Hello"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "ollama_model_missing"
    assert detail["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_openrouter_regression_check(client, monkeypatch):
    async def handler(request):
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{"id": "openrouter/free"}]})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "OpenRouter OK"}}]})

    mock_async_client(monkeypatch, handler)

    await client.put("/api/ai/config", json={
        "provider": "openrouter",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_api_key": "sk-or-v1-test",
        "selected_model": "openrouter/free",
    })

    status_resp = await client.get("/api/ai/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["provider"] == "openrouter"

    chat_resp = await client.post("/api/ai/chat", json={"message": "Hello OpenRouter"})
    assert chat_resp.status_code == 200
    assert chat_resp.json()["message"]["content"] == "OpenRouter OK"


@pytest.mark.asyncio
async def test_system_status_diagnostics_extended(client, monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\path\to\ollama.exe")

    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen:7b"}]})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)

    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "selected_model": "qwen:7b",
    })

    resp = await client.get("/api/system/status")
    assert resp.status_code == 200
    ai = resp.json()["ai"]
    assert ai["installed"] is True
    assert ai["service_running"] is True
    assert ai["api_responding"] is True
    assert ai["models_available"] is True
