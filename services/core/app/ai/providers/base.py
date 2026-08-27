"""Base interfaces for AI providers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AIModel:
    name: str
    size: int | None = None
    modified_at: str | None = None


@dataclass(frozen=True)
class ProviderHealth:
    status: str
    configured: bool
    message: str | None = None
    version: str | None = None


class AIProvider(ABC):
    name: str

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        raise NotImplementedError

    @abstractmethod
    async def list_models(self) -> list[AIModel]:
        raise NotImplementedError

    @abstractmethod
    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError
