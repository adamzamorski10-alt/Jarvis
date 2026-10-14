"""Stage 22.5 — hostile Web Search provider-boundary acceptance tests.

Provider data is untrusted input. The service must reject contract violations
before results can be propagated to Operator callers.
"""
import pytest

from app.web_search.models import WebSearchRequest, WebSearchResponse, WebSearchResult
from app.web_search.providers.base import WebSearchProviderError
from app.web_search.service import WebSearchService


class StaticProvider:
    name = "test-provider"

    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def search(self, request):
        self.calls += 1
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "https://trusted.example@evil.example/path",
        "https://user:password@evil.example/path",
        "//evil.example/path",
        "file:///etc/passwd",
        "https://:80/path",
        "https://[::1/path",
        "https://example.com/\npath",
    ],
)
async def test_service_rejects_deceptive_or_unsafe_result_urls(url):
    request = WebSearchRequest(query="security test")
    provider = StaticProvider(
        WebSearchResponse(
            query=request.query,
            provider="test-provider",
            results=[WebSearchResult(title="untrusted", url=url, rank=1)],
        )
    )

    with pytest.raises(WebSearchProviderError) as exc:
        await WebSearchService(provider).search(request)

    assert exc.value.code == "invalid_result_url"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_service_rejects_provider_query_identity_mismatch():
    request = WebSearchRequest(query="original query")
    provider = StaticProvider(
        WebSearchResponse(
            query="different query",
            provider="test-provider",
            results=[WebSearchResult(title="result", url="https://example.com", rank=1)],
        )
    )

    with pytest.raises(WebSearchProviderError) as exc:
        await WebSearchService(provider).search(request)

    assert exc.value.code == "provider_query_mismatch"


@pytest.mark.asyncio
async def test_service_rejects_invalid_rank_even_when_result_is_duplicate():
    request = WebSearchRequest(query="rank validation", count=2)
    provider = StaticProvider(
        WebSearchResponse.model_construct(
            query=request.query,
            provider="test-provider",
            results=[
                WebSearchResult(title="first", url="https://example.com/a", rank=1),
                WebSearchResult.model_construct(
                    title="malicious duplicate",
                    url="https://EXAMPLE.com/a/",
                    rank=999,
                ),
            ],
        )
    )

    with pytest.raises(WebSearchProviderError) as exc:
        await WebSearchService(provider).search(request)

    assert exc.value.code == "invalid_result_rank"


@pytest.mark.asyncio
async def test_service_deduplicates_equivalent_urls_and_reassigns_ranks():
    request = WebSearchRequest(query="deduplicate", count=3)
    provider = StaticProvider(
        WebSearchResponse(
            query=request.query,
            provider="test-provider",
            results=[
                WebSearchResult(title="first", url="https://EXAMPLE.com/a/#one", rank=1),
                WebSearchResult(title="duplicate", url="https://example.com/a/#two", rank=2),
                WebSearchResult(title="second", url="https://example.com/b", rank=3),
            ],
        )
    )

    result = await WebSearchService(provider).search(request)

    assert [item.url for item in result.results] == [
        "https://EXAMPLE.com/a/#one",
        "https://example.com/b",
    ]
    assert [item.rank for item in result.results] == [1, 2]


@pytest.mark.asyncio
async def test_service_does_not_store_raw_query_in_audit_events():
    class Audit:
        def __init__(self):
            self.events = []

        async def record(self, event_type, **kwargs):
            self.events.append((event_type, kwargs))

    secret_query = "private project codename sentinel"
    request = WebSearchRequest(query=secret_query)
    provider = StaticProvider(
        WebSearchResponse(
            query=request.query,
            provider="test-provider",
            results=[WebSearchResult(title="result", url="https://example.com", rank=1)],
        )
    )
    audit = Audit()

    await WebSearchService(provider, audit).search(request, request_id="req-hostile-audit")

    serialized_events = repr(audit.events)
    assert secret_query not in serialized_events
    assert "query_sha256" in serialized_events
    assert "query_length" in serialized_events
