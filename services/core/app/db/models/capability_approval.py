"""
ORM Model — Capability Approvals

Persistent storage for application-owned approval requests for
AI Manager policy/capability decisions.
"""
from datetime import datetime, timezone
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class CapabilityApprovalModel(Base):
    __tablename__ = "capability_approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    intent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    scope_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    effective_risk: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    denied_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<CapabilityApprovalModel approval_id={self.approval_id!r} "
            f"intent_id={self.intent_id!r} status={self.status}>"
        )
