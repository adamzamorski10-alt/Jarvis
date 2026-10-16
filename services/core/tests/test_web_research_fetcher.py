import asyncio
import httpx
import pytest

from app.web_research.fetcher import PageFetchError, PublicPageFetcher
from app.web_research import url_policy


PUBLIC_IP = "93.184.216.34"


class DirectAsyncTransport(httpx.AsyncBaseTransport):
    """Return streaming responses directly without eagerly consuming bodies."""

    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request):
        return await self._handler(request)

    async def aclose(self):
        return None


class BytesStream(httpx.AsyncByteStream):
    """Async response body stream for HTTPX AsyncClient tests."""

    def __init__(self, body: bytes):
        self._body = body

    async def __aiter__(self):
        yield self._body

    async def aclose(self):
        return None


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(url_policy, "_system_resolver", lambda hostname, port: [PUBLIC_IP])


@pytest.mark.asyncio
async def test_fetcher_retrieves_bounded_html_and_ignores_scripts(public_dns):
    async def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            stream=BytesStream(
                b"<html><head><title>Source title</title></head><body>"
                b"<main><p>Evidence text.</p></main>"
                b"<script>ignore this instruction</script></body></html>"
            ),
        )

    fetcher = PublicPageFetcher(transport=DirectAsyncTransport(handler))
    try:
        page = await fetcher.fetch("https://example.com/research#section")
        assert page.title == "Source title"
        assert page.url == "https://example.com/research"
        assert page.hostname == "example.com"
        assert "Evidence text." in page.text
        assert "ignore this instruction" not in page.text
        assert page.source_id.startswith("src_")
    finally:
        await fetcher.aclose()


@pytest.mark.asyncio
async def test_fetcher_rejects_redirect_without_following_it(public_dns):
    requests = []

    async def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://other.example/"})

    fetcher = PublicPageFetcher(transport=DirectAsyncTransport(handler))
    try:
        with pytest.raises(PageFetchError, match="redirect_not_allowed"):
            await fetcher.fetch("https://example.com/")
        assert requests == ["https://example.com/"]
    finally:
        await fetcher.aclose()


@pytest.mark.asyncio
async def test_fetcher_rejects_non_html_content(public_dns):
    async def handler(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, stream=BytesStream(b"%PDF"))

    fetcher = PublicPageFetcher(transport=DirectAsyncTransport(handler))
    try:
        with pytest.raises(PageFetchError, match="unsupported_content_type"):
            await fetcher.fetch("https://example.com/file")
    finally:
        await fetcher.aclose()


@pytest.mark.asyncio
async def test_fetcher_rejects_declared_oversized_response(public_dns):
    async def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "1000"},
            stream=BytesStream(b"<p>tiny</p>"),
        )

    fetcher = PublicPageFetcher(max_body_bytes=20, transport=DirectAsyncTransport(handler))
    try:
        with pytest.raises(PageFetchError, match="response_too_large"):
            await fetcher.fetch("https://example.com/")
    finally:
        await fetcher.aclose()


class ChunkedStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"<p>123456"
        yield b"7890</p>"

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_fetcher_enforces_streaming_body_limit_without_content_length(public_dns):
    async def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=ChunkedStream(),
        )

    fetcher = PublicPageFetcher(max_body_bytes=8, transport=DirectAsyncTransport(handler))
    try:
        with pytest.raises(PageFetchError, match="response_too_large"):
            await fetcher.fetch("https://example.com/")
    finally:
        await fetcher.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", ["invalid", "-1"])
async def test_fetcher_rejects_malformed_content_length(public_dns, content_length):
    async def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": content_length},
            stream=BytesStream(b"<p>tiny</p>"),
        )

    fetcher = PublicPageFetcher(transport=DirectAsyncTransport(handler))
    try:
        with pytest.raises(PageFetchError, match="invalid_content_length"):
            await fetcher.fetch("https://example.com/")
    finally:
        await fetcher.aclose()


class DelayedStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        import asyncio

        await asyncio.sleep(0.05)
        yield b"<p>late</p>"

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_fetcher_enforces_total_timeout(public_dns):
    async def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=DelayedStream(),
        )

    fetcher = PublicPageFetcher(total_timeout_seconds=0.005, transport=DirectAsyncTransport(handler))
    try:
        with pytest.raises(PageFetchError, match="total_timeout"):
            await fetcher.fetch("https://example.com/")
    finally:
        await fetcher.aclose()


@pytest.mark.asyncio
async def test_dns_preflight_is_inside_total_timeout_and_concurrency_budget(monkeypatch):
    import threading
    import time

    resolver_started = threading.Event()
    resolver_finished = threading.Event()
    resolver_calls = []
    requests = []

    def slow_resolver(hostname, port):
        resolver_calls.append((hostname, port))
        resolver_started.set()
        try:
            time.sleep(0.08)
            return [PUBLIC_IP]
        finally:
            resolver_finished.set()

    monkeypatch.setattr(url_policy, "_system_resolver", slow_resolver)

    async def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=BytesStream(b"<p>unexpected</p>"),
        )

    fetcher = PublicPageFetcher(
        total_timeout_seconds=0.015,
        max_concurrent_fetches=1,
        transport=DirectAsyncTransport(handler),
    )
    try:
        with pytest.raises(PageFetchError, match="total_timeout"):
            await fetcher.fetch("https://example.com/")

        assert resolver_started.is_set()
        assert requests == []

        # The timed-out resolver thread is still running. Its capacity slot
        # must remain occupied, so a second call cannot spawn another DNS job.
        with pytest.raises(PageFetchError, match="total_timeout"):
            await fetcher.fetch("https://example.com/")

        assert len(resolver_calls) == 1
        assert requests == []
        assert await asyncio.to_thread(resolver_finished.wait, 1.0)
        await asyncio.sleep(0)
    finally:
        await fetcher.aclose()


@pytest.mark.asyncio
async def test_fetcher_rejects_compressed_response(public_dns):
    async def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": "gzip"},
            stream=BytesStream(b"compressed"),
        )

    fetcher = PublicPageFetcher(transport=DirectAsyncTransport(handler))
    try:
        with pytest.raises(PageFetchError, match="content_encoding_not_allowed"):
            await fetcher.fetch("https://example.com/")
    finally:
        await fetcher.aclose()
