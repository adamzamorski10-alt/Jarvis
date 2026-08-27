"""Kimi/Moonshot cloud AI provider."""
import httpx

from app.ai.providers.base import AIModel, AIProvider, ProviderHealth


class KimiProviderError(RuntimeError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


class KimiProvider(AIProvider):
    name = "kimi"

    def __init__(self, base_url: str, api_key: str | None, timeout: float = 45.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise KimiProviderError("not_configured", "Kimi API key is not configured")
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
            return KimiProviderError("invalid_api_key", "Kimi authentication failed")
        if response.status_code == 403:
            return KimiProviderError("unauthorized", "Kimi request is not authorized")
        if response.status_code == 404:
            return KimiProviderError("model_not_found", "Kimi model was not found")
        if response.status_code == 429:
            return KimiProviderError("rate_limited", "Kimi rate limit reached")
        if response.status_code >= 500:
            return KimiProviderError("server_error", "Kimi server error")
        return KimiProviderError("unknown_error", message)

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

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        payload = {"model": model, "messages": messages}
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
            content = body["choices"][0]["message"]["content"]
        except Exception:
            raise KimiProviderError("unknown_error", "Invalid Kimi chat response") from None
        if not isinstance(content, str):
            raise KimiProviderError("unknown_error", "Invalid Kimi chat content")
        return content
