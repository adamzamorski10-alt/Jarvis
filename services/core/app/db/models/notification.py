"""
ORM Model — Notifications

Durable storage for application-owned notification records.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class NotificationModel(Base):
    """
    SQLAlchemy ORM model for notifications.

    Notifications are mutable user-facing records derived from events.
    They are NOT events, NOT audit records, and NOT delivery attempts.
    """
    __tablename__ = "notifications"

    notification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    notification_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="info", index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_event_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(74), nullable=True, index=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_metadata: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)

    __table_args__ = (
        Index("ix_notifications_created_at", "created_at"),
        Index("ix_notifications_read_at", "read_at"),
        Index("uq_notifications_source_event_type", "source_event_id", "notification_type", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationModel notification_id={self.notification_id!r} "
            f"type={self.notification_type!r} severity={self.severity!r} "
            f"read_at={self.read_at!r}>"
        )


__all__ = ["NotificationModel"]
