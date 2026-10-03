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


def test_web_search_request_is_bounded():
    with pytest.raises(ValueError):
        WebSearchRequest(query="x", count=21)
    with pytest.raises(ValueError):
        WebSearchRequest(query="   ")
