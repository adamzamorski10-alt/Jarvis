"""OpenRouter cloud AI provider."""
import httpx

from app.ai.providers.base import AIModel, AIProvider, ProviderHealth


class OpenRouterProviderError(RuntimeError):
    def __init__(self, status: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class OpenRouterProvider(AIProvider):
    name = "openrouter"

    def __init__(self, base_url: str, api_key: str | None, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise OpenRouterProviderError("provider_not_configured", "OpenRouter API key is not configured", retryable=False)
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ai-operator.local",
            "X-Title": "AI Operator",
        }

    def _map_http_error(self, response: httpx.Response) -> OpenRouterProviderError:
        message = "OpenRouter API request failed"
        try:
            error = response.json().get("error", {})
            if isinstance(error, dict) and error.get("message"):
                message = str(error["message"])
            elif isinstance(error, str):
                message = error
        except Exception:
            pass
        if response.status_code == 401:
            return OpenRouterProviderError("invalid_api_key", "OpenRouter authentication failed (invalid API key)", retryable=False)
        if response.status_code == 402:
            return OpenRouterProviderError("provider_quota_exceeded", "OpenRouter credits or quota limit reached", retryable=False)
        if response.status_code == 403:
            return OpenRouterProviderError("unauthorized", "OpenRouter request is not authorized", retryable=False)
        if response.status_code == 404:
            return OpenRouterProviderError("model_not_found", "OpenRouter model was not found", retryable=False)
        if response.status_code == 429:
            return OpenRouterProviderError("rate_limited", "OpenRouter rate limit reached", retryable=True)
        if response.status_code == 503:
            return OpenRouterProviderError("model_unavailable", "OpenRouter model or free router is temporarily unavailable", retryable=True)
        if response.status_code in {500, 502, 504}:
            return OpenRouterProviderError("server_error", "OpenRouter server error", retryable=True)
        return OpenRouterProviderError("unknown_error", message, retryable=False)

    async def health_check(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(status="not_configured", configured=False, message="OpenRouter API key is missing")
        try:
            await self.list_models()
            return ProviderHealth(status="ready", configured=True)
        except OpenRouterProviderError as exc:
            return ProviderHealth(status=exc.status, configured=True, message=str(exc))
        except httpx.TimeoutException:
            return ProviderHealth(status="timeout", configured=True, message="OpenRouter request timed out")
        except (httpx.ConnectError, httpx.NetworkError):
            return ProviderHealth(status="network_error", configured=True, message="OpenRouter network error")
        except Exception as exc:
            return ProviderHealth(status="unknown_error", configured=True, message=str(exc))

    async def list_models(self) -> list[AIModel]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.TimeoutException:
            raise OpenRouterProviderError("timeout", "OpenRouter models request timed out") from None
        except (httpx.ConnectError, httpx.NetworkError):
            raise OpenRouterProviderError("network_error", "OpenRouter network error") from None
        if response.status_code != 200:
            raise self._map_http_error(response)
        body = response.json()
        data = body.get("data")
        if not isinstance(data, list):
            raise OpenRouterProviderError("unknown_error", "Invalid OpenRouter models response")

        models = [AIModel(name=str(item.get("id"))) for item in data if item.get("id")]
        
        # Ensure openrouter/free router model is always included as an option
        has_free_router = any(m.name == "openrouter/free" for m in models)
        if not has_free_router:
            models.insert(0, AIModel(name="openrouter/free"))
        return models

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        payload = {"model": model, "messages": messages}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload)
        except httpx.TimeoutException:
            raise OpenRouterProviderError("timeout", "OpenRouter chat request timed out") from None
        except (httpx.ConnectError, httpx.NetworkError):
            raise OpenRouterProviderError("network_error", "OpenRouter network error") from None
        if response.status_code != 200:
            raise self._map_http_error(response)
        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except Exception:
            raise OpenRouterProviderError("unknown_error", "Invalid OpenRouter chat response") from None
        if not isinstance(content, str):
            raise OpenRouterProviderError("unknown_error", "Invalid OpenRouter chat content")
        return content
