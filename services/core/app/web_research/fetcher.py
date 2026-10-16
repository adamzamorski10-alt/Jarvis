"""Bounded public HTML retrieval using the DNS-pinning transport."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib

import httpx

from app.web_research.html_extractor import extract_html_text
from app.web_research.pinned_transport import PinnedPublicHTTPTransport
from app.web_research.url_policy import (
    ResearchURLPolicyError,
    validate_and_resolve_research_url,
)


class PageFetchError(RuntimeError):
    """Safe, stable error code for public-page retrieval failures."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RetrievedPage:
    source_id: str
    title: str
    url: str
    hostname: str
    text: str
    content_type: str
    byte_length: int


class PublicPageFetcher:
    """Fetch a single public HTML page with bounded bytes, time, and concurrency."""

    def __init__(
        self,
        *,
        max_body_bytes: int = 1_000_000,
        max_text_chars: int = 100_000,
        total_timeout_seconds: float = 12.0,
        max_concurrent_fetches: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not 1 <= max_body_bytes <= 1_000_000:
            raise ValueError("max_body_bytes must be between 1 and 1000000")
        if not 1 <= max_text_chars <= 100_000:
            raise ValueError("max_text_chars must be between 1 and 100000")
        if total_timeout_seconds <= 0 or max_concurrent_fetches < 1:
            raise ValueError("timeout and concurrency limits must be positive")
        self._max_body_bytes = max_body_bytes
        self._max_text_chars = max_text_chars
        self._total_timeout_seconds = total_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent_fetches)
        self._client = httpx.AsyncClient(
            transport=transport or PinnedPublicHTTPTransport(max_connections=max_concurrent_fetches),
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
            headers={
                "User-Agent": "AI-Operator-Research/1.0",
                "Accept": "text/html, application/xhtml+xml",
                "Accept-Encoding": "identity",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str, *, title_hint: str = "") -> RetrievedPage:
        # One total budget covers semaphore waiting, DNS preflight, and HTTP.
        # asyncio.to_thread cannot stop a resolver already running in a worker
        # thread, so retain the semaphore slot until that worker actually exits
        # if the caller's total budget expires during DNS.
        acquired = False
        resolution_task: asyncio.Task | None = None
        timed_out = False
        defer_semaphore_release = False
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                await self._semaphore.acquire()
                acquired = True
                resolution_task = asyncio.create_task(
                    asyncio.to_thread(validate_and_resolve_research_url, url)
                )
                target = await asyncio.shield(resolution_task)

                async with self._client.stream("GET", target.original_url) as response:
                    if 300 <= response.status_code < 400:
                        raise PageFetchError("redirect_not_allowed")
                    if not 200 <= response.status_code < 300:
                        raise PageFetchError("upstream_http_error")

                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise PageFetchError("unsupported_content_type")
                    content_encoding = response.headers.get("content-encoding", "identity").strip().lower()
                    if content_encoding not in {"", "identity"}:
                        raise PageFetchError("content_encoding_not_allowed")

                    length_header = response.headers.get("content-length")
                    if length_header is not None:
                        try:
                            declared_length = int(length_header)
                        except ValueError as exc:
                            raise PageFetchError("invalid_content_length") from exc
                        if declared_length < 0:
                            raise PageFetchError("invalid_content_length")
                        if declared_length > self._max_body_bytes:
                            raise PageFetchError("response_too_large")

                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(body) + len(chunk) > self._max_body_bytes:
                            raise PageFetchError("response_too_large")
                        body.extend(chunk)

            html = body.decode("utf-8", errors="replace")
            extracted = extract_html_text(
                html,
                max_html_chars=self._max_body_bytes,
                max_text_chars=self._max_text_chars,
            )
            title = extracted.title or title_hint.strip()[:512] or target.hostname
            source_id = "src_" + hashlib.sha256(target.original_url.encode("utf-8")).hexdigest()[:12]
            return RetrievedPage(
                source_id=source_id,
                title=title,
                url=target.original_url,
                hostname=target.hostname,
                text=extracted.text,
                content_type=content_type,
                byte_length=len(body),
            )
        except TimeoutError as exc:
            timed_out = True
            raise PageFetchError("total_timeout") from exc
        except PageFetchError:
            raise
        except ResearchURLPolicyError as exc:
            raise PageFetchError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise PageFetchError("upstream_timeout") from exc
        except httpx.HTTPError as exc:
            raise PageFetchError("upstream_request_failed") from exc
        except ValueError as exc:
            raise PageFetchError("html_extraction_failed") from exc
        finally:
            if acquired:
                if timed_out and resolution_task is not None and not resolution_task.done():
                    # A timed-out asyncio.to_thread await does not stop its worker.
                    # Keep this capacity slot occupied until the DNS worker exits.
                    def release_after_dns(task: asyncio.Task) -> None:
                        # Retrieve a late resolver exception to avoid an
                        # unobserved-task warning after the request timed out.
                        try:
                            task.exception()
                        except asyncio.CancelledError:
                            pass
                        self._semaphore.release()

                    resolution_task.add_done_callback(release_after_dns)
                    defer_semaphore_release = True
                if not defer_semaphore_release:
                    self._semaphore.release()



__all__ = ["PageFetchError", "PublicPageFetcher", "RetrievedPage"]
