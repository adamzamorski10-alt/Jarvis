"""
ORM Model — Audit Events
Structured, immutable audit log for tool operations, permission decisions,
approvals, and executions. Designed for security traceability and future analysis.
"""
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

class AuditEventModel(Base):
    """
    SQLAlchemy ORM model for structured audit events.

    All timestamps are UTC. Arguments and result are stored as sanitized JSON text
    with a size cap. Sensitive data must be redacted before persisting.
    """
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str | None] = mapped_column(String(100), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    autonomy_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    grant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Sanitized, size-capped JSON payload (not raw user input)
    arguments: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_audit_events_timestamp", "timestamp"),
        Index("ix_audit_events_event_type", "event_type"),
        Index("ix_audit_events_request_id", "request_id"),
        Index("ix_audit_events_tool_id", "tool_id"),
        Index("ix_audit_events_decision", "decision"),
        Index("ix_audit_events_grant_id", "grant_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditEventModel id={self.id} type={self.event_type} "
            f"tool={self.tool_id!r} decision={self.decision}>"
        )
