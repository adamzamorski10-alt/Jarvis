"""Brave Search API adapter."""
from __future__ import annotations

import httpx

from app.web_search.models import WebSearchRequest, WebSearchResponse, WebSearchResult
from app.web_search.providers.base import WebSearchProvider


class WebSearchProviderError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class BraveSearchProvider(WebSearchProvider):
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, timeout_seconds: float = 10.0) -> None:
        key = api_key.strip() if isinstance(api_key, str) else ""
        if not key:
            raise ValueError("Brave Search API key is required")
        if not 2.0 <= timeout_seconds <= 30.0:
            raise ValueError("timeout_seconds must be between 2 and 30")
        self._api_key = key
        self._timeout = timeout_seconds

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        params = {
            "q": request.query,
            "count": request.count,
            "country": request.country,
            "search_lang": request.search_lang,
            "safesearch": "moderate",
        }
        if request.freshness:
            params["freshness"] = request.freshness

        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
                response = await client.get(
                    self.endpoint,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self._api_key,
                    },
                )
        except httpx.TimeoutException as exc:
            raise WebSearchProviderError("provider_timeout", "Web search timed out.", True) from exc
        except httpx.RequestError as exc:
            raise WebSearchProviderError("provider_unreachable", "Web search provider is unreachable.", True) from exc

        if response.status_code == 401:
            raise WebSearchProviderError("provider_unauthorized", "Web search API key is invalid.", False)
        if response.status_code == 403:
            raise WebSearchProviderError("provider_forbidden", "Web search provider rejected the request.", False)
        if response.status_code == 429:
            raise WebSearchProviderError("provider_rate_limited", "Web search rate limit was reached.", True)
        if response.status_code >= 500:
            raise WebSearchProviderError("provider_server_error", "Web search provider returned a server error.", True)
        if response.status_code >= 400:
            raise WebSearchProviderError("provider_request_failed", "Web search request was rejected.", False)

        try:
            data = response.json()
        except ValueError as exc:
            raise WebSearchProviderError("invalid_provider_response", "Web search provider returned invalid JSON.") from exc

        web = data.get("web") or {}
        raw_results = web.get("results") or []
        results: list[WebSearchResult] = []
        for index, item in enumerate(raw_results[:request.count], 1):
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not url or not (url.startswith("http://") or url.startswith("https://")):
                continue
            profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
            results.append(
                WebSearchResult(
                    title=title[:512],
                    url=url[:4096],
                    snippet=str(item.get("description") or "").strip()[:4096],
                    source=(str(profile.get("long_name") or "").strip()[:256] or None),
                    rank=index,
                )
            )

        return WebSearchResponse(
            query=request.query,
            provider=self.name,
            results=results,
            more_results_available=bool((data.get("query") or {}).get("more_results_available")),
        )
