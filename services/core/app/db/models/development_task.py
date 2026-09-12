"""
ORM Model — Development Tasks
Persistent storage for AI Operator autonomous development tasks in SQLite.
"""
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class DevelopmentTaskModel(Base):
    """
    SQLAlchemy ORM model for development tasks.
    """
    __tablename__ = "development_tasks"

    task_id: Mapped[str] = mapped_column(String(74), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("development_tasks.task_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stage 6.7 — Heartbeat & Crash Recovery Tracking
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    recovery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_recovery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_recovery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recovery_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    execution_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Stage 6.8 — Execution termination & cleanup safety
    execution_terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workspace_cleanup_pending: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)

    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    __table_args__ = (
        Index("uq_development_tasks_plan_task_key", "plan_id", "task_key", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<DevelopmentTaskModel task_id={self.task_id!r} project_id={self.project_id!r} "
            f"status={self.status} priority={self.priority} attempt_count={self.attempt_count} "
            f"recovery_attempt_count={self.recovery_attempt_count}>"
        )

