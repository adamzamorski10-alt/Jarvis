"""System status route."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.api.schemas.system import SystemStatusResponse, ComponentStatus
from app.db.database import get_db
from app.services.settings_service import SettingsService
from app.ai.services.provider_manager import ProviderManager
from app.ai.services.conversation_service import ConversationService
from app.ai.services.ai_service import AIService
from app.services.activity_service import ActivityService

router = APIRouter()


@router.get("/system/status", response_model=SystemStatusResponse)
async def system_status(session: AsyncSession = Depends(get_db)) -> SystemStatusResponse:
    """
    Returns the status of all core system components.
    Performs a real database connectivity check.
    """
    # Check database connectivity with a real query
    db_status: ComponentStatus
    try:
        await session.execute(text("SELECT 1"))
        db_status = ComponentStatus(status="connected")
    except Exception as exc:
        db_status = ComponentStatus(status="error", message=str(exc))

    try:
        settings = SettingsService(session)
        ai = AIService(ProviderManager(settings), ConversationService(session), ActivityService(session))
        ai_status = await ai.get_status()
        ai_component = ComponentStatus(
            status="ready" if ai_status["ai_ready"] else ai_status["provider_status"],
            message=f"model={ai_status['model_status']}",
        )
    except Exception as exc:
        ai_component = ComponentStatus(status="error", message=str(exc))

    return SystemStatusResponse(
        core=ComponentStatus(status="running"),
        database=db_status,
        ai=ai_component,
    )
