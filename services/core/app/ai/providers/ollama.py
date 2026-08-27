"""Ollama AI provider integration."""
import httpx

from app.ai.providers.base import AIModel, AIProvider, ProviderHealth


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def health_check(self) -> ProviderHealth:
        if not self.base_url:
            return ProviderHealth(status="not_configured", configured=False, message="Ollama URL is not configured")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/version")
            if response.status_code != 200:
                return ProviderHealth(status="unreachable", configured=True, message=f"HTTP {response.status_code}")
            version = response.json().get("version")
            return ProviderHealth(status="online", configured=True, version=version)
        except (httpx.ConnectError, httpx.NetworkError):
            return ProviderHealth(status="unreachable", configured=True, message="Ollama is not reachable")
        except httpx.TimeoutException:
            return ProviderHealth(status="timeout", configured=True, message="Ollama health check timed out")
        except Exception as exc:
            return ProviderHealth(status="unreachable", configured=True, message=str(exc))

    async def list_models(self) -> list[AIModel]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.base_url}/api/tags")
        response.raise_for_status()
        body = response.json()
        models = body.get("models")
        if not isinstance(models, list):
            raise ValueError("Invalid Ollama models response")
        return [
            AIModel(
                name=str(item.get("name", "")),
                size=item.get("size") if isinstance(item.get("size"), int) else None,
                modified_at=item.get("modified_at") if isinstance(item.get("modified_at"), str) else None,
            )
            for item in models
            if item.get("name")
        ]

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        payload = {"model": model, "messages": messages, "stream": False}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
        response.raise_for_status()
        body = response.json()
        message = body.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("Invalid Ollama chat response")
        return message["content"]
