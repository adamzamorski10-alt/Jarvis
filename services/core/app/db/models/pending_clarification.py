"""
ORM Model — Pending Clarifications.

Persistent storage for clarification sessions awaiting user response.
"""
from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PendingClarificationModel(Base):
    __tablename__ = "pending_clarifications"

    clarification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(74), nullable=True, index=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_token_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    round_number: Mapped[int] = mapped_column(default=1, nullable=False)
    max_rounds: Mapped[int] = mapped_column(default=3, nullable=False)

    questions: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answers: Mapped[str | None] = mapped_column(Text, nullable=True)
    re_evaluation_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    re_evaluation_questions: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PendingClarificationModel clarification_id={self.clarification_id!r} "
            f"conversation_id={self.conversation_id!r} status={self.status}>"
        )
