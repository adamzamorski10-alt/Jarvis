"""
ORM Model — Pending Approvals
Persistent storage for ASK_USER approval requests awaiting user decision.
"""
from datetime import datetime, timezone
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PendingApprovalModel(Base):
    __tablename__ = "pending_approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[str | None] = mapped_column(Text, nullable=True)
    arguments_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_definition_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tool_definition_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PendingApprovalModel approval_id={self.approval_id!r} "
            f"tool_id={self.tool_id!r} action={self.action!r} status={self.status}>"
        )
