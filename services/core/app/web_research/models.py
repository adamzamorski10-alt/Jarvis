"""Strict, provider-neutral contracts for bounded web research.

These contracts do not perform network access. URL safety and connection pinning
must be enforced by the fetcher/transport before this module can be considered
an SSRF security boundary.
"""
from __future__ import annotations

from enum import Enum
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ResearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=600)
    max_sources: int = Field(default=3, ge=1, le=5)
    locale: str | None = Field(default=None, min_length=2, max_length=16)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(ch) < 0x20 for ch in value):
            raise ValueError("query must be non-blank and contain no control characters")
        return value

    @field_validator("locale")
    @classmethod
    def normalize_locale(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ResearchSourceStatus(str, Enum):
    RETRIEVED = "retrieved"
    SEARCH_ONLY = "search_only"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNSUPPORTED_CONTENT = "unsupported_content"


class ResearchSource(BaseModel):
    """Safe metadata returned to callers; page text is intentionally excluded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=r"^src_[a-f0-9]{12}$")
    title: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=2048)
    hostname: str = Field(min_length=1, max_length=253)
    status: ResearchSourceStatus
    extracted_text_length: int = Field(default=0, ge=0, le=100_000)
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @field_validator("title", "hostname")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("url")
    @classmethod
    def validate_public_url_shape(cls, value: str) -> str:
        # Shape validation only. Public-IP checks and pinning the actual connection
        # are the fetcher's responsibility; a preflight DNS lookup is insufficient.
        if len(value) > 2048 or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError("URL contains whitespace/control characters or is too long")
        try:
            parts = urlsplit(value)
            hostname = parts.hostname
            _ = parts.port
        except ValueError as exc:
            raise ValueError("invalid URL") from exc
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or not hostname:
            raise ValueError("URL must be absolute HTTP(S)")
        if parts.username is not None or parts.password is not None:
            raise ValueError("URL userinfo is forbidden")
        if parts.fragment:
            # Fragments are not sent to the server; remove them to keep canonical
            # source identity deterministic.
            value = value.split("#", 1)[0]
        return value

    @model_validator(mode="after")
    def hostname_matches_url(self) -> Self:
        parsed_hostname = urlsplit(self.url).hostname
        if parsed_hostname is None or parsed_hostname.rstrip(".").lower() != self.hostname.rstrip(".").lower():
            raise ValueError("hostname must match the source URL")
        return self


class ResearchCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=r"^src_[a-f0-9]{12}$")
    excerpt: str | None = Field(default=None, max_length=500)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("citation offsets must be provided together")
        if self.start_offset is not None and self.end_offset is not None:
            if self.end_offset <= self.start_offset:
                raise ValueError("citation end_offset must be greater than start_offset")
        return self


class ResearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str = Field(default="", max_length=12_000)
    sources: list[ResearchSource] = Field(default_factory=list, max_length=5)
    citations: list[ResearchCitation] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    synthesized: bool = False

    @model_validator(mode="after")
    def validate_evidence_links(self) -> Self:
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        known = set(source_ids)
        if any(citation.source_id not in known for citation in self.citations):
            raise ValueError("citation references an unknown source ID")
        if self.synthesized and not self.answer.strip():
            raise ValueError("synthesized response must contain an answer")
        if self.synthesized and not self.citations:
            raise ValueError("synthesized response must contain at least one citation")
        return self


__all__ = [
    "ResearchCitation",
    "ResearchRequest",
    "ResearchResponse",
    "ResearchSource",
    "ResearchSourceStatus",
]
