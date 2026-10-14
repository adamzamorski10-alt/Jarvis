from contextlib import asynccontextmanager

import httpx
import pytest

from app.web_search.models import WebSearchRequest
from app.web_search.providers.base import WebSearchProviderError
from app.web_search.providers.duckduckgo import DuckDuckGoSearchProvider


@pytest.mark.asyncio
async def test_duckduckgo_maps_and_unwraps_results(monkeypatch):
    provider = DuckDuckGoSearchProvider()
    html = """
    <html><a class="result__a" href="https://example.com/one">One</a>
    <div class="result__snippet">First result</div>
    <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Ftwo">Two</a>
    <div class="result__snippet">Second result</div></html>
    """

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(
                200,
                text=html,
                request=httpx.Request("GET", provider.endpoint),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await provider.search(WebSearchRequest(query="test", count=2))

    assert result.provider == "duckduckgo"
    assert [item.url for item in result.results] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert [item.rank for item in result.results] == [1, 2]


@pytest.mark.asyncio
async def test_duckduckgo_rejects_unsafe_targets(monkeypatch):
    provider = DuckDuckGoSearchProvider()
    html = """
    <a class="result__a" href="javascript:alert(1)">Bad</a>
    <a class="result__a" href="https://user:pass@example.com/x">Bad2</a>
    <a class="result__a" href="https://example.com/good#fragment">Good</a>
    """

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(
                200,
                text=html,
                request=httpx.Request("GET", provider.endpoint),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = await provider.search(WebSearchRequest(query="test"))

    assert [item.url for item in result.results] == ["https://example.com/good"]


@pytest.mark.asyncio
async def test_duckduckgo_maps_http_failure(monkeypatch):
    provider = DuckDuckGoSearchProvider()

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(
                429,
                text="",
                request=httpx.Request("GET", provider.endpoint),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    with pytest.raises(WebSearchProviderError) as exc:
        await provider.search(WebSearchRequest(query="test"))
    assert exc.value.code == "provider_http_429"
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_duckduckgo_response_size_header_is_enforced(monkeypatch):
    provider = DuckDuckGoSearchProvider()

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield httpx.Response(
                200,
                content=b"x",
                headers={"content-length": str(provider.max_response_bytes + 1)},
                request=httpx.Request("GET", provider.endpoint),
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    with pytest.raises(WebSearchProviderError) as exc:
        await provider.search(WebSearchRequest(query="test"))
    assert exc.value.code == "provider_response_too_large"


@pytest.mark.asyncio
async def test_duckduckgo_streaming_body_size_is_enforced_without_content_length(monkeypatch):
    provider = DuckDuckGoSearchProvider()
    oversized_body = b"x" * (provider.max_response_bytes + 1)

    class OversizedResponse:
        status_code = 200
        headers = {}
        encoding = "utf-8"

        async def aiter_bytes(self, chunk_size):
            for offset in range(0, len(oversized_body), chunk_size):
                yield oversized_body[offset:offset + chunk_size]

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            yield OversizedResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    with pytest.raises(WebSearchProviderError) as exc:
        await provider.search(WebSearchRequest(query="test"))
    assert exc.value.code == "provider_response_too_large"
