"""Tests for Ollama model management, pulling, progress, cancellation, deletion, and storage detection."""
import os
from pathlib import Path
import pytest
import httpx

from app.ai.providers.ollama import OllamaProvider, get_ollama_models_path, get_ollama_storage_drive
from app.ai.services.ollama_download_manager import download_manager


ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def mock_async_client(monkeypatch, handler):
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: ORIGINAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), timeout=k.get("timeout")),
    )


@pytest.fixture(autouse=True)
def reset_download_manager():
    download_manager.active = False
    download_manager.model = None
    download_manager.status = "idle"
    download_manager.phase = None
    download_manager.completed = None
    download_manager.total = None
    download_manager.percentage = None
    download_manager.message = None
    download_manager.error = None
    download_manager.started_at = None
    download_manager.finished_at = None
    yield


@pytest.mark.asyncio
async def test_ollama_models_path_env_detection(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODELS", r"F:\AI-Models\.ollama\models")
    path = get_ollama_models_path()
    assert str(path) == r"F:\AI-Models\.ollama\models"
    drive = get_ollama_storage_drive(path)
    assert drive == "F:"


@pytest.mark.asyncio
async def test_ollama_storage_info_endpoint(client, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODELS", r"F:\AI-Models\.ollama\models")

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
    })

    resp = await client.get("/api/ai/models/storage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["drive"] == "F:"
    assert data["location"] == r"F:\AI-Models\.ollama\models"
    assert isinstance(data["available_bytes"], int)
    assert isinstance(data["used_bytes"], int)


@pytest.mark.asyncio
async def test_ollama_pull_status_idle(client):
    resp = await client.get("/api/ai/models/pull/status")
    assert resp.status_code == 200
    assert resp.json()["active"] is False


@pytest.mark.asyncio
async def test_ollama_pull_model_success(client, monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\path\to\ollama.exe")

    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/pull":
            lines = [
                '{"status": "pulling manifest"}',
                '{"status": "downloading digestname", "completed": 500, "total": 1000}',
                '{"status": "verifying digestname"}',
                '{"status": "writing manifest"}',
                '{"status": "success"}',
            ]
            return httpx.Response(200, text="\n".join(lines))
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)

    # Mock high available space
    monkeypatch.setattr("shutil.disk_usage", lambda p: type("Usage", (), {"free": 100_000_000_000, "total": 200_000_000_000, "used": 100_000_000_000})())

    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
    })

    pull_resp = await client.post("/api/ai/models/pull", json={"model": "qwen3:8b"})
    assert pull_resp.status_code == 200
    assert pull_resp.json()["model"] == "qwen3:8b"

    # Poll status until task finishes
    import asyncio
    for _ in range(10):
        await asyncio.sleep(0.05)
        st = (await client.get("/api/ai/models/pull/status")).json()
        if not st.get("active") and st.get("status") == "success":
            break

    final_st = (await client.get("/api/ai/models/pull/status")).json()
    assert final_st["status"] == "success"
    assert final_st["percentage"] == 100.0


@pytest.mark.asyncio
async def test_ollama_pull_duplicate_blocked(client, monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\path\to\ollama.exe")

    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)

    download_manager.active = True
    download_manager.model = "llama3:8b"

    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
    })

    resp = await client.post("/api/ai/models/pull", json={"model": "qwen3:8b"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"]["code"] == "model_download_in_progress"


@pytest.mark.asyncio
async def test_ollama_insufficient_disk_space(client, monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\path\to\ollama.exe")

    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)

    # Mock low disk space (2 GB available, required is ~5.2 GB for qwen3:8b)
    monkeypatch.setattr("shutil.disk_usage", lambda p: type("Usage", (), {"free": 2_000_000_000, "total": 100_000_000_000, "used": 98_000_000_000})())

    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
    })

    resp = await client.post("/api/ai/models/pull", json={"model": "qwen3:8b"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"]["code"] == "insufficient_disk_space"
    assert detail["error"]["required_bytes"] == 5_200_000_000
    assert detail["error"]["available_bytes"] == 2_000_000_000


@pytest.mark.asyncio
async def test_ollama_cancel_pull(client):
    download_manager.active = True
    download_manager.model = "qwen3:8b"
    download_manager.status = "downloading"

    resp = await client.post("/api/ai/models/pull/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is False
    assert data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_ollama_delete_model(client, monkeypatch):
    monkeypatch.setattr("app.ai.providers.ollama.find_ollama_executable", lambda: r"C:\path\to\ollama.exe")

    async def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if request.url.path == "/api/delete" and request.method == "DELETE":
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)

    await client.put("/api/ai/config", json={
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "selected_model": "qwen3:8b",
    })

    del_resp = await client.delete("/api/ai/models/qwen3:8b")
    assert del_resp.status_code == 200
    assert del_resp.json()["model"] == "qwen3:8b"

    # Verify selected model was cleared when active model deleted
    cfg = (await client.get("/api/ai/config")).json()
    assert cfg["selected_model"] is None


@pytest.mark.asyncio
async def test_kimi_regression_check(client, monkeypatch):
    async def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "moonshot-v1-8k"}]})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Kimi OK"}}]})

    mock_async_client(monkeypatch, handler)

    await client.put("/api/ai/config", json={
        "provider": "kimi",
        "kimi_base_url": "https://api.moonshot.cn/v1",
        "kimi_api_key": "sk-test-kimi",
        "selected_model": "moonshot-v1-8k",
    })

    chat_resp = await client.post("/api/ai/chat", json={"message": "Hello Kimi"})
    assert chat_resp.status_code == 200
    assert chat_resp.json()["message"]["content"] == "Kimi OK"
