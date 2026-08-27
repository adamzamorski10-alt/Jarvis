"""
FastAPI Dependency Injection
Provides reusable dependencies for routes.
"""
from typing import AsyncGenerator
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services.settings_service import SettingsService
from app.services.activity_service import ActivityService
from app.ai.services.provider_manager import ProviderManager
from app.ai.services.conversation_service import ConversationService
from app.ai.services.ai_service import AIService


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
