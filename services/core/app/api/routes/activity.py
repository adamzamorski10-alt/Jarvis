"""Activity routes."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.schemas.activity import (
    ActivityEventCreate,
    ActivityEventResponse,
    PaginatedActivityResponse,
)
from app.dependencies import get_activity_service
from app.identity.auth import require_authenticated
from app.services.activity_service import ActivityService

router = APIRouter()


@router.get("/activity", response_model=PaginatedActivityResponse)
async def get_activity(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    level: str | None = Query(default=None),
    activity_svc: ActivityService = Depends(get_activity_service),
) -> PaginatedActivityResponse:
    _ = require_authenticated(request)
    """Return paginated activity events, newest first."""
    events, total = await activity_svc.get_events(
        limit=limit,
        offset=offset,
        category=category,
        level=level,
    )
    return PaginatedActivityResponse(
        events=[ActivityEventResponse.model_validate(e) for e in events],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/activity", response_model=ActivityEventResponse, status_code=201)
async def create_activity_event(
    request: Request,
    body: ActivityEventCreate,
    activity_svc: ActivityService = Depends(get_activity_service),
) -> ActivityEventResponse:
    _ = require_authenticated(request)
    """Create an activity event (for testing and internal use)."""
    event = await activity_svc.create_event(
        message=body.message,
        level=body.level,
        category=body.category,
        details=body.details,
    )
    return ActivityEventResponse.model_validate(event)
