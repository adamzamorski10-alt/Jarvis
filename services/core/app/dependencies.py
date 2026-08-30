"""
FastAPI Dependency Injection
Provides reusable dependencies for routes.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, AsyncGenerator
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services.settings_service import SettingsService
from app.services.activity_service import ActivityService
from app.ai.services.provider_manager import ProviderManager
from app.ai.services.conversation_service import ConversationService
from app.ai.services.ai_service import AIService

if TYPE_CHECKING:
    from app.repositories.grant_repository import GrantRepository
    from app.repositories.audit_log_repository import AuditLogRepository
    from app.tools.audit_service import AuditLogService
    from app.tools.executor import ToolExecutor
    from app.tools.implementation_registry import ToolImplementationRegistry
    from app.tools.registry import ToolRegistry
    from app.tools.permission_engine import PermissionEngine


async def get_settings_service(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[SettingsService, None]:
    yield SettingsService(session)


async def get_activity_service(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[ActivityService, None]:
    yield ActivityService(session)


async def get_provider_manager(
    settings_svc: SettingsService = Depends(get_settings_service),
) -> AsyncGenerator[ProviderManager, None]:
    yield ProviderManager(settings_svc)


async def get_conversation_service(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[ConversationService, None]:
    yield ConversationService(session)


async def get_ai_service(
    providers: ProviderManager = Depends(get_provider_manager),
    conversations: ConversationService = Depends(get_conversation_service),
    activity: ActivityService = Depends(get_activity_service),
) -> AsyncGenerator[AIService, None]:
    yield AIService(providers, conversations, activity)


def get_tool_registry():
    from app.tools.registry import tool_registry
    return tool_registry


async def get_grant_repository(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[GrantRepository, None]:
    from app.repositories.grant_repository import GrantRepository
    yield GrantRepository(session)


async def get_audit_log_repository(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[AuditLogRepository, None]:
    from app.repositories.audit_log_repository import AuditLogRepository
    yield AuditLogRepository(session)


async def get_audit_log_service(
    repo: AuditLogRepository = Depends(get_audit_log_repository),
) -> AsyncGenerator[AuditLogService, None]:
    from app.tools.audit_service import AuditLogService
    yield AuditLogService(repo)


async def get_permission_engine(
    registry: ToolRegistry = Depends(get_tool_registry),
    grant_repo: GrantRepository = Depends(get_grant_repository),
    audit_service: AuditLogService = Depends(get_audit_log_service),
) -> AsyncGenerator[PermissionEngine, None]:
    from app.tools.permission_engine import PermissionEngine
    yield PermissionEngine(
        registry=registry,
        grant_repo=grant_repo,
        audit_service=audit_service,
    )


def get_tool_executor(
    registry: ToolRegistry = Depends(get_tool_registry),
) -> ToolExecutor:
    from app.tools.executor import ToolExecutor
    from app.tools.implementation_registry import tool_implementation_registry
    return ToolExecutor(registry=registry, impl_registry=tool_implementation_registry)
