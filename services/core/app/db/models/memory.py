"""
ORM Model — Memories
Persistent storage for durable memory/knowledge entries in SQLite.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, Float, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class MemoryModel(Base):
    """
    SQLAlchemy ORM model for memory entries.

    Memories are durable knowledge entries. They are NOT execution state,
    NOT events, and NOT authorization records.

    Soft delete is represented by deleted_at timestamp.
    Supersession is represented by superseded_by memory_id.
    """

    __tablename__ = "memories"

    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scope_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    trust_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    memory_metadata: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)

    __table_args__ = (
        Index("idx_memories_scope", "scope", "scope_id"),
        Index("idx_memories_category", "category"),
        Index("idx_memories_trust", "trust_level"),
        Index("idx_memories_source", "source"),
        Index("idx_memories_created_at", "created_at"),
        Index("idx_memories_deleted_at", "deleted_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<MemoryModel memory_id={self.memory_id!r} "
            f"category={self.category!r} scope={self.scope!r} "
            f"trust_level={self.trust_level!r} deleted_at={self.deleted_at!r}>"
        )


__all__ = ["MemoryModel"]
