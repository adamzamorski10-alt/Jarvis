"""
History API routes.

Read-only endpoints exposing the existing HistoryService.
No new authorization framework. No mutation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas.history import HistoryEntryResponse, HistoryPageResponse
from app.dependencies import get_history_service
from app.events.history import HistoryService

router = APIRouter()


def _to_response(page: Any) -> HistoryPageResponse:
    entries = [HistoryEntryResponse.model_validate(e) for e in page.entries]
    return HistoryPageResponse(
        entries=entries,
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
    )


@router.get(
    "/history/tasks/{task_id}",
    response_model=HistoryPageResponse,
    responses={
        422: {"description": "Invalid pagination parameters"},
    },
    tags=["History"],
)
async def get_task_history(
    task_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    svc: HistoryService = Depends(get_history_service),
) -> HistoryPageResponse:
    task_id_clean = task_id.strip()
    if not task_id_clean:
        raise HTTPException(status_code=422, detail="task_id must not be empty")
    page = await svc.get_task_history(task_id_clean, limit=limit, offset=offset)
    return _to_response(page)


@router.get(
    "/history/plans/{plan_id}",
    response_model=HistoryPageResponse,
    responses={
        422: {"description": "Invalid pagination parameters"},
    },
    tags=["History"],
)
async def get_plan_history(
    plan_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    svc: HistoryService = Depends(get_history_service),
) -> HistoryPageResponse:
    plan_id_clean = plan_id.strip()
    if not plan_id_clean:
        raise HTTPException(status_code=422, detail="plan_id must not be empty")
    page = await svc.get_plan_history(plan_id_clean, limit=limit, offset=offset)
    return _to_response(page)


@router.get(
    "/history/executions/{execution_id}",
    response_model=HistoryPageResponse,
    responses={
        422: {"description": "Invalid pagination parameters"},
    },
    tags=["History"],
)
async def get_execution_history(
    execution_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    svc: HistoryService = Depends(get_history_service),
) -> HistoryPageResponse:
    execution_id_clean = execution_id.strip()
    if not execution_id_clean:
        raise HTTPException(status_code=422, detail="execution_id must not be empty")
    page = await svc.get_execution_history(execution_id_clean, limit=limit, offset=offset)
    return _to_response(page)


@router.get(
    "/history/correlations/{correlation_id}",
    response_model=HistoryPageResponse,
    responses={
        422: {"description": "Invalid pagination parameters"},
    },
    tags=["History"],
)
async def get_correlation_history(
    correlation_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    svc: HistoryService = Depends(get_history_service),
) -> HistoryPageResponse:
    correlation_id_clean = correlation_id.strip()
    if not correlation_id_clean:
        raise HTTPException(status_code=422, detail="correlation_id must not be empty")
    page = await svc.get_correlation_history(correlation_id_clean, limit=limit, offset=offset)
    return _to_response(page)


@router.get(
    "/history/between",
    response_model=HistoryPageResponse,
    responses={
        422: {"description": "Invalid parameters"},
    },
    tags=["History"],
)
async def get_history_between(
    start: datetime,
    end: datetime,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    event_type: str | None = Query(default=None),
    svc: HistoryService = Depends(get_history_service),
) -> HistoryPageResponse:
    if start.tzinfo is None:
        raise HTTPException(status_code=422, detail="start datetime must be timezone-aware (UTC)")
    if end.tzinfo is None:
        raise HTTPException(status_code=422, detail="end datetime must be timezone-aware (UTC)")
    if start > end:
        raise HTTPException(status_code=422, detail=f"start ({start}) must not be after end ({end})")

    parsed_event_type = None
    if event_type is not None:
        from app.events.schemas import EventType
        try:
            parsed_event_type = EventType(event_type.strip())
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid event_type: {event_type}")

    page = await svc.get_history_between(
        start, end, limit=limit, offset=offset, event_type=parsed_event_type
    )
    return _to_response(page)


@router.get(
    "/history/recent",
    response_model=HistoryPageResponse,
    responses={
        422: {"description": "Invalid pagination parameters"},
    },
    tags=["History"],
)
async def get_recent_history(
    limit: int = Query(default=50, ge=1, le=500),
    svc: HistoryService = Depends(get_history_service),
) -> HistoryPageResponse:
    page = await svc.get_recent_history(limit=limit)
    return _to_response(page)
