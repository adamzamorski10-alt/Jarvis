"""Settings routes."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.schemas.settings import SettingsResponse, UpdateSettingsRequest
from app.dependencies import get_settings_service, get_activity_service
from app.identity.auth import require_authenticated
from app.services.settings_service import SettingsService
from app.services.activity_service import ActivityService

router = APIRouter()


@router.get("/settings", response_model=SettingsResponse)
async def get_settings_endpoint(
    request: Request,
    settings_svc: SettingsService = Depends(get_settings_service),
) -> SettingsResponse:
    _ = require_authenticated(request)
    """Return all application settings."""
    all_settings = await settings_svc.get_all()
    return SettingsResponse(settings=all_settings)


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    request: Request,
    req: UpdateSettingsRequest,
    settings_svc: SettingsService = Depends(get_settings_service),
    activity_svc: ActivityService = Depends(get_activity_service),
) -> SettingsResponse:
    _ = require_authenticated(request)
    """Update one or more application settings."""
    try:
        updated = await settings_svc.update(req.settings)

        # Log the change as an activity event
        changed_keys = list(req.settings.keys())
        await activity_svc.create_event(
            level="INFO",
            category="settings",
            message=f"Settings updated: {', '.join(changed_keys)}",
            details={"changed_keys": changed_keys, "new_values": req.settings},
        )

        return SettingsResponse(settings=updated)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
