"""Kimi/Moonshot cloud AI provider."""
import httpx

from app.ai.models import AIResponse
from app.ai.normalization import normalize_tool_calls, normalize_tool_definitions
from app.ai.providers.base import AIModel, AIProvider, ProviderHealth


class KimiProviderError(RuntimeError):
    def __init__(self, status: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class KimiProvider(AIProvider):
    name = "kimi"

    def __init__(self, base_url: str, api_key: str | None, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise KimiProviderError("provider_not_configured", "Kimi API key is not configured", retryable=False)
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _map_http_error(self, response: httpx.Response) -> KimiProviderError:
        message = "Kimi API request failed"
        try:
            error = response.json().get("error", {})
            if isinstance(error, dict) and error.get("message"):
                message = str(error["message"])
        except Exception:
            pass
        if response.status_code == 401:
            return KimiProviderError("invalid_api_key", "Kimi authentication failed (invalid API key)", retryable=False)
        if response.status_code == 402:
            return KimiProviderError("provider_quota_exceeded", "Kimi credits or quota limit reached", retryable=False)
        if response.status_code == 403:
            return KimiProviderError("unauthorized", "Kimi request is not authorized", retryable=False)
        if response.status_code == 404:
            return KimiProviderError("model_not_found", "Kimi model was not found", retryable=False)
        if response.status_code == 429:
            return KimiProviderError("rate_limited", "Kimi rate limit reached", retryable=True)
        if response.status_code == 503:
            return KimiProviderError("model_unavailable", "Kimi model is temporarily unavailable", retryable=True)
        if response.status_code in {500, 502, 504}:
            return KimiProviderError("server_error", "Kimi server error", retryable=True)
        return KimiProviderError("unknown_error", message, retryable=False)

    async def health_check(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(status="not_configured", configured=False, message="Kimi API key is missing")
        try:
            await self.list_models()
            return ProviderHealth(status="ready", configured=True)
        except KimiProviderError as exc:
            return ProviderHealth(status=exc.status, configured=True, message=str(exc))
        except httpx.TimeoutException:
            return ProviderHealth(status="timeout", configured=True, message="Kimi request timed out")
        except (httpx.ConnectError, httpx.NetworkError):
            return ProviderHealth(status="network_error", configured=True, message="Kimi network error")
        except Exception as exc:
            return ProviderHealth(status="unknown_error", configured=True, message=str(exc))

    async def list_models(self) -> list[AIModel]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.TimeoutException:
            raise KimiProviderError("timeout", "Kimi models request timed out") from None
        except (httpx.ConnectError, httpx.NetworkError):
            raise KimiProviderError("network_error", "Kimi network error") from None
        if response.status_code != 200:
            raise self._map_http_error(response)
        body = response.json()
        data = body.get("data")
        if not isinstance(data, list):
            raise KimiProviderError("unknown_error", "Invalid Kimi models response")
        return [AIModel(name=str(item.get("id"))) for item in data if item.get("id")]

    async def chat(self, model: str, messages: list[dict[str, str]], tools: list[dict] | None = None) -> AIResponse:
        payload = {"model": model, "messages": messages}
        if tools:
            payload["tools"] = normalize_tool_definitions(tools)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload)
        except httpx.TimeoutException:
            raise KimiProviderError("timeout", "Kimi chat request timed out") from None
        except (httpx.ConnectError, httpx.NetworkError):
            raise KimiProviderError("network_error", "Kimi network error") from None
        if response.status_code != 200:
            raise self._map_http_error(response)
        body = response.json()
        try:
            choice = body["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if not isinstance(content, str) and content is not None:
                raise KimiProviderError("unknown_error", "Invalid Kimi chat content")
            tool_calls = None
            raw_tool_calls = message.get("tool_calls")
            if isinstance(raw_tool_calls, list) and raw_tool_calls:
                tool_calls = normalize_tool_calls(raw_tool_calls)
            finish_reason = choice.get("finish_reason") or ("tool_calls" if tool_calls else "stop")
            return AIResponse(content=content, tool_calls=tool_calls, finish_reason=finish_reason)
        except Exception:
            raise KimiProviderError("unknown_error", "Invalid Kimi chat response") from None
