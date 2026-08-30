"""
AI Operator Core — Database Engine & Session Management
"""
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
    AsyncEngine,
)
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.debug,
            connect_args={"check_same_thread": False},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


# Convenience: module-level session maker (populated lazily)
def AsyncSessionLocal() -> AsyncSession:
    """Create a new async session."""
    return get_session_factory()()


async def init_db() -> None:
    """Create all tables if they don't exist and run lightweight migrations."""
    # Import models so SQLAlchemy knows about them
    from app.db.models import settings, activity, conversation, permission_grant, audit_event  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def migrate_sqlite(connection):
            from sqlalchemy import inspect, text
            inspector = inspect(connection)
            if "messages" in inspector.get_table_names():
                columns = {col["name"] for col in inspector.get_columns("messages")}
                if "client_message_id" not in columns:
                    connection.execute(text("ALTER TABLE messages ADD COLUMN client_message_id VARCHAR(64)"))
            if "audit_events" in inspector.get_table_names():
                columns = {col["name"] for col in inspector.get_columns("audit_events")}
                if "arguments_json" in columns and "arguments" not in columns:
                    connection.execute(text("ALTER TABLE audit_events RENAME COLUMN arguments_json TO arguments"))
                if "result_json" in columns and "result" not in columns:
                    connection.execute(text("ALTER TABLE audit_events RENAME COLUMN result_json TO result"))
                if "reason" not in columns:
                    connection.execute(text("ALTER TABLE audit_events ADD COLUMN reason VARCHAR(500) DEFAULT 'unknown'"))

        await conn.run_sync(migrate_sqlite)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
