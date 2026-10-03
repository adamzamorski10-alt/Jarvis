import pytest

from app.web_search.models import WebSearchRequest, WebSearchResponse, WebSearchResult
from app.web_search.service import WebSearchService


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
