"""System status endpoint schemas."""
from pydantic import BaseModel


class ComponentStatus(BaseModel):
    status: str  # "running" | "connected" | "error" | "not_configured"
    message: str | None = None


class SystemStatusResponse(BaseModel):
    core: ComponentStatus
    database: ComponentStatus
    ai: ComponentStatus
