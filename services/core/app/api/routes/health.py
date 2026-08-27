"""Health check route."""
from datetime import datetime, timezone
from fastapi import APIRouter

from app.api.schemas.health import HealthResponse
from app.core.config import get_settings

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Returns the health status of the Core service.
    Always responds quickly — does not check database connectivity here.
    Use /system/status for full component status.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        timestamp=datetime.now(timezone.utc),
    )
