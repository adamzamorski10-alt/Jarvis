"""
AI Operator Core — Application Configuration
"""
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_OPERATOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "ai-operator-core"
    app_version: str = "0.1.0"
    debug: bool = False

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # Database
    db_path: Path = Path("data/ai_operator.db")

    # CORS — only localhost origins for security
    cors_origins: list[str] = [
        "http://localhost:1420",   # Tauri dev default
        "http://localhost:3000",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ]

    # Logging
    log_level: str = "INFO"
    log_format: str = "pretty"  # "pretty" | "json"

    # Identity & Session
    session_absolute_timeout_seconds: int = 86400  # 24 hours
    session_idle_timeout_seconds: int = 86400  # 24 hours

    @property
    def database_url(self) -> str:
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
