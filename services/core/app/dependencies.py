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


async def get_permission_engine(
    registry: ToolRegistry = Depends(get_tool_registry),
    grant_repo: GrantRepository = Depends(get_grant_repository),
) -> AsyncGenerator[PermissionEngine, None]:
    from app.tools.permission_engine import PermissionEngine
    yield PermissionEngine(registry=registry, grant_repo=grant_repo)
