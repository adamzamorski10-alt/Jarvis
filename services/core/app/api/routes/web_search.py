"""Authenticated Web Search API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.ai.services.provider_manager import AIConfigError, ProviderManager
from app.api.schemas.web_search import WebSearchConfigRequest, WebSearchConfigResponse
from app.dependencies import get_provider_manager, get_web_search_service
from app.identity.auth import require_authenticated
from app.web_search.models import WebSearchRequest, WebSearchResponse
from app.web_search.service import WebSearchService

router = APIRouter(prefix="/web")


@router.get("/search", response_model=WebSearchResponse)
async def search_web(
    request: Request,
    q: str,
    count: int = 10,
    country: str = "PL",
    search_lang: str = "pl",
    freshness: str | None = None,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    service: WebSearchService | None = Depends(get_web_search_service),
):
    ctx = require_authenticated(request)
    if service is None:
        raise HTTPException(status_code=503, detail="Web search is not configured")
    try:
        return await service.search(
            WebSearchRequest(
                query=q,
                count=count,
                country=country,
                search_lang=search_lang,
                freshness=freshness,
            ),
            request_id=x_request_id,
        )
    except Exception as exc:
        code = getattr(exc, "code", "web_search_failed")
        status = 429 if code == "provider_rate_limited" else 502
        raise HTTPException(status_code=status, detail={"code": code, "message": str(exc)}) from exc


@router.get("/search/config", response_model=WebSearchConfigResponse)
async def get_web_search_config(
    request: Request,
    providers: ProviderManager = Depends(get_provider_manager),
):
    require_authenticated(request)
    config = await providers.get_config()
    return WebSearchConfigResponse(
        provider=str(config["web_search_provider"]),
        api_key_configured=bool(config["web_search_api_key_configured"]),
        api_key_hint=config.get("web_search_api_key_hint"),
    )


@router.put("/search/config", response_model=WebSearchConfigResponse)
async def update_web_search_config(
    request: Request,
    body: WebSearchConfigRequest,
    providers: ProviderManager = Depends(get_provider_manager),
):
    require_authenticated(request)
    try:
        config = await providers.update_web_search_config(body.provider, body.api_key)
    except AIConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WebSearchConfigResponse(
        provider=str(config["web_search_provider"]),
        api_key_configured=bool(config["web_search_api_key_configured"]),
        api_key_hint=config.get("web_search_api_key_hint"),
    )
