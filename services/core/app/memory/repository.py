"""
Repository — Memories

Data access layer for memory entries in SQLite.

Soft delete is represented by deleted_at timestamp.
Supersession is represented by superseded_by memory_id.

The repository does NOT authorize, rank, or assemble memories.
It persists and retrieves.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.memory import MemoryModel
from app.memory.models import MemoryEntry, MemoryCategory, MemoryScope, TrustLevel, ProvenanceSource


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500
_MAX_OFFSET = 100_000


def _serialize_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    return json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _deserialize_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class MemoryRepository:
    """
    Data access layer for memory entries.

    All queries exclude soft-deleted records (deleted_at IS NOT NULL)
    unless include_deleted is explicitly set.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def create(self, memory: MemoryEntry) -> MemoryEntry:
        """
        Persist a new memory entry.

        Returns the persisted MemoryEntry.
        """
        if not isinstance(memory, MemoryEntry):
            raise TypeError(f"Expected MemoryEntry, got {type(memory).__name__}")

        model = MemoryModel(
            memory_id=memory.memory_id,
            category=memory.category.value,
            scope=memory.scope.value,
            scope_id=memory.scope_id,
            content=json.dumps(memory.content, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
            trust_level=memory.trust_level.value,
            confidence=memory.confidence,
            source=memory.source.value,
            source_id=memory.source_id,
            created_at=_to_naive_utc(memory.created_at) or datetime.now(timezone.utc),
            updated_at=_to_naive_utc(memory.updated_at) or datetime.now(timezone.utc),
            archived_at=_to_naive_utc(memory.archived_at),
            expires_at=_to_naive_utc(memory.expires_at),
            superseded_by=memory.superseded_by,
            deleted_at=_to_naive_utc(memory.deleted_at),
            memory_metadata=_serialize_metadata(memory.metadata),
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_domain(model)

    async def get(self, memory_id: str) -> MemoryEntry | None:
        """Return MemoryEntry by memory_id, or None if not found or soft-deleted."""
        result = await self._session.execute(
            select(MemoryModel).where(
                MemoryModel.memory_id == memory_id,
                MemoryModel.deleted_at.is_(None),
            )
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_including_deleted(self, memory_id: str) -> MemoryEntry | None:
        """Return MemoryEntry by memory_id, including soft-deleted records."""
        result = await self._session.execute(
            select(MemoryModel).where(MemoryModel.memory_id == memory_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def list(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        category: MemoryCategory | None = None,
        trust_level: TrustLevel | None = None,
        source: ProvenanceSource | None = None,
        include_deleted: bool = False,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[list[MemoryEntry], int]:
        """
        List memory entries with optional filters.

        Returns (entries, total_count).
        Default ordering: created_at DESC, memory_id DESC (newest first).
        """
        limit = max(1, min(limit, _MAX_LIMIT))
        offset = max(0, min(offset, _MAX_OFFSET))

        query = select(MemoryModel)
        count_query = select(func.count()).select_from(MemoryModel)

        if scope is not None:
            scope_val = scope.value if isinstance(scope, MemoryScope) else str(scope)
            query = query.where(MemoryModel.scope == scope_val)
            count_query = count_query.where(MemoryModel.scope == scope_val)
        if scope_id is not None:
            query = query.where(MemoryModel.scope_id == scope_id)
            count_query = count_query.where(MemoryModel.scope_id == scope_id)
        if category is not None:
            cat_val = category.value if isinstance(category, MemoryCategory) else str(category)
            query = query.where(MemoryModel.category == cat_val)
            count_query = count_query.where(MemoryModel.category == cat_val)
        if trust_level is not None:
            trust_val = trust_level.value if isinstance(trust_level, TrustLevel) else str(trust_level)
            query = query.where(MemoryModel.trust_level == trust_val)
            count_query = count_query.where(MemoryModel.trust_level == trust_val)
        if source is not None:
            src_val = source.value if isinstance(source, ProvenanceSource) else str(source)
            query = query.where(MemoryModel.source == src_val)
            count_query = count_query.where(MemoryModel.source == src_val)
        if not include_deleted:
            query = query.where(MemoryModel.deleted_at.is_(None))
            count_query = count_query.where(MemoryModel.deleted_at.is_(None))

        count_result = await self._session.execute(count_query)
        total = count_result.scalar_one() or 0

        query = query.order_by(MemoryModel.created_at.desc(), MemoryModel.memory_id.desc())
        query = query.limit(limit).offset(offset)

        result = await self._session.execute(query)
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models], total

    async def update(self, memory_id: str, changes: dict[str, Any]) -> MemoryEntry | None:
        """
        Update fields of an existing memory entry.

        Returns the updated MemoryEntry, or None if not found.
        """
        existing = await self.get(memory_id)
        if existing is None:
            return None

        allowed_fields = {
            "content", "confidence", "metadata", "archived_at",
            "expires_at", "superseded_by", "deleted_at",
        }
        update_data: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in allowed_fields:
                raise ValueError(f"Field '{key}' is not allowed in memory updates")
            if key == "content":
                update_data["content"] = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            elif key == "metadata":
                update_data["memory_metadata"] = _serialize_metadata(value)
            elif key in ("archived_at", "expires_at", "deleted_at"):
                update_data[key] = _to_naive_utc(value)
            else:
                update_data[key] = value

        if "updated_at" not in update_data:
            update_data["updated_at"] = datetime.now(timezone.utc)

        stmt = (
            update(MemoryModel)
            .where(MemoryModel.memory_id == memory_id, MemoryModel.deleted_at.is_(None))
            .values(**update_data)
            .returning(MemoryModel)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def delete(self, memory_id: str) -> bool:
        """
        Soft delete a memory entry.

        Returns True if the entry was found and deleted, False otherwise.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            update(MemoryModel)
            .where(MemoryModel.memory_id == memory_id, MemoryModel.deleted_at.is_(None))
            .values(deleted_at=now, updated_at=now)
            .returning(MemoryModel.memory_id)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        row = result.scalar_one_or_none()
        return row is not None

    async def supersede(self, memory_id: str, new_memory: MemoryEntry) -> MemoryEntry:
        """
        Create new memory entry and mark old as superseded.

        Returns the new MemoryEntry.
        """
        existing = await self.get(memory_id)
        if existing is None:
            raise ValueError(f"Memory {memory_id} not found or already deleted")

        persisted = await self.create(new_memory)
        await self.update(memory_id, {"superseded_by": persisted.memory_id})
        return persisted

    async def count(
        self,
        *,
        scope: MemoryScope | None = None,
        category: MemoryCategory | None = None,
        trust_level: TrustLevel | None = None,
        include_deleted: bool = False,
    ) -> int:
        """Return count of memory entries matching the given filters."""
        query = select(func.count()).select_from(MemoryModel)

        if scope is not None:
            scope_val = scope.value if isinstance(scope, MemoryScope) else str(scope)
            query = query.where(MemoryModel.scope == scope_val)
        if category is not None:
            cat_val = category.value if isinstance(category, MemoryCategory) else str(category)
            query = query.where(MemoryModel.category == cat_val)
        if trust_level is not None:
            trust_val = trust_level.value if isinstance(trust_level, TrustLevel) else str(trust_level)
            query = query.where(MemoryModel.trust_level == trust_val)
        if not include_deleted:
            query = query.where(MemoryModel.deleted_at.is_(None))

        result = await self._session.execute(query)
        return result.scalar_one() or 0

    async def search(
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
        offset: int = 0,
    ) -> tuple[list[MemoryEntry], int]:
        """
        Search memory entries with optional text query and filters.

        `query` should be a pre-built LIKE pattern (e.g. from MemoryRetrievalService).
        Returns (entries, total_count).
        Default ordering: created_at DESC, memory_id DESC (newest first).
        """
        limit = max(1, min(limit, _MAX_LIMIT))
        offset = max(0, min(offset, _MAX_OFFSET))

        query_stmt = select(MemoryModel)
        count_stmt = select(func.count()).select_from(MemoryModel)

        if query and query.strip():
            query_stmt = query_stmt.where(MemoryModel.content.like(query, escape="\\"))
            count_stmt = count_stmt.where(MemoryModel.content.like(query, escape="\\"))

        if scope is not None:
            scope_val = scope.value if isinstance(scope, MemoryScope) else str(scope)
            query_stmt = query_stmt.where(MemoryModel.scope == scope_val)
            count_stmt = count_stmt.where(MemoryModel.scope == scope_val)
        if scope_id is not None:
            query_stmt = query_stmt.where(MemoryModel.scope_id == scope_id)
            count_stmt = count_stmt.where(MemoryModel.scope_id == scope_id)
        if categories:
            cat_vals = [c.value if isinstance(c, MemoryCategory) else str(c) for c in categories]
            query_stmt = query_stmt.where(MemoryModel.category.in_(cat_vals))
            count_stmt = count_stmt.where(MemoryModel.category.in_(cat_vals))
        if trust_levels:
            trust_vals = [t.value if isinstance(t, TrustLevel) else str(t) for t in trust_levels]
            query_stmt = query_stmt.where(MemoryModel.trust_level.in_(trust_vals))
            count_stmt = count_stmt.where(MemoryModel.trust_level.in_(trust_vals))
        if sources:
            src_vals = [s.value if isinstance(s, ProvenanceSource) else str(s) for s in sources]
            query_stmt = query_stmt.where(MemoryModel.source.in_(src_vals))
            count_stmt = count_stmt.where(MemoryModel.source.in_(src_vals))
        if not include_deleted:
            query_stmt = query_stmt.where(MemoryModel.deleted_at.is_(None))
            count_stmt = count_stmt.where(MemoryModel.deleted_at.is_(None))
        if not include_superseded:
            query_stmt = query_stmt.where(MemoryModel.superseded_by.is_(None))
            count_stmt = count_stmt.where(MemoryModel.superseded_by.is_(None))
        if created_after is not None:
            query_stmt = query_stmt.where(MemoryModel.created_at >= created_after)
            count_stmt = count_stmt.where(MemoryModel.created_at >= created_after)
        if created_before is not None:
            query_stmt = query_stmt.where(MemoryModel.created_at <= created_before)
            count_stmt = count_stmt.where(MemoryModel.created_at <= created_before)

        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar_one() or 0

        query_stmt = query_stmt.order_by(MemoryModel.created_at.desc(), MemoryModel.memory_id.desc())
        query_stmt = query_stmt.limit(limit).offset(offset)

        result = await self._session.execute(query_stmt)
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models], total

    @staticmethod
    def _to_domain(model: MemoryModel | None) -> MemoryEntry | None:
        if model is None:
            return None
        try:
            content = json.loads(model.content) if model.content else {}
        except Exception:
            content = {}
        if not isinstance(content, dict):
            content = {}

        try:
            metadata = json.loads(model.memory_metadata) if model.memory_metadata else None
        except Exception:
            metadata = None

        return MemoryEntry(
            memory_id=model.memory_id,
            category=MemoryCategory(model.category),
            scope=MemoryScope(model.scope),
            scope_id=model.scope_id,
            content=content,
            trust_level=TrustLevel(model.trust_level),
            confidence=model.confidence,
            source=ProvenanceSource(model.source),
            source_id=model.source_id,
            created_at=_to_aware_utc(model.created_at),
            updated_at=_to_aware_utc(model.updated_at),
            archived_at=_to_aware_utc(model.archived_at),
            expires_at=_to_aware_utc(model.expires_at),
            superseded_by=model.superseded_by,
            deleted_at=_to_aware_utc(model.deleted_at),
            metadata=metadata,
        )


__all__ = ["MemoryRepository"]
