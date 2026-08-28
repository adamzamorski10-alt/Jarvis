"""
ORM Model — Permission Grants
Persistent storage for user-granted tool execution permissions in SQLite.
"""
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PermissionGrantModel(Base):
    """
    SQLAlchemy ORM model for user permission grants.
    
    Grant Types:
        - 'one_time': Single-use grant, atomically consumed upon execution.
        - 'time_bound': Active until expires_at datetime.
        - 'persistent': Active until explicitly revoked.
    """
    __tablename__ = "permission_grants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    action_pattern: Mapped[str] = mapped_column(String(100), nullable=False, default="*")
    grant_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    def __repr__(self) -> str:
        return (
            f"<PermissionGrantModel id={self.id} tool_id={self.tool_id!r} "
            f"action={self.action_pattern!r} type={self.grant_type} consumed={self.consumed}>"
        )
