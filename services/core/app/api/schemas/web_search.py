"""Web Search API schemas."""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebSearchConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(default="duckduckgo", min_length=1, max_length=32)
    api_key: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("provider must not be blank")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("api_key must not be blank")
        return value.strip() if value is not None else None


class WebSearchConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str
    api_key_configured: bool
    api_key_hint: str | None = None
