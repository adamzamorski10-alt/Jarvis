"""Authenticated Web Search API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from app.ai.services.provider_manager import AIConfigError, ProviderManager
from app.api.schemas.web_search import WebSearchConfigRequest, WebSearchConfigResponse
from app.dependencies import get_permission_engine, get_provider_manager, get_web_search_service
from app.identity.auth import require_authenticated
from app.tools.models import AutonomyMode, PermissionDecisionType, PermissionEvaluationRequest, RiskLevel
from app.operator_capability.executor import OperatorCapabilityExecutor
from app.web_search.models import WebSearchRequest, WebSearchResponse
from app.web_search.service import WebSearchService
from app.web_search.providers.base import WebSearchProviderError

router = APIRouter(prefix="/web")


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.get("/search", response_model=WebSearchResponse)
async def search_web(
    request: Request,
    response: Response,
    q: str = Query(..., min_length=1, max_length=600),
    count: int = Query(default=10, ge=1, le=20),
    country: str = Query(default="PL", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$"),
    search_lang: str = Query(default="pl", min_length=2, max_length=10, pattern=r"^[A-Za-z-]+$"),
    freshness: str | None = Query(default=None, max_length=32),
    x_request_id: str | None = Header(
        default=None,
        alias="X-Request-ID",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    service: WebSearchService | None = Depends(get_web_search_service),
    permission_engine = Depends(get_permission_engine),
):
    _no_store(response)
    ctx = require_authenticated(request)
    if service is None:
        raise HTTPException(status_code=503, detail="Web search is not configured")
    OperatorCapabilityExecutor.ensure_web_search_permission_definition()
    decision = await permission_engine.evaluate(
        PermissionEvaluationRequest(
            tool_id="web.search",
            risk_level=RiskLevel.SAFE,
            action="search",
            autonomy_mode=AutonomyMode.MANUAL,
            arguments={"query_length": len(q)},
            context={"request_id": x_request_id} if x_request_id else {},
            actor_id=ctx.actor_id if getattr(ctx, "actor_id", None) else None,
        )
    )
    if decision.decision != PermissionDecisionType.ALLOW:
        raise HTTPException(
            status_code=403,
            detail={"code": "permission_denied", "message": decision.reason, "rule_id": decision.rule_id},
        )
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
    except WebSearchProviderError as exc:
        status = 429 if exc.code == "provider_rate_limited" else 502
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "web_search_failed", "message": "Web search failed."},
        ) from exc


@router.get("/search/config", response_model=WebSearchConfigResponse)
async def get_web_search_config(
    request: Request,
    response: Response,
    providers: ProviderManager = Depends(get_provider_manager),
):
    _no_store(response)
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
    response: Response,
    body: WebSearchConfigRequest,
    providers: ProviderManager = Depends(get_provider_manager),
):
    _no_store(response)
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
