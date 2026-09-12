"""
ORM Model — Plan Fingerprints
Canonical fingerprint record for plan-level idempotency and conflict detection.
"""
from datetime import datetime, timezone
from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PlanFingerprintModel(Base):
    """
    One row per immutable plan.  The fingerprint is computed from canonical
    task content and dependency edges; timestamps and mutable state are excluded.

    A unique constraint on plan_id guarantees at most one persisted fingerprint
    per plan, enabling atomic conflict detection.
    """
    __tablename__ = "plan_fingerprints"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_plan_fingerprints_plan_id"),
    )

    def __repr__(self) -> str:
        return f"<PlanFingerprintModel plan_id={self.plan_id!r} fingerprint={self.fingerprint!r}>"
