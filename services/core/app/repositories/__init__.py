# app/repositories/__init__.py
from app.repositories.settings_repository import SettingsRepository
from app.repositories.activity_repository import ActivityRepository

__all__ = ["SettingsRepository", "ActivityRepository"]
