"""
ORM Model — Activity Events
Immutable audit/activity log for all system events.
Designed to be extensible for future tool, agent, and task events.
"""
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ActivityEvent(Base):
    """
    Immutable activity/audit record.

    Categories (current):
        system, database, application, api, settings

    Future categories:
        tool, agent, task, automation, approval, security
    """
    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    level: Mapped[str] = mapped_column(
        String(20),
        default="INFO",
        index=True,
    )  # DEBUG | INFO | WARNING | ERROR | CRITICAL
    category: Mapped[str] = mapped_column(
        String(50),
        default="system",
        index=True,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<ActivityEvent id={self.id} level={self.level} category={self.category} msg={self.message!r:.40}>"
