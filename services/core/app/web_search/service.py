"""Application-owned Web Search service."""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

from app.web_search.models import WebSearchRequest, WebSearchResponse
from app.web_search.providers.base import WebSearchProvider, WebSearchProviderError


class WebSearchService:
    def __init__(self, provider: WebSearchProvider, audit_service=None) -> None:
        self._provider = provider
        self._audit = audit_service

    async def _record_audit(self, event_type, **kwargs) -> None:
        """Best-effort audit recording; never masks the capability outcome."""
        if self._audit is None:
            return
        try:
            await self._audit.record(event_type, **kwargs)
        except Exception:
            return

    async def search(self, request: WebSearchRequest, request_id: str | None = None) -> WebSearchResponse:
        from app.tools.audit_models import AuditEventType

        if request_id:
            await self._record_audit(
                AuditEventType.WEB_SEARCH_REQUESTED,
                reason="Web search capability invoked",
                request_id=request_id,
                tool_id="web.search",
                action="search",
                success=None,
                arguments={
                    "query_sha256": hashlib.sha256(request.query.encode("utf-8")).hexdigest(),
                    "query_length": len(request.query),
                    "count": request.count,
                    "country": request.country,
                },
            )
        try:
            result = await self._provider.search(request)
            if result.provider != self._provider.name:
                raise WebSearchProviderError("provider_identity_mismatch", "Web search provider returned an invalid provider identity.")
            if result.query != request.query:
                raise WebSearchProviderError("provider_query_mismatch", "Web search provider returned an invalid query identity.")
            if len(result.results) > request.count:
                raise WebSearchProviderError("provider_result_limit_exceeded", "Web search provider returned too many results.")

            seen = set()
            deduped = []
            for item in result.results:
                parts = urlsplit(item.url)
                if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
                    raise WebSearchProviderError("invalid_result_url", "Web search provider returned an invalid result URL.")
                if parts.username is not None or parts.password is not None:
                    raise WebSearchProviderError("invalid_result_url", "Web search provider returned an invalid result URL.")
                normalized_url = urlunsplit(
                    (
                        parts.scheme.lower(),
                        parts.netloc.lower(),
                        parts.path.rstrip("/") or "/",
                        parts.query,
                        "",
                    )
                )
                if normalized_url in seen:
                    continue
                seen.add(normalized_url)
                if item.rank < 1 or item.rank > request.count:
                    raise WebSearchProviderError("invalid_result_rank", "Web search provider returned an invalid result rank.")
                deduped.append(item.model_copy(update={"rank": len(deduped) + 1}))
            if len(deduped) != len(result.results):
                result = result.model_copy(update={"results": deduped})
        except Exception as exc:
            if request_id:
                await self._record_audit(
                    AuditEventType.WEB_SEARCH_FAILED,
                    reason="Web search capability failed",
                    request_id=request_id,
                    tool_id="web.search",
                    action="search",
                    success=False,
                    error_code=getattr(exc, "code", exc.__class__.__name__),
                    error_message="Web search provider request failed.",
                )
            raise

        if request_id:
            await self._record_audit(
                AuditEventType.WEB_SEARCH_SUCCEEDED,
                reason="Web search capability completed",
                request_id=request_id,
                tool_id="web.search",
                action="search",
                success=True,
                result={"provider": result.provider, "result_count": len(result.results)},
            )
        return result
