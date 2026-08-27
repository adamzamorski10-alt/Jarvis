"""
Repository — App Settings
Data access layer for application settings.
"""
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.settings import AppSetting


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self) -> list[AppSetting]:
        result = await self._session.execute(select(AppSetting))
        return list(result.scalars().all())

    async def get_by_key(self, key: str) -> AppSetting | None:
        result = await self._session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        return result.scalar_one_or_none()

    async def upsert(self, key: str, value: str | None) -> AppSetting:
        existing = await self.get_by_key(key)
        if existing:
            existing.value = value
            from datetime import datetime, timezone
            existing.updated_at = datetime.now(timezone.utc)
            return existing
        setting = AppSetting(key=key, value=value)
        self._session.add(setting)
        await self._session.flush()
        return setting

    async def upsert_many(self, settings: dict[str, str | None]) -> list[AppSetting]:
        results = []
        for key, value in settings.items():
            results.append(await self.upsert(key, value))
        return results

    async def delete(self, key: str) -> bool:
        result = await self._session.execute(
            delete(AppSetting).where(AppSetting.key == key)
        )
        return result.rowcount > 0
