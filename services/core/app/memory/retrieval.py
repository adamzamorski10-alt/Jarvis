"""
Memory Retrieval Service — V1 Deterministic Read-Only Layer.

Provides bounded, deterministic memory search over the existing MemoryRepository.
Does NOT generate, modify, or execute memory content.
Does NOT perform semantic ranking in V1.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.identity.models import ActorType, RequestContext
from app.memory.models import (
    MemoryCategory,
    MemoryEntry,
    MemoryScope,
    ProvenanceSource,
    TrustLevel,
)
from app.memory.repository import MemoryRepository
from app.memory.service import MemoryCaller, MemoryForbidden, MemoryService


# ── Bounds ──────────────────────────────────────────────────────────────────────

_MAX_QUERY_LENGTH = 256
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_MAX_CATEGORIES = 10
_MAX_TRUST_LEVELS = 5
_MAX_SOURCES = 10


# ── LIKE escaping ──────────────────────────────────────────────────────────────


def _escape_like_metacharacters(value: str) -> str:
    """Escape SQL LIKE metacharacters so the query is treated as literal text."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_like_pattern(query: str) -> str:
    """Build a safe LIKE pattern from a user query."""
    escaped = _escape_like_metacharacters(query.strip())
    return f"%{escaped}%"


# ── Request / Response ─────────────────────────────────────────────────────────


class MemoryRetrievalRequest:
    """
    Typed retrieval request.

    All filters are optional. `request_context` is server-populated only.
    """

    __slots__ = (
        "query",
        "scope",
        "scope_id",
        "categories",
        "trust_levels",
        "sources",
        "include_deleted",
        "include_superseded",
        "created_after",
        "created_before",
        "limit",
        "request_context",
    )

    def __init__(
        self,
        *,
        query: str | None = None,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        categories: list[MemoryCategory] | None = None,
        trust_levels: list[TrustLevel] | None = None,
        sources: list[ProvenanceSource] | None = None,
        include_deleted: bool = False,
        include_superseded: bool = False,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = _DEFAULT_LIMIT,
        request_context: RequestContext | None = None,
    ) -> None:
        if query is not None and len(query) > _MAX_QUERY_LENGTH:
            raise ValueError(f"query exceeds max length {_MAX_QUERY_LENGTH}: {len(query)}")
        if scope_id is not None and len(scope_id) > 100:
            raise ValueError(f"scope_id exceeds max length 100: {len(scope_id)}")
        if categories is not None and len(categories) > _MAX_CATEGORIES:
            raise ValueError(f"categories exceeds max length {_MAX_CATEGORIES}: {len(categories)}")
        if trust_levels is not None and len(trust_levels) > _MAX_TRUST_LEVELS:
            raise ValueError(f"trust_levels exceeds max length {_MAX_TRUST_LEVELS}: {len(trust_levels)}")
        if sources is not None and len(sources) > _MAX_SOURCES:
            raise ValueError(f"sources exceeds max length {_MAX_SOURCES}: {len(sources)}")
        limit = max(1, min(limit, _MAX_LIMIT))
        if created_after is not None and created_after.tzinfo is None:
            created_after = created_after.replace(tzinfo=timezone.utc)
        if created_before is not None and created_before.tzinfo is None:
            created_before = created_before.replace(tzinfo=timezone.utc)

        self.query = query.strip() if query else None
        self.scope = scope
        self.scope_id = scope_id
        self.categories = categories
        self.trust_levels = trust_levels
        self.sources = sources
        self.include_deleted = include_deleted
        self.include_superseded = include_superseded
        self.created_after = created_after
        self.created_before = created_before
        self.limit = limit
        self.request_context = request_context


class MemoryRetrievalResponse:
    """Typed retrieval result."""

    __slots__ = ("success", "query", "memories", "total", "truncated")

    def __init__(
        self,
        *,
        success: bool,
        query: str | None,
        memories: list[MemoryEntry],
        total: int,
        truncated: bool,
    ) -> None:
        self.success = success
        self.query = query
        self.memories = memories
        self.total = total
        self.truncated = truncated


# ── Service ─────────────────────────────────────────────────────────────────────


class MemoryRetrievalService:
    """
    Deterministic, read-only memory retrieval service.

    Does NOT modify memory state.
    Does NOT emit retrieval events in V1.
    """

    def __init__(self, session: Any, memory_service: MemoryService | None = None) -> None:
        self._repo = MemoryRepository(session)
        self._memory_service = memory_service

    async def search(self, request: MemoryRetrievalRequest) -> MemoryRetrievalResponse:
        """
        Search memories using bounded SQL LIKE matching and structured filters.

        Returns deterministic results ordered by recency.
        """
        if request.request_context is None:
            raise MemoryForbidden("Authenticated request context is required for memory retrieval")
        if not request.request_context.is_authenticated and request.request_context.actor_type != ActorType.SYSTEM:
            raise MemoryForbidden("Authenticated request context is required for memory retrieval")

        if self._memory_service is not None:
            _ = MemoryService.caller_from_request_context(request.request_context)

        like_pattern = _build_like_pattern(request.query) if request.query else None

        memories, total = await self._repo.search(
            query=like_pattern,
            scope=request.scope,
            scope_id=request.scope_id,
            categories=request.categories,
            trust_levels=request.trust_levels,
            sources=request.sources,
            include_deleted=request.include_deleted,
            include_superseded=request.include_superseded,
            created_after=request.created_after,
            created_before=request.created_before,
            limit=request.limit,
            offset=0,
        )

        return MemoryRetrievalResponse(
            success=True,
            query=request.query,
            memories=memories,
            total=total,
            truncated=total > request.limit,
        )


# Reuse MemoryForbidden from service
from app.memory.service import MemoryForbidden  # noqa: E402


__all__ = [
    "MemoryRetrievalRequest",
    "MemoryRetrievalResponse",
    "MemoryRetrievalService",
    "_build_like_pattern",
    "_escape_like_metacharacters",
    "_MAX_QUERY_LENGTH",
    "_DEFAULT_LIMIT",
    "_MAX_LIMIT",
]
