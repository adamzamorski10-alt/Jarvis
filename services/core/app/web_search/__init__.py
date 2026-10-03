"""Provider-neutral web search capability."""

from app.web_search.models import WebSearchRequest, WebSearchResult, WebSearchResponse
from app.web_search.service import WebSearchService

__all__ = ["WebSearchRequest", "WebSearchResult", "WebSearchResponse", "WebSearchService"]
