"""Provider contract for web search."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.web_search.models import WebSearchRequest, WebSearchResponse


class WebSearchProviderError(RuntimeError):
    """Provider-neutral, safe error contract for web-search adapters."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class WebSearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        raise NotImplementedError
