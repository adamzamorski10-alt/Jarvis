"""System status endpoint schemas."""
from pydantic import BaseModel
from typing import Optional


class CoreComponentStatus(BaseModel):
    status: str  # "running" | "starting" | "error"
    version: str = "0.1.0"
    uptime_seconds: float = 0.0
    start_time: str = ""
    message: Optional[str] = None


class DatabaseComponentStatus(BaseModel):
    status: str  # "connected" | "error"
    engine: str = "SQLite (aiosqlite)"
    migration_status: str = "up_to_date"
    schema_version: str = "1.0"
    last_error: Optional[str] = None
    message: Optional[str] = None


class AIComponentStatus(BaseModel):
    status: str  # "ready" | "online" | "no_models" | "stopped" | "not_installed" | "unreachable" | "not_configured" | "error"
    provider: str = "ollama"
    configured: bool = False
    selected_model: Optional[str] = None
    message: Optional[str] = None
    installed: Optional[bool] = None
    service_running: Optional[bool] = None
    api_responding: Optional[bool] = None
    models_available: Optional[bool] = None
    executable_path: Optional[str] = None


class SystemStatusResponse(BaseModel):
    core: CoreComponentStatus
    database: DatabaseComponentStatus
    ai: AIComponentStatus
