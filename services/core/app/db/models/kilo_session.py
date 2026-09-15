"""
ORM Model — Kilo Sessions
Persistent tracking of Kilo Code CLI execution sessions for recovery and audit.
"""
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, Integer, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class KiloSessionModel(Base):
    """
    Persistent record for a Kilo Code CLI execution session.

    Application owns all identity fields. Kilo session ID is stored as
    untrusted external metadata only.
    """
    __tablename__ = "kilo_sessions"

    app_session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(74), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    workspace_path: Mapped[str] = mapped_column(String(500), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    kilo_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    kilo_session_state: Mapped[str | None] = mapped_column(String(32), nullable=True)

    state: Mapped[str] = mapped_column(String(20), nullable=False, default="NEW", index=True)
    recovery_eligible: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)

    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    process_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    process_terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    authorization_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_scope_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_event_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_kilo_sessions_kilo_session", "kilo_session_id"),
        Index("idx_kilo_sessions_state_created", "state", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<KiloSessionModel app_session_id={self.app_session_id!r} "
            f"task_id={self.task_id!r} state={self.state} "
            f"kilo_session_id={self.kilo_session_id!r}>"
        )
