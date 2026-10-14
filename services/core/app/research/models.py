"""Bounded contracts for source-grounded research responses."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SOURCE_ID_PATTERN = r"^src_[1-5]$"


class ResearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=600)
    max_sources: int = Field(default=5, ge=1, le=5)
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
        value = value.strip().upper()
        if len(value) != 2 or not value.isascii() or not value.isalpha():
            raise ValueError("country must contain exactly two ASCII letters")
        return value

    @field_validator("search_lang")
    @classmethod
    def validate_search_lang(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or not value.isascii() or not all(ch.isalpha() or ch == "-" for ch in value):
            raise ValueError("search_lang must contain only ASCII letters and hyphens")
        return value

    @field_validator("freshness")
    @classmethod
    def validate_freshness(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not value.isascii() or any(ord(ch) < 0x20 for ch in value):
            raise ValueError("freshness contains invalid characters")
        return value


class ResearchSourceStatus(str, Enum):
    FETCHED = "fetched"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNSUPPORTED_CONTENT = "unsupported_content"
    EMPTY_EXTRACTION = "empty_extraction"


class ResearchSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_SOURCE_ID_PATTERN)
    title: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=4096)
    host: str = Field(min_length=1, max_length=253)
    retrieved_at: datetime | None = None
    status: ResearchSourceStatus
    excerpt: str = Field(default="", max_length=12_000)
    error_code: str | None = Field(default=None, min_length=1, max_length=64)


class ResearchClaimStatus(str, Enum):
    SUPPORTED = "supported"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"


class ResearchClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=2_000)
    source_ids: list[str] = Field(default_factory=list, max_length=5)
    evidence_status: ResearchClaimStatus


class ResearchSynthesisStatus(str, Enum):
    COMPLETE = "complete"
    EVIDENCE_ONLY = "evidence_only"


class ResearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=600)
    answer: str = Field(min_length=1, max_length=12_000)
    provider: str = Field(min_length=1, max_length=32)
    model: str | None = Field(default=None, max_length=128)
    sources: list[ResearchSource] = Field(default_factory=list, max_length=5)
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    synthesis_status: ResearchSynthesisStatus = ResearchSynthesisStatus.COMPLETE


__all__ = [
    "ResearchClaim",
    "ResearchClaimStatus",
    "ResearchRequest",
    "ResearchResponse",
    "ResearchSource",
    "ResearchSourceStatus",
    "ResearchSynthesisStatus",
]
