"""Future provider placeholders."""
from app.ai.providers.base import AIProvider, ProviderHealth


class FutureProvider(AIProvider):
    def __init__(self, name: str) -> None:
        self.name = name

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(status="not_implemented", configured=False, message=f"{self.name} is planned for a future phase")

    async def list_models(self) -> list:
        return []

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        raise RuntimeError(f"{self.name} is not implemented")
