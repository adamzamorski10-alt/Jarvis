"""Brave Search API adapter."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx

from app.web_search.models import WebSearchRequest, WebSearchResponse, WebSearchResult
from app.web_search.providers.base import WebSearchProvider, WebSearchProviderError


class BraveSearchProvider(WebSearchProvider):
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"
    max_response_bytes = 2_000_000

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
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "GET",
                    self.endpoint,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self._api_key,
                    },
                ) as response:
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
                    if response.status_code != 200:
                        raise WebSearchProviderError("provider_request_failed", "Web search provider returned an unexpected status.", False)

                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > self.max_response_bytes:
                                raise WebSearchProviderError(
                                    "provider_response_too_large",
                                    "Web search provider response is too large.",
                                )
                        except ValueError as exc:
                            raise WebSearchProviderError(
                                "invalid_provider_response",
                                "Web search provider returned an invalid Content-Length.",
                                False,
                            ) from exc

                    # Enforce the response cap while streaming, not after a
                    # full untrusted response has already been buffered.
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if len(body) + len(chunk) > self.max_response_bytes:
                            raise WebSearchProviderError(
                                "provider_response_too_large",
                                "Web search provider response is too large.",
                            )
                        body.extend(chunk)
                    response_text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
        except httpx.TimeoutException as exc:
            raise WebSearchProviderError("provider_timeout", "Web search timed out.", True) from exc
        except httpx.RequestError as exc:
            raise WebSearchProviderError("provider_unreachable", "Web search provider is unreachable.", True) from exc

        try:
            data = json.loads(response_text)
        except (ValueError, TypeError, UnicodeError) as exc:
            raise WebSearchProviderError(
                "invalid_provider_response",
                "Web search provider returned invalid JSON.",
            ) from exc

        if not isinstance(data, dict):
            raise WebSearchProviderError("invalid_provider_response", "Web search provider returned an invalid response shape.")

        web = data.get("web")
        if web is None:
            raw_results = []
        elif isinstance(web, dict):
            raw_results = web.get("results") or []
        else:
            raise WebSearchProviderError("invalid_provider_response", "Web search provider returned an invalid web result shape.")

        if not isinstance(raw_results, list):
            raise WebSearchProviderError("invalid_provider_response", "Web search provider returned an invalid result list.")

        results: list[WebSearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not self._is_safe_result_url(url):
                continue
            profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
            results.append(
                WebSearchResult(
                    title=title[:512],
                    url=url[:4096],
                    snippet=str(item.get("description") or "").strip()[:4096],
                    source=(str(profile.get("long_name") or "").strip()[:256] or None),
                    rank=len(results) + 1,
                )
            )
            if len(results) >= request.count:
                break

        query_meta = data.get("query")
        more_results = bool(query_meta.get("more_results_available")) if isinstance(query_meta, dict) else False

        return WebSearchResponse(
            query=request.query,
            provider=self.name,
            results=results,
            more_results_available=more_results,
        )

    @staticmethod
    def _is_safe_result_url(url: str) -> bool:
        if not url or len(url) > 4096:
            return False
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return False
        # Userinfo makes displayed URLs deceptive and must never become a
        # trusted navigation target later.
        if parsed.username is not None or parsed.password is not None:
            return False
        return True
