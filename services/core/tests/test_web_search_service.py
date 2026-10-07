import pytest

from app.web_search.models import WebSearchRequest, WebSearchResponse, WebSearchResult
from app.web_search.service import WebSearchService
from app.web_search.providers.base import WebSearchProviderError


class FakeProvider:
    name = "fake"

    async def search(self, request):
        return WebSearchResponse(
            query=request.query,
            provider="fake",
            results=[WebSearchResult(title="Example", url="https://example.com", rank=1)],
        )


@pytest.mark.asyncio
async def test_service_returns_provider_result():
    service = WebSearchService(FakeProvider())
    result = await service.search(WebSearchRequest(query="hello"))
    assert result.results[0].url == "https://example.com"


@pytest.mark.asyncio
async def test_service_audits_without_raw_query():
    class Audit:
        def __init__(self): self.calls = []
        async def record(self, *args, **kwargs): self.calls.append(kwargs)

    audit = Audit()
    service = WebSearchService(FakeProvider(), audit)
    await service.search(WebSearchRequest(query="private phrase"), request_id="req_1")
    assert audit.calls
    assert all("private phrase" not in str(call) for call in audit.calls)


@pytest.mark.asyncio
async def test_web_search_service_rejects_provider_identity_mismatch():
    class Provider:
        name = "expected"

        async def search(self, request):
            return WebSearchResponse(
                query=request.query,
                provider="unexpected",
                results=[WebSearchResult(title="x", url="https://example.com", rank=1)],
            )

    with pytest.raises(WebSearchProviderError) as exc:
        await WebSearchService(Provider()).search(WebSearchRequest(query="test"))
    assert exc.value.code == "provider_identity_mismatch"


@pytest.mark.asyncio
async def test_web_search_service_rejects_unsafe_provider_url():
    class Provider:
        name = "test"

        async def search(self, request):
            return WebSearchResponse(
                query=request.query,
                provider="test",
                results=[WebSearchResult(title="x", url="javascript:alert(1)", rank=1)],
            )

    with pytest.raises(WebSearchProviderError) as exc:
        await WebSearchService(Provider()).search(WebSearchRequest(query="test"))
    assert exc.value.code == "invalid_result_url"


@pytest.mark.asyncio
async def test_web_search_service_rejects_provider_result_overflow():
    class Provider:
        name = "test"

        async def search(self, request):
            return WebSearchResponse(
                query=request.query,
                provider="test",
                results=[
                    WebSearchResult(title="one", url="https://example.com/1", rank=1),
                    WebSearchResult(title="two", url="https://example.com/2", rank=2),
                ],
            )

    with pytest.raises(WebSearchProviderError) as exc:
        await WebSearchService(Provider()).search(WebSearchRequest(query="test", count=1))
    assert exc.value.code == "provider_result_limit_exceeded"


@pytest.mark.asyncio
async def test_web_search_service_validates_rank_before_deduplication():
    class Provider:
        name = "test"

        async def search(self, request):
            return WebSearchResponse(
                query=request.query,
                provider="test",
                results=[
                    WebSearchResult(title="valid", url="https://example.com/a", rank=1),
                    WebSearchResult(title="invalid-duplicate", url="https://EXAMPLE.com/a/", rank=99),
                ],
            )

    with pytest.raises(WebSearchProviderError) as exc:
        await WebSearchService(Provider()).search(WebSearchRequest(query="test", count=2))
    assert exc.value.code == "invalid_result_rank"


@pytest.mark.asyncio
async def test_web_search_service_audit_failure_does_not_mask_success():
    class BrokenAudit:
        async def record(self, *args, **kwargs):
            raise RuntimeError("audit backend unavailable")

    result = await WebSearchService(FakeProvider(), BrokenAudit()).search(
        WebSearchRequest(query="test"),
        request_id="req_success",
    )
    assert result.results[0].url == "https://example.com"


@pytest.mark.asyncio
async def test_web_search_service_audit_failure_does_not_mask_provider_failure():
    class BrokenAudit:
        async def record(self, *args, **kwargs):
            raise RuntimeError("audit backend unavailable")

    class BrokenProvider:
        name = "test"

        async def search(self, request):
            raise WebSearchProviderError("provider_timeout", "Web search timed out.", True)

    with pytest.raises(WebSearchProviderError) as exc:
        await WebSearchService(BrokenProvider(), BrokenAudit()).search(
            WebSearchRequest(query="test"),
            request_id="req_failure",
        )
    assert exc.value.code == "provider_timeout"
