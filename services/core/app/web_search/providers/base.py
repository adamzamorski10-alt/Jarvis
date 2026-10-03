"""Provider contract for web search."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.web_search.models import WebSearchRequest, WebSearchResponse


class WebSearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        raise NotImplementedError
