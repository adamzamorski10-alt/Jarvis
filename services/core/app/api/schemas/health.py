"""Health endpoint schemas."""
from datetime import datetime
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    timestamp: datetime
