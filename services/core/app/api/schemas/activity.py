"""Activity event schemas."""
from datetime import datetime
from pydantic import BaseModel


class ActivityEventResponse(BaseModel):
    id: int
    timestamp: datetime
    level: str
    category: str
    message: str
    details: dict | None = None

    model_config = {"from_attributes": True}


class ActivityEventCreate(BaseModel):
    level: str = "INFO"
    category: str = "system"
    message: str
    details: dict | None = None


class PaginatedActivityResponse(BaseModel):
    events: list[ActivityEventResponse]
    total: int
    limit: int
    offset: int
