"""
Service — Settings
Business logic for application settings management.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.settings_repository import SettingsRepository

# Default settings seeded on first run
DEFAULT_SETTINGS: dict[str, str] = {
    "theme": "dark",
    "language": "en",
    "autonomy_mode": "manual",
    "ai_provider": "ollama",
    "ai_ollama_url": "http://127.0.0.1:11434",
    "ai_ollama_autostart": "false",
    "ai_kimi_base_url": "https://api.moonshot.ai/v1",
    "ai_kimi_api_key_encrypted": "",
    "ai_selected_model": "",
}


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = SettingsRepository(session)

    async def ensure_defaults(self) -> None:
        """Seed default settings if they don't already exist."""
        for key, value in DEFAULT_SETTINGS.items():
            existing = await self._repo.get_by_key(key)
            if existing is None or existing.value is None:
                await self._repo.upsert(key, value)

    async def get_all(self) -> dict[str, str | None]:
        """Return all settings as a plain dict."""
        settings = await self._repo.get_all()
        return {s.key: s.value for s in settings}

    async def get_one(self, key: str) -> str | None:
        """Return a single setting value by key."""
        setting = await self._repo.get_by_key(key)
        return setting.value if setting else None

    async def update(self, updates: dict[str, str | None]) -> dict[str, str | None]:
        """Update one or more settings. Returns the updated set."""
        await self._repo.upsert_many(updates)
        return await self.get_all()
