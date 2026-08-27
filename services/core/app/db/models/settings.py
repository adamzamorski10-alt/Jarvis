"""
ORM Model — Application Settings
Key-value store for all application configuration.
"""
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class AppSetting(Base):
    """
    Persistent application settings stored as key-value pairs.
    Keys are unique identifiers, values are JSON-serializable strings.
    """
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<AppSetting key={self.key!r} value={self.value!r}>"
