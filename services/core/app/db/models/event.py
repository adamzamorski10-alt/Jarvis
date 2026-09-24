"""
ORM Model — Events

Append-only event store for AI Operator lifecycle events.

All timestamps are UTC.  Records are immutable after creation.
Payload and metadata are stored as sanitized JSON text with size caps.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class EventModel(Base):
    """
    SQLAlchemy ORM model for lifecycle events.

    Records are append-only.  No UPDATE or DELETE operations are permitted
    except for retention-based deletion by time window.
    """

    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1.0"
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    causation_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(String(74), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)
    sensitivity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal"
    )

    __table_args__ = (
        Index("ix_events_occurred_at", "occurred_at"),
        Index("ix_events_recorded_at", "recorded_at"),
        Index("ix_events_event_type", "event_type"),
        Index("ix_events_source", "source"),
        Index("ix_events_correlation_id", "correlation_id"),
        Index("ix_events_causation_id", "causation_id"),
        Index("ix_events_task_id", "task_id"),
        Index("ix_events_plan_id", "plan_id"),
        Index("ix_events_run_id", "run_id"),
        Index("ix_events_execution_id", "execution_id"),
        Index("ix_events_sensitivity", "sensitivity"),
    )

    def __repr__(self) -> str:
        return (
            f"<EventModel event_id={self.event_id!r} type={self.event_type!r} "
            f"source={self.source!r} task_id={self.task_id!r} "
            f"occurred_at={self.occurred_at!r}>"
        )
