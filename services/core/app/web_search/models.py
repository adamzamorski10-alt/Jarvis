"""Contracts for the Web Search capability."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=600)
    count: int = Field(default=10, ge=1, le=20)
    country: str = Field(default="PL", min_length=2, max_length=2)
    search_lang: str = Field(default="pl", min_length=2, max_length=10)
    freshness: str | None = Field(default=None, max_length=32)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("country")
    @classmethod
    def validate_country(cls, value: str) -> str:
        return value.strip().upper()


class WebSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=4096)
    snippet: str = Field(default="", max_length=4096)
    source: str | None = Field(default=None, max_length=256)
    rank: int = Field(ge=1, le=20)


class WebSearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    provider: str
    results: list[WebSearchResult] = Field(max_length=20)
    more_results_available: bool = False


__all__ = ["WebSearchRequest", "WebSearchResult", "WebSearchResponse"]
