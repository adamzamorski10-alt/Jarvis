import pytest

from app.web_research.fetcher import PageFetchError, RetrievedPage
from app.web_research.models import ResearchRequest, ResearchSourceStatus
from app.web_research.service import ResearchService
from app.web_search.models import WebSearchResponse, WebSearchResult


class FakeSearchService:
    def __init__(self, results):
        self.results = results
        self.requests = []

    async def search(self, request, request_id=None):
        self.requests.append((request, request_id))
        return WebSearchResponse(query=request.query, provider="fake", results=self.results[: request.count])


class FakeFetcher:
    def __init__(self, pages=None, failures=None):
        self.pages = pages or {}
        self.failures = failures or []
        self.calls = []

    async def fetch(self, url, *, title_hint=""):
        self.calls.append(url)
        if url in self.failures:
            raise PageFetchError(self.failures[url])
        return self.pages[url]


def result(url, rank, title=None):
    return WebSearchResult(
        title=title or f"Title {rank}",
        url=url,
        snippet="search snippet",
        source="test",
        rank=rank,
    )


@pytest.mark.asyncio
async def test_research_service_bounds_sources_and_keeps_text_internal():
    candidates = [result(f"https://example{n}.com/article", n) for n in range(1, 5)]
    pages = {
        item.url: RetrievedPage(
            source_id=f"src_{n:012x}",
            title=item.title,
            url=item.url,
            hostname=f"example{n}.com",
            text=f"Evidence {n}",
            content_type="text/html",
            byte_length=50,
        )
        for n, item in enumerate(candidates, start=1)
    }
    search = FakeSearchService(candidates)
    fetcher = FakeFetcher(pages=pages)
    service = ResearchService(search, fetcher)

    bundle = await service.collect(ResearchRequest(query="research", max_sources=2), request_id="req-1")

    assert search.requests[0][0].count == 6
    assert search.requests[0][1] == "req-1"
    assert len(fetcher.calls) == 2
    assert len(bundle.response.sources) == 2
    assert len(bundle.evidence) == 2
    assert bundle.evidence[0].text == "Evidence 1"
    assert not hasattr(bundle.response.sources[0], "text")
    assert bundle.response.synthesized is False
    assert "Synthesis has not been run" in bundle.response.limitations[-1]


@pytest.mark.asyncio
async def test_research_service_deduplicates_urls_before_fetching():
    candidates = [
        result("https://example.com/article#one", 1),
        result("https://example.com/article#two", 2),
        result("https://other.example/article", 3),
    ]
    pages = {
        "https://example.com/article#one": RetrievedPage(
            source_id="src_012345abcdef",
            title="Example",
            url="https://example.com/article",
            hostname="example.com",
            text="Evidence",
            content_type="text/html",
            byte_length=20,
        ),
        "https://other.example/article": RetrievedPage(
            source_id="src_abcdef012345",
            title="Other",
            url="https://other.example/article",
            hostname="other.example",
            text="Other evidence",
            content_type="text/html",
            byte_length=20,
        ),
    }
    fetcher = FakeFetcher(pages=pages)
    bundle = await ResearchService(FakeSearchService(candidates), fetcher).collect(
        ResearchRequest(query="research", max_sources=3)
    )

    assert len(fetcher.calls) == 2
    assert len(bundle.response.sources) == 2


@pytest.mark.asyncio
async def test_research_service_reports_blocked_source_without_fabricating_evidence():
    candidate = result("https://127.0.0.1/internal", 1)
    fetcher = FakeFetcher(failures={candidate.url: "ip_literal_forbidden"})
    bundle = await ResearchService(FakeSearchService([candidate]), fetcher).collect(
        ResearchRequest(query="research", max_sources=1)
    )

    assert len(bundle.response.sources) == 1
    assert bundle.response.sources[0].status is ResearchSourceStatus.BLOCKED
    assert bundle.response.sources[0].error_code == "ip_literal_forbidden"
    assert bundle.evidence == ()
    assert bundle.response.synthesized is False


@pytest.mark.asyncio
async def test_research_service_retries_surplus_candidates_after_fetch_failure():
    candidates = [
        result("https://failed.example/article", 1),
        result("https://good2.example/article", 2),
        result("https://good3.example/article", 3),
        result("https://unused.example/article", 4),
    ]
    pages = {
        item.url: RetrievedPage(
            source_id=f"src_{n:012x}",
            title=item.title,
            url=item.url,
            hostname=item.url.split("/")[2],
            text=f"Evidence {n}",
            content_type="text/html",
            byte_length=30,
        )
        for n, item in enumerate(candidates[1:], start=2)
    }
    search = FakeSearchService(candidates)
    fetcher = FakeFetcher(pages=pages, failures={candidates[0].url: "upstream_timeout"})
    bundle = await ResearchService(search, fetcher).collect(
        ResearchRequest(query="research", max_sources=2)
    )

    assert search.requests[0][0].count == 6
    assert fetcher.calls == [
        candidates[0].url,
        candidates[1].url,
        candidates[2].url,
    ]
    assert len(bundle.response.sources) == 2
    assert all(source.status is ResearchSourceStatus.RETRIEVED for source in bundle.response.sources)
    assert [item.text for item in bundle.evidence] == ["Evidence 2", "Evidence 3"]
    assert all(source.url != candidates[0].url for source in bundle.response.sources)


@pytest.mark.asyncio
async def test_research_service_keeps_failure_status_when_no_replacement_is_available():
    candidates = [
        result("https://failed1.example/article", 1),
        result("https://failed2.example/article", 2),
        result("https://good.example/article", 3),
    ]
    pages = {
        candidates[2].url: RetrievedPage(
            source_id="src_abcdef012345",
            title="Good",
            url=candidates[2].url,
            hostname="good.example",
            text="Evidence",
            content_type="text/html",
            byte_length=20,
        )
    }
    fetcher = FakeFetcher(
        pages=pages,
        failures={
            candidates[0].url: "upstream_timeout",
            candidates[1].url: "upstream_http_error",
        },
    )
    bundle = await ResearchService(FakeSearchService(candidates), fetcher).collect(
        ResearchRequest(query="research", max_sources=3)
    )

    assert len(bundle.response.sources) == 3
    assert [source.status for source in bundle.response.sources] == [
        ResearchSourceStatus.RETRIEVED,
        ResearchSourceStatus.FAILED,
        ResearchSourceStatus.FAILED,
    ]
    assert len(bundle.evidence) == 1
