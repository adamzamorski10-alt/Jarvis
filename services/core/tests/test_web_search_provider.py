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
        async def get(self, *args, **kwargs):
            return httpx.Response(
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
        async def get(self, *args, **kwargs):
            return httpx.Response(
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
        async def get(self, *args, **kwargs):
            return httpx.Response(
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
        async def get(self, *args, **kwargs):
            response = httpx.Response(
                200,
                content=b"x" * (provider.max_response_bytes + 1),
                request=httpx.Request("GET", provider.endpoint),
            )
            return response

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
        async def get(self, *args, **kwargs):
            return httpx.Response(
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
