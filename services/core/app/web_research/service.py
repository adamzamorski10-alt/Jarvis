"""Bounded retrieval orchestration for Stage 22.7.

This layer collects evidence only. It does not synthesize claims or expose full
page text in the public response; synthesis is a later, separately gated stage.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from urllib.parse import urlsplit, urlunsplit

from app.web_search.models import WebSearchRequest
from app.web_search.service import WebSearchService
from app.web_research.fetcher import PageFetchError, PublicPageFetcher, RetrievedPage
from app.web_research.models import (
    ResearchRequest,
    ResearchResponse,
    ResearchSource,
    ResearchSourceStatus,
)


@dataclass(frozen=True)
class ResearchEvidence:
    source_id: str
    title: str
    url: str
    hostname: str
    text: str


@dataclass(frozen=True)
class ResearchBundle:
    response: ResearchResponse
    evidence: tuple[ResearchEvidence, ...]


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme.lower(),
            (parts.netloc or "").lower(),
            parts.path or "/",
            parts.query,
            "",
        )
    )


def _source_id(url: str) -> str:
    return "src_" + hashlib.sha256(_canonical_url(url).encode("utf-8")).hexdigest()[:12]


class ResearchService:
    """Search, select, retrieve, and preserve bounded source evidence."""

    def __init__(
        self,
        search_service: WebSearchService,
        page_fetcher: PublicPageFetcher,
        *,
        max_concurrent_sources: int = 3,
    ) -> None:
        if max_concurrent_sources < 1 or max_concurrent_sources > 5:
            raise ValueError("max_concurrent_sources must be between 1 and 5")
        self._search = search_service
        self._fetcher = page_fetcher
        self._semaphore = asyncio.Semaphore(max_concurrent_sources)

    async def collect(
        self,
        request: ResearchRequest,
        *,
        request_id: str | None = None,
    ) -> ResearchBundle:
        # Ask for a small surplus because some results may be inaccessible or
        # blocked by the public-page network policy.
        search_request = WebSearchRequest(
            query=request.query,
            count=min(20, request.max_sources * 3),
        )
        search_response = await self._search.search(search_request, request_id=request_id)

        selected = []
        seen: set[str] = set()
        for result in search_response.results:
            canonical = _canonical_url(result.url)
            if canonical in seen:
                continue
            seen.add(canonical)
            selected.append(result)
            if len(selected) >= request.max_sources:
                break

        async def retrieve(result) -> tuple[ResearchSource, ResearchEvidence | None]:
            fallback_id = _source_id(result.url)
            hostname = urlsplit(result.url).hostname or "unknown"
            async with self._semaphore:
                try:
                    page: RetrievedPage = await self._fetcher.fetch(
                        result.url,
                        title_hint=result.title,
                    )
                except PageFetchError as exc:
                    if exc.code in {
                        "unsupported_content_type",
                        "content_encoding_not_allowed",
                    }:
                        status = ResearchSourceStatus.UNSUPPORTED_CONTENT
                    elif (
                        exc.code in {
                            "ip_literal_forbidden",
                            "non_public_destination",
                            "dns_resolution_failed",
                            "dns_no_addresses",
                            "dns_returned_invalid_address",
                            "url_userinfo_forbidden",
                            "unsupported_url",
                            "non_default_port_forbidden",
                            "invalid_hostname",
                            "malformed_url",
                            "invalid_url_length",
                            "invalid_url_characters",
                            "missing_hostname",
                            "scoped_or_escaped_hostname_forbidden",
                        }
                        or exc.code.startswith("dns_")
                    ):
                        status = ResearchSourceStatus.BLOCKED
                    else:
                        status = ResearchSourceStatus.FAILED
                    source = ResearchSource(
                        source_id=fallback_id,
                        title=(result.title.strip() or hostname)[:512],
                        url=result.url,
                        hostname=hostname,
                        status=status,
                        extracted_text_length=0,
                        error_code=exc.code,
                    )
                    return source, None

            source = ResearchSource(
                source_id=page.source_id,
                title=page.title,
                url=page.url,
                hostname=page.hostname,
                status=ResearchSourceStatus.RETRIEVED,
                extracted_text_length=len(page.text),
            )
            evidence = ResearchEvidence(
                source_id=page.source_id,
                title=page.title,
                url=page.url,
                hostname=page.hostname,
                text=page.text,
            )
            return source, evidence

        retrieved_pairs = await asyncio.gather(*(retrieve(result) for result in selected))
        sources = [source for source, _ in retrieved_pairs]
        evidence = tuple(item for _, item in retrieved_pairs if item is not None)
        limitations: list[str] = []
        if not selected:
            limitations.append("The search provider returned no candidate sources.")
        if not evidence:
            limitations.append("No candidate source could be retrieved as public HTML.")
        limitations.append("Synthesis has not been run; this response contains source status only.")

        response = ResearchResponse(
            answer="",
            sources=sources,
            citations=[],
            limitations=limitations,
            synthesized=False,
        )
        return ResearchBundle(response=response, evidence=evidence)


__all__ = ["ResearchBundle", "ResearchEvidence", "ResearchService"]
