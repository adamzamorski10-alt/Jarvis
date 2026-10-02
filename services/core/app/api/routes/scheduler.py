"""
Scheduler REST Control Plane — Thin route layer over SchedulerService.

All business logic lives in SchedulerService. Routes only handle:
- HTTP concerns (status codes, request parsing, response serialization)
- Error mapping (ValueError → HTTPException)
- Input sanitization (strip whitespace from path params)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.history import HistoryPageResponse
from app.api.schemas.scheduler import (
    CreateScheduledJobRequest,
    ScheduledJobListResponse,
    ScheduledJobResponse,
    UpdateScheduledJobRequest,
)
from app.dependencies import get_history_service, get_scheduler_service
from app.identity.auth import require_authenticated
from app.events.history import HistoryService
from app.scheduler.service import JobIdempotentReturn, SchedulerService

logger = logging.getLogger(__name__)

router = APIRouter()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _map_service_error(exc: ValueError) -> HTTPException:
    """Map SchedulerService ValueError to HTTPException."""
    message = str(exc)
    lower = message.lower()
    if "not found" in lower:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "different definition" in lower:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    if "active claim" in lower or "execution in progress" in lower:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    if "cannot modify" in lower and "while occurrence is" in lower:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    if "cannot delete" in lower:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    if "in the past" in lower:
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)
    if "fields cannot be modified" in lower:
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


@router.post(
    "/scheduler/jobs",
    response_model=ScheduledJobResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": ScheduledJobResponse, "description": "Idempotent retry — existing job returned"},
        409: {"model": dict, "description": "Job exists with different definition"},
        404: {"model": dict, "description": "Target task not found"},
        422: {"model": dict, "description": "Invalid request body or schedule"},
    },
    tags=["Scheduler"],
)
async def create_job(
    request: Request,
    req: CreateScheduledJobRequest,
    svc: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledJobResponse:
    _ = require_authenticated(request)
    try:
        job = await svc.create_job(
            job_id=req.job_id,
            name=req.name,
            schedule_type=req.schedule_type,
            schedule_expr=req.schedule_expr,
            timezone=req.timezone,
            target_type=req.target_type,
            target_id=req.target_id,
            enabled=req.enabled,
            now=_now_utc(),
        )
    except JobIdempotentReturn as exc:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=ScheduledJobResponse.model_validate(exc.job).model_dump(mode="json"),
        )
    except ValueError as exc:
        raise _map_service_error(exc)
    return ScheduledJobResponse.model_validate(job)


@router.get(
    "/scheduler/jobs",
    response_model=ScheduledJobListResponse,
    responses={
        422: {"model": dict, "description": "Invalid pagination parameters"},
    },
    tags=["Scheduler"],
)
async def list_jobs(
    request: Request,
    status: str | None = Query(default=None, description="Filter by job status"),
    enabled: bool | None = Query(default=None, description="Filter by enabled flag"),
    target_type: str | None = Query(default=None, description="Filter by target type"),
    target_id: str | None = Query(default=None, description="Filter by target id"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    svc: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledJobListResponse:
    _ = require_authenticated(request)
    jobs, total = await svc.list_jobs(
        status=status,
        enabled=enabled,
        target_type=target_type,
        target_id=target_id,
        limit=limit,
        offset=offset,
    )
    return ScheduledJobListResponse(
        jobs=[ScheduledJobResponse.model_validate(j) for j in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/scheduler/jobs/{job_id}",
    response_model=ScheduledJobResponse,
    responses={
        404: {"model": dict, "description": "Job not found"},
    },
    tags=["Scheduler"],
)
async def get_job(
    request: Request,
    job_id: str,
    svc: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledJobResponse:
    _ = require_authenticated(request)
    job = await svc.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found")
    return ScheduledJobResponse.model_validate(job)


@router.patch(
    "/scheduler/jobs/{job_id}",
    response_model=ScheduledJobResponse,
    responses={
        200: {"model": ScheduledJobResponse, "description": "Job updated"},
        404: {"model": dict, "description": "Job not found"},
        409: {"model": dict, "description": "State-dependent update conflict"},
        422: {"model": dict, "description": "Invalid field/value or request body"},
    },
    tags=["Scheduler"],
)
async def update_job(
    request: Request,
    job_id: str,
    req: UpdateScheduledJobRequest,
    svc: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledJobResponse:
    _ = require_authenticated(request)
    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No updatable fields provided",
        )
    try:
        job = await svc.update_job(job_id, updates, now=_now_utc())
    except ValueError as exc:
        raise _map_service_error(exc)
    return ScheduledJobResponse.model_validate(job)


@router.delete(
    "/scheduler/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": dict, "description": "Job not found"},
        409: {"model": dict, "description": "Cannot delete job with active claim or execution"},
    },
    tags=["Scheduler"],
)
async def delete_job(
    request: Request,
    job_id: str,
    svc: SchedulerService = Depends(get_scheduler_service),
) -> None:
    _ = require_authenticated(request)
    try:
        await svc.delete_job(job_id)
    except ValueError as exc:
        raise _map_service_error(exc)


@router.post(
    "/scheduler/jobs/{job_id}/enable",
    response_model=ScheduledJobResponse,
    responses={
        404: {"model": dict, "description": "Job not found"},
    },
    tags=["Scheduler"],
)
async def enable_job(
    request: Request,
    job_id: str,
    svc: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledJobResponse:
    _ = require_authenticated(request)
    try:
        job = await svc.enable_job(job_id)
    except ValueError as exc:
        raise _map_service_error(exc)
    return ScheduledJobResponse.model_validate(job)


@router.post(
    "/scheduler/jobs/{job_id}/disable",
    response_model=ScheduledJobResponse,
    responses={
        404: {"model": dict, "description": "Job not found"},
    },
    tags=["Scheduler"],
)
async def disable_job(
    request: Request,
    job_id: str,
    svc: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledJobResponse:
    _ = require_authenticated(request)
    try:
        job = await svc.disable_job(job_id)
    except ValueError as exc:
        raise _map_service_error(exc)
    return ScheduledJobResponse.model_validate(job)


@router.get(
    "/scheduler/jobs/{job_id}/history",
    response_model=HistoryPageResponse,
    responses={
        422: {"model": dict, "description": "Invalid pagination parameters"},
    },
    tags=["Scheduler"],
)
async def get_job_history(
    request: Request,
    job_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    history_svc: HistoryService = Depends(get_history_service),
) -> HistoryPageResponse:
    _ = require_authenticated(request)
    from app.api.routes.history import _to_response
    job_id_clean = job_id.strip()
    if not job_id_clean:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="job_id must not be empty",
        )
    page = await history_svc.get_correlation_history(job_id_clean, limit=limit, offset=offset)
    return _to_response(page)
