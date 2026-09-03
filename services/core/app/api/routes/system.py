"""System status route."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.api.schemas.system import (
    SystemStatusResponse,
    CoreComponentStatus,
    DatabaseComponentStatus,
    AIComponentStatus,
)
from app.db.database import get_db
from app.services.settings_service import SettingsService
from app.ai.services.provider_manager import ProviderManager
from app.ai.services.conversation_service import ConversationService
from app.ai.services.ai_service import AIService
from app.services.activity_service import ActivityService
from app.core.config import get_settings
from app.core.lifecycle import get_uptime_seconds, get_start_time_iso

router = APIRouter()


@router.get("/system/status", response_model=SystemStatusResponse)
async def system_status(session: AsyncSession = Depends(get_db)) -> SystemStatusResponse:
    """
    Returns the status of all core system components.
    Performs a real database connectivity check and AI provider inspection.
    """
    app_settings = get_settings()

    # Core component
    core_status = CoreComponentStatus(
        status="running",
        version=app_settings.app_version,
        uptime_seconds=round(get_uptime_seconds(), 2),
        start_time=get_start_time_iso(),
        message="Core engine is running normally",
    )

    # Database component check
    db_status: DatabaseComponentStatus
    try:
        await session.execute(text("SELECT 1"))
        db_status = DatabaseComponentStatus(
            status="connected",
            engine="SQLite (aiosqlite)",
            migration_status="up_to_date",
            schema_version="1.0",
            last_error=None,
            message="Database connection active",
        )
    except Exception as exc:
        db_status = DatabaseComponentStatus(
            status="error",
            engine="SQLite (aiosqlite)",
            migration_status="unknown",
            schema_version="1.0",
            last_error=str(exc),
            message=f"Database error: {str(exc)}",
        )

    # AI component check
    try:
        settings = SettingsService(session)
        provider_manager = ProviderManager(settings)
        conversation_service = ConversationService(session)
        activity_service = ActivityService(session)
        from app.tools.registry import tool_registry
        from app.tools.executor import ToolExecutor
        from app.tools.implementation_registry import tool_implementation_registry
        from app.tools.tool_execution_service import ToolExecutionService
        from app.repositories.grant_repository import GrantRepository
        from app.repositories.audit_log_repository import AuditLogRepository
        from app.tools.audit_service import AuditLogService
        from app.tools.permission_engine import PermissionEngine
        from app.repositories.approval_repository import ApprovalRepository
        from app.tools.approval_service import ApprovalService

        grant_repo = GrantRepository(session)
        audit_repo = AuditLogRepository(session)
        audit_service = AuditLogService(audit_repo)
        approval_repo = ApprovalRepository(session)
        permission_engine = PermissionEngine(
            registry=tool_registry,
            grant_repo=grant_repo,
            audit_service=audit_service,
            approval_repo=approval_repo,
        )
        executor = ToolExecutor(registry=tool_registry, impl_registry=tool_implementation_registry)
        tool_execution_service = ToolExecutionService(
            registry=tool_registry,
            impl_registry=tool_implementation_registry,
            permission_engine=permission_engine,
            audit_service=audit_service,
            executor=executor,
        )
        approval_repo = ApprovalRepository(session)
        approval_service = ApprovalService(
            repo=approval_repo,
            tool_execution_service=tool_execution_service,
            settings=settings,
            audit_service=audit_service,
        )
        ai = AIService(provider_manager, conversation_service, activity_service, settings, tool_execution_service, approval_service)
        ai_status = await ai.get_status()
        ai_status_str = "ready" if ai_status["ai_ready"] else ai_status["provider_status"]
        ai_component = AIComponentStatus(
            status=ai_status_str,
            provider=ai_status.get("provider", "unknown"),
            configured=bool(ai_status.get("configured", False)),
            selected_model=ai_status.get("model"),
            message=ai_status.get("message") or f"model={ai_status['model_status']}",
            installed=ai_status.get("installed"),
            service_running=ai_status.get("service_running"),
            api_responding=ai_status.get("api_responding"),
            models_available=ai_status.get("models_available"),
            executable_path=ai_status.get("executable_path"),
        )
    except Exception as exc:
        ai_component = AIComponentStatus(
            status="error",
            provider="unknown",
            configured=False,
            selected_model=None,
            message=str(exc),
        )

    return SystemStatusResponse(
        core=core_status,
        database=db_status,
        ai=ai_component,
    )
