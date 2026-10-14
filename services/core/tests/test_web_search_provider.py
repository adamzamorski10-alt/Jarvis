from contextlib import asynccontextmanager

import httpx
import pytest

from app.web_search.models import WebSearchRequest
from app.web_search.providers.brave import BraveSearchProvider, WebSearchProviderError


@pytest.mark.asyncio
async def test_brave_search_maps_results(monkeypatch):
    provider = BraveSearchProvider("secret")

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {"title": "One", "url": "https://example.com/1", "description": "First"},
                            {"title": "Two", "url": "https://example.com/2", "description": "Second", "profile": {"long_name": "Example"}},
                            {"title": "Bad", "url": "javascript:alert(1)", "description": "ignored"},
                        ]
                    },
                    "query": {"more_results_available": True},
                },
                request=httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search"),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await provider.search(WebSearchRequest(query="test"))
    assert result.provider == "brave"
    assert [item.title for item in result.results] == ["One", "Two"]
    assert [item.rank for item in result.results] == [1, 2]
    assert result.results[1].source == "Example"
    assert result.more_results_available is True


@pytest.mark.asyncio
async def test_brave_search_maps_auth_failure(monkeypatch):
    provider = BraveSearchProvider("secret")

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(
                401,
                json={},
                request=httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search"),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    with pytest.raises(WebSearchProviderError) as exc:
        await provider.search(WebSearchRequest(query="test"))
    assert exc.value.code == "provider_unauthorized"
    assert exc.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"web": "not-an-object"},
        {"web": {"results": "not-a-list"}},
        {"query": "not-an-object"},
    ],
)
async def test_brave_search_rejects_malformed_response(monkeypatch, payload):
    provider = BraveSearchProvider("secret")

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(
                200,
                json=payload,
                request=httpx.Request("GET", provider.endpoint),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    if payload == {"query": "not-an-object"}:
        # A response without web results is valid and simply means no results;
        # a malformed query metadata field must not crash the parser.
        result = await provider.search(WebSearchRequest(query="test"))
        assert result.results == []
        return
    with pytest.raises(WebSearchProviderError) as exc:
        await provider.search(WebSearchRequest(query="test"))
    assert exc.value.code == "invalid_provider_response"


@pytest.mark.asyncio
async def test_brave_search_rejects_oversized_response(monkeypatch):
    provider = BraveSearchProvider("secret")

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            response = httpx.Response(
                200,
                content=b"x" * (provider.max_response_bytes + 1),
                request=httpx.Request("GET", provider.endpoint),
            )
            yield response

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    with pytest.raises(WebSearchProviderError) as exc:
        await provider.search(WebSearchRequest(query="test"))
    assert exc.value.code == "provider_response_too_large"


@pytest.mark.asyncio
async def test_brave_search_rejects_deceptive_urls(monkeypatch):
    provider = BraveSearchProvider("secret")

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {"title": "JS", "url": "javascript:alert(1)"},
                            {"title": "Userinfo", "url": "https://user:pass@example.com/x"},
                            {"title": "Valid", "url": "https://example.com/x"},
                        ]
                    }
                },
                request=httpx.Request("GET", provider.endpoint),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await provider.search(WebSearchRequest(query="test"))
    assert [item.url for item in result.results] == ["https://example.com/x"]


def test_web_search_request_is_bounded():
    with pytest.raises(ValueError):
        WebSearchRequest(query="x", count=21)
    with pytest.raises(ValueError):
        WebSearchRequest(query="   ")


@pytest.mark.asyncio
async def test_duckduckgo_search_maps_snippet_and_unwraps_redirect(monkeypatch):
    from app.web_search.providers.duckduckgo import DuckDuckGoSearchProvider

    provider = DuckDuckGoSearchProvider()
    target = "https://example.com/article?q=1"
    html = f"""
    <div class="result">
      <h2><a class="result__a" href="/l/?uddg={target}">Example result</a></h2>
      <div class="result__snippet">Useful snippet.</div>
    </div>
    <div class="result">
      <h2><a class="result__a" href="https://example.org/second#fragment">Second</a></h2>
      <div class="result__snippet">Second snippet.</div>
    </div>
    """

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(200, text=html, request=httpx.Request("GET", provider.endpoint))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await provider.search(WebSearchRequest(query="test", count=2))
    assert [item.url for item in result.results] == [target, "https://example.org/second"]
    assert result.results[0].snippet == "Useful snippet."
    assert result.results[1].snippet == "Second snippet."


@pytest.mark.asyncio
async def test_duckduckgo_search_rejects_unsafe_and_duplicate_urls(monkeypatch):
    from app.web_search.providers.duckduckgo import DuckDuckGoSearchProvider

    provider = DuckDuckGoSearchProvider()
    html = """
    <div class="result"><a class="result__a" href="javascript:alert(1)">Bad</a><div class="result__snippet">bad</div></div>
    <div class="result"><a class="result__a" href="https://user:pass@example.com/a">Bad2</a><div class="result__snippet">bad</div></div>
    <div class="result"><a class="result__a" href="https://example.com/a#x">Good</a><div class="result__snippet">good</div></div>
    <div class="result"><a class="result__a" href="https://example.com/a#y">Duplicate</a><div class="result__snippet">dup</div></div>
    """

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(200, text=html, request=httpx.Request("GET", provider.endpoint))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await provider.search(WebSearchRequest(query="test"))
    assert [item.url for item in result.results] == ["https://example.com/a"]
