"""
Pydantic schemas for Files API.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FileEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str
    size: int | None = None
    path: str


class DirectoryListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    entries: list[FileEntry]
    truncated: bool


class FileReadResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    size: int
    encoding: str = "utf-8"
    truncated: bool


class FileSearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    query: str
    path: str
    matches: list[FileEntry]
    truncated: bool
