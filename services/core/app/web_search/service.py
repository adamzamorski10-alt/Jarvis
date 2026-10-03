"""Application-owned Web Search service."""
from __future__ import annotations

from app.web_search.models import WebSearchRequest, WebSearchResponse
from app.web_search.providers.base import WebSearchProvider


class WebSearchService:
    def __init__(self, provider: WebSearchProvider, audit_service=None) -> None:
        self._provider = provider
        self._audit = audit_service

    async def search(self, request: WebSearchRequest, request_id: str | None = None) -> WebSearchResponse:
        if request_id and self._audit is not None:
            from app.tools.audit_models import AuditEventType
            await self._audit.record(
                AuditEventType.WEB_SEARCH_REQUESTED,
                reason="Web search capability invoked",
                request_id=request_id,
                tool_id="web.search",
                action="search",
                success=None,
                arguments={"query": request.query, "count": request.count, "country": request.country},
            )
        try:
            result = await self._provider.search(request)
        except Exception as exc:
            if request_id and self._audit is not None:
                from app.tools.audit_models import AuditEventType
                await self._audit.record(
                    AuditEventType.WEB_SEARCH_FAILED,
                    reason="Web search capability failed",
                    request_id=request_id,
                    tool_id="web.search",
                    action="search",
                    success=False,
                    error_code=getattr(exc, "code", exc.__class__.__name__),
                    error_message=str(exc),
                )
            raise
        if request_id and self._audit is not None:
            from app.tools.audit_models import AuditEventType
            await self._audit.record(
                AuditEventType.WEB_SEARCH_SUCCEEDED,
                reason="Web search capability completed",
                request_id=request_id,
                tool_id="web.search",
                action="search",
                success=True,
                result={"provider": result.provider, "result_count": len(result.results)},
            )
        return result
