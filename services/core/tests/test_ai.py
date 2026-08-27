"""Tests for AI provider foundation."""
import pytest
import httpx

from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.kimi import KimiProvider, KimiProviderError
from app.ai.providers.openrouter import OpenRouterProvider, OpenRouterProviderError


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
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen:test"}]})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)
    health = await OllamaProvider("http://ollama.test").health_check()
    assert health.status in {"online", "ready"}
    assert health.version == "0.1.0"


@pytest.mark.asyncio
async def test_ai_status_unreachable(client, monkeypatch):
    async def handler(request):
        raise httpx.ConnectError("offline", request=request)

    mock_async_client(monkeypatch, handler)
    response = await client.get("/api/ai/status")
    assert response.status_code == 200
    assert response.json()["provider_status"] in {"unreachable", "stopped", "not_installed"}
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
    msg = response.json()["detail"]["message"]
    assert "No AI model selected" in msg or "not ready" in msg


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


@pytest.mark.asyncio
async def test_openrouter_not_configured():
    health = await OpenRouterProvider("https://openrouter.ai/api/v1", None).health_check()
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
async def test_openrouter_http_error_mapping(monkeypatch, status_code, expected):
    async def handler(request):
        return httpx.Response(status_code, json={"error": {"message": "openrouter failure"}})

    mock_async_client(monkeypatch, handler)
    with pytest.raises(OpenRouterProviderError) as exc:
        await OpenRouterProvider("https://openrouter.ai/api/v1", "sk-or-test").list_models()
    assert exc.value.status == expected
    assert "sk-or-test" not in str(exc.value)


@pytest.mark.asyncio
async def test_openrouter_models_includes_free_router(monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={"data": [{"id": "meta-llama/llama-3.3-70b-instruct:free"}]})

    mock_async_client(monkeypatch, handler)
    models = await OpenRouterProvider("https://openrouter.ai/api/v1", "sk-or-test").list_models()
    model_names = [m.name for m in models]
    assert "openrouter/free" in model_names
    assert "meta-llama/llama-3.3-70b-instruct:free" in model_names


@pytest.mark.asyncio
async def test_openrouter_chat_success(monkeypatch):
    seen = {}

    async def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["referer"] = request.headers.get("http-referer")
        seen["title"] = request.headers.get("x-title")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello from openrouter"}}]})

    mock_async_client(monkeypatch, handler)
    result = await OpenRouterProvider("https://openrouter.ai/api/v1", "sk-or-secret").chat(
        "openrouter/free", [{"role": "user", "content": "hi"}]
    )
    assert result == "hello from openrouter"
    assert seen["auth"] == "Bearer sk-or-secret"
    assert seen["referer"] == "https://ai-operator.local"
    assert seen["title"] == "AI Operator"


@pytest.mark.asyncio
async def test_openrouter_config_does_not_leak_api_key(client):
    response = await client.put("/api/ai/config", json={
        "provider": "openrouter",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_api_key": "sk-or-v1-abcdef123456",
        "selected_model": "openrouter/free",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["openrouter_api_key_configured"] is True
    assert body["openrouter_api_key_hint"] == "****3456"
    assert "abcdef123456" not in str(body)

    loaded = (await client.get("/api/ai/config")).json()
    assert "openrouter_api_key" not in loaded
    assert "abcdef123456" not in str(loaded)


@pytest.mark.asyncio
async def test_openrouter_clear_api_key(client):
    await client.put("/api/ai/config", json={
        "provider": "openrouter",
        "openrouter_api_key": "sk-or-v1-secret9999",
    })
    loaded = (await client.get("/api/ai/config")).json()
    assert loaded["openrouter_api_key_configured"] is True

    # Clear key by submitting empty string
    await client.put("/api/ai/config", json={
        "provider": "openrouter",
        "openrouter_api_key": "",
    })
    cleared = (await client.get("/api/ai/config")).json()
    assert cleared["openrouter_api_key_configured"] is False
    assert cleared["openrouter_api_key_hint"] is None


@pytest.mark.asyncio
async def test_openrouter_chat_flow_and_free_router_selection(client, monkeypatch):
    async def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "meta-llama/llama-3.3-70b-instruct:free"}]})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "Free router answer"}}]})
        return httpx.Response(404)

    mock_async_client(monkeypatch, handler)
    await client.put("/api/ai/config", json={
        "provider": "openrouter",
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_api_key": "sk-or-valid-key-0000",
        "selected_model": "openrouter/free",
    })

    status = (await client.get("/api/ai/status")).json()
    assert status["provider"] == "openrouter"
    assert status["provider_status"] == "ready"
    assert status["model_status"] == "ready"
    assert status["ai_ready"] is True

    chat_resp = await client.post("/api/ai/chat", json={"message": "What is AI Operator?"})
    assert chat_resp.status_code == 200
    body = chat_resp.json()
    assert body["message"]["content"] == "Free router answer"
    conversation_id = body["conversation"]["id"]

    # Verify persistence
    conv = (await client.get(f"/api/ai/conversations/{conversation_id}")).json()
    assert len(conv["messages"]) == 2
    assert conv["provider"] == "openrouter"
    assert conv["model"] == "openrouter/free"


@pytest.mark.asyncio
async def test_provider_switching_preserves_conversation_history(client, monkeypatch):
    async def handler(request):
        if "/tags" in request.url.path or "/version" in request.url.path:
            return httpx.Response(200, json={"version": "0.1.0", "models": [{"name": "qwen:test"}]})
        if "/models" in request.url.path:
            return httpx.Response(200, json={"data": [{"id": "kimi-k3"}]})
        if "/chat" in request.url.path or "/chat/completions" in request.url.path:
            return httpx.Response(200, json={"choices": [{"message": {"content": "Resp"}}], "message": {"role": "assistant", "content": "Resp"}})
        return httpx.Response(200)

    mock_async_client(monkeypatch, handler)

    # 1. Create conversation with Ollama
    await client.put("/api/ai/config", json={"provider": "ollama", "selected_model": "qwen:test"})
    c1 = (await client.post("/api/ai/chat", json={"message": "Msg 1"})).json()
    cid1 = c1["conversation"]["id"]

    # 2. Switch to OpenRouter
    await client.put("/api/ai/config", json={"provider": "openrouter", "openrouter_api_key": "sk-test", "selected_model": "openrouter/free"})
    c2 = (await client.post("/api/ai/chat", json={"message": "Msg 2"})).json()
    cid2 = c2["conversation"]["id"]

    # 3. Verify both conversations exist and are unaffected
    conv1 = (await client.get(f"/api/ai/conversations/{cid1}")).json()
    conv2 = (await client.get(f"/api/ai/conversations/{cid2}")).json()
    assert conv1["provider"] == "ollama"
    assert conv2["provider"] == "openrouter"
    assert len(conv1["messages"]) == 2
    assert len(conv2["messages"]) == 2


@pytest.mark.asyncio
async def test_openrouter_chat_structured_error_on_429(client, monkeypatch):
    async def handler(request):
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}})
        return httpx.Response(200, json={"data": []})

    mock_async_client(monkeypatch, handler)
    await client.put("/api/ai/config", json={
        "provider": "openrouter",
        "openrouter_api_key": "sk-or-valid",
        "selected_model": "openrouter/free",
    })

    resp = await client.post("/api/ai/chat", json={"message": "Test rate limit"})
    assert resp.status_code == 409
    body = resp.json()
    assert "error" in body["detail"]
    assert body["detail"]["error"]["code"] == "rate_limited"
    assert body["detail"]["error"]["provider"] == "openrouter"
    assert body["detail"]["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_openrouter_chat_structured_error_on_503(client, monkeypatch):
    async def handler(request):
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(503, json={"error": {"message": "Model is temporarily overloaded"}})
        return httpx.Response(200, json={"data": []})

    mock_async_client(monkeypatch, handler)
    await client.put("/api/ai/config", json={
        "provider": "openrouter",
        "openrouter_api_key": "sk-or-valid",
        "selected_model": "openrouter/free",
    })

    resp = await client.post("/api/ai/chat", json={"message": "Test overloaded"})
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["error"]["code"] == "model_unavailable"
    assert body["detail"]["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_openrouter_chat_retry_does_not_duplicate_user_message(client, monkeypatch):
    attempts = 0

    async def handler(request):
        nonlocal attempts
        if request.url.path.endswith("/chat/completions"):
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "Success on retry"}}]})
        return httpx.Response(200, json={"data": []})

    mock_async_client(monkeypatch, handler)
    await client.put("/api/ai/config", json={
        "provider": "openrouter",
        "openrouter_api_key": "sk-or-valid",
        "selected_model": "openrouter/free",
    })

    # First attempt: fails with 429
    resp1 = await client.post("/api/ai/chat", json={
        "message": "Retryable prompt",
        "client_message_id": "client-msg-uuid-1",
    })
    assert resp1.status_code == 409

    # Fetch conversations to get the ID of the created conversation
    convs = (await client.get("/api/ai/conversations")).json()
    assert len(convs) == 1
    cid = convs[0]["id"]
    conv_after_fail = (await client.get(f"/api/ai/conversations/{cid}")).json()
    assert len(conv_after_fail["messages"]) == 1
    assert conv_after_fail["messages"][0]["role"] == "user"

    # Second attempt: retry exact same client_message_id
    resp2 = await client.post("/api/ai/chat", json={
        "conversation_id": cid,
        "message": "Retryable prompt",
        "client_message_id": "client-msg-uuid-1",
    })
    assert resp2.status_code == 200
    conv_after_success = (await client.get(f"/api/ai/conversations/{cid}")).json()

    # Verify: exactly 2 messages (1 user, 1 assistant), NO duplicate user message!
    assert len(conv_after_success["messages"]) == 2
    assert conv_after_success["messages"][0]["role"] == "user"
    assert conv_after_success["messages"][1]["role"] == "assistant"
    assert conv_after_success["messages"][1]["content"] == "Success on retry"


@pytest.mark.asyncio
async def test_two_identical_prompts_normally_sent_creates_two_messages(client, monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Echo answer"}}]})

    mock_async_client(monkeypatch, handler)
    await client.put("/api/ai/config", json={
        "provider": "openrouter",
        "openrouter_api_key": "sk-or-valid",
        "selected_model": "openrouter/free",
    })

    # Send 1
    r1 = await client.post("/api/ai/chat", json={"message": "Same prompt", "client_message_id": "id-1"})
    assert r1.status_code == 200
    cid = r1.json()["conversation"]["id"]

    # Send 2 (identical prompt, different client_message_id)
    r2 = await client.post("/api/ai/chat", json={"conversation_id": cid, "message": "Same prompt", "client_message_id": "id-2"})
    assert r2.status_code == 200

    conv = (await client.get(f"/api/ai/conversations/{cid}")).json()
    # Should have 4 messages: user 1, assistant 1, user 2, assistant 2
    assert len(conv["messages"]) == 4
    assert conv["messages"][0]["content"] == "Same prompt"
    assert conv["messages"][2]["content"] == "Same prompt"


