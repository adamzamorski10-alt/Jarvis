"""Web Search API schemas."""
from pydantic import BaseModel, ConfigDict, Field


class WebSearchConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(default="duckduckgo", min_length=1, max_length=32)
    api_key: str | None = Field(default=None, min_length=1, max_length=512)


class WebSearchConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str
    api_key_configured: bool
    api_key_hint: str | None = None
