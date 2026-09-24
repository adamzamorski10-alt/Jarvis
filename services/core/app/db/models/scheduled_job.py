"""
ORM Model — Scheduled Jobs
Persistent storage for scheduler job definitions in SQLite.
"""
from datetime import datetime, timezone
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ScheduledJobModel(Base):
    """
    SQLAlchemy ORM model for scheduled jobs.
    """
    __tablename__ = "scheduled_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1, index=True)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    schedule_expr: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    occurrence_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True, index=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    claim_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    job_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_scheduled_jobs_next_run_at", "next_run_at"),
        Index("idx_scheduled_jobs_enabled", "enabled"),
        Index("idx_scheduled_jobs_target", "target_type", "target_id"),
        Index("idx_scheduled_jobs_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<ScheduledJobModel job_id={self.job_id!r} status={self.status} "
            f"schedule_type={self.schedule_type} next_run_at={self.next_run_at!r}>"
        )
