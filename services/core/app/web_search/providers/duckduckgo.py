"""Free web-search provider using DuckDuckGo's public HTML endpoint.

This adapter intentionally requires no API key. It is a best-effort provider:
the application contract remains provider-neutral so a different provider can
be selected later without changing the Operator capability.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from app.web_search.models import WebSearchRequest, WebSearchResponse, WebSearchResult
from app.web_search.providers.base import WebSearchProvider, WebSearchProviderError


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        classes = set((attrs_dict.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            if self._current is None:
                self._current = {"title": "", "url": attrs_dict.get("href", ""), "snippet": ""}
            self._field = "title"
        elif tag in {"a", "div"} and self._current is not None and (
            "result__snippet" in classes or "result__snippet" in (attrs_dict.get("class") or "")
        ):
            self._field = "snippet"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._field:
            self._current[self._field] += data

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag == "a" and self._field == "title":
            self._field = None
            return
        if tag == "div" and self._field == "snippet":
            if self._current.get("title", "").strip() and self._current.get("url", "").strip():
                self.results.append(self._current)
            self._current = None
            self._field = None


class DuckDuckGoSearchProvider(WebSearchProvider):
    name = "duckduckgo"
    endpoint = "https://html.duckduckgo.com/html/"
    max_response_bytes = 2 * 1024 * 1024

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        timeout = httpx.Timeout(10.0, connect=5.0)
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                headers={
                    "User-Agent": "AI-Operator/1.0 (+private-local-assistant)",
                    "Accept": "text/html,application/xhtml+xml",
                },
            ) as client:
                response = await client.get(
                    self.endpoint,
                    params={"q": request.query},
                )
        except httpx.TimeoutException as exc:
            raise WebSearchProviderError("provider_timeout", "Web search timed out.", True) from exc
        except httpx.RequestError as exc:
            raise WebSearchProviderError("provider_unavailable", "Web search provider is unavailable.", True) from exc

        if len(response.content) > self.max_response_bytes:
            raise WebSearchProviderError("provider_response_too_large", "Web search provider response is too large.")
        if response.status_code != 200:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise WebSearchProviderError(
                f"provider_http_{response.status_code}",
                "Web search provider returned an HTTP error.",
                retryable,
            )

        parser = _DuckDuckGoParser()
        try:
            parser.feed(response.text)
        except Exception as exc:
            raise WebSearchProviderError("invalid_provider_response", "Web search provider returned invalid HTML.", False) from exc

        results: list[WebSearchResult] = []
        for raw in parser.results:
            target = self._safe_target(raw.get("url", ""))
            if target is None:
                continue
            results.append(
                WebSearchResult(
                    title=raw.get("title", "").strip()[:512],
                    url=target,
                    snippet=raw.get("snippet", "").strip()[:4096],
                    source=urlsplit(target).hostname,
                    rank=len(results) + 1,
                )
            )
            if len(results) >= request.count:
                break

        return WebSearchResponse(
            query=request.query,
            provider=self.name,
            results=results,
            more_results_available=len(parser.results) > len(results),
        )

    @staticmethod
    def _safe_target(raw_url: str) -> str | None:
        if not raw_url:
            return None
        absolute = urljoin("https://html.duckduckgo.com", raw_url)
        parts = urlsplit(absolute)
        if parts.netloc == "html.duckduckgo.com":
            target = parse_qs(parts.query).get("uddg", [None])[0]
            if target:
                parts = urlsplit(target)
            else:
                return None
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return None
        if parts.username or parts.password:
            return None
        return parts._replace(fragment="").geturl()

