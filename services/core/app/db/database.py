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
    from app.db.models import (  # noqa: F401
        settings,
        activity,
        conversation,
        permission_grant,
        audit_event,
        pending_approval,
        capability_approval,
        development_task,
        task_dependency,
        plan_fingerprint,
    )

    engine = get_engine()
    async with engine.begin() as conn:
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
            if "pending_approvals" in inspector.get_table_names():
                columns = {col["name"] for col in inspector.get_columns("pending_approvals")}
                if "action" not in columns:
                    connection.execute(text("ALTER TABLE pending_approvals ADD COLUMN action VARCHAR(100) NOT NULL DEFAULT 'unknown'"))
                if "arguments_hash" not in columns:
                    connection.execute(text("ALTER TABLE pending_approvals ADD COLUMN arguments_hash VARCHAR(64)"))
                if "tool_definition_version" not in columns:
                    connection.execute(text("ALTER TABLE pending_approvals ADD COLUMN tool_definition_version VARCHAR(50)"))
                if "tool_definition_fingerprint" not in columns:
                    connection.execute(text("ALTER TABLE pending_approvals ADD COLUMN tool_definition_fingerprint VARCHAR(64)"))
            if "capability_approvals" not in inspector.get_table_names():
                connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS capability_approvals (
                        approval_id VARCHAR(64) PRIMARY KEY,
                        intent_id VARCHAR(64) NOT NULL,
                        operation_type VARCHAR(50) NOT NULL,
                        scope_fingerprint VARCHAR(64) NOT NULL,
                        effective_risk VARCHAR(20) NOT NULL,
                        requested_by VARCHAR(64),
                        approved_by VARCHAR(64),
                        denied_by VARCHAR(64),
                        request_reason VARCHAR(1024),
                        decision_reason VARCHAR(1024),
                        status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        consumed_at TIMESTAMP WITH TIME ZONE
                    )
                """))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_capability_approvals_status ON capability_approvals(status)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_capability_approvals_intent_id ON capability_approvals(intent_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_capability_approvals_scope_fingerprint ON capability_approvals(scope_fingerprint)"))
            if "development_tasks" in inspector.get_table_names():
                columns = {col["name"] for col in inspector.get_columns("development_tasks")}
                if "plan_id" not in columns:
                    connection.execute(text("ALTER TABLE development_tasks ADD COLUMN plan_id VARCHAR(64)"))
                if "task_key" not in columns:
                    connection.execute(text("ALTER TABLE development_tasks ADD COLUMN task_key VARCHAR(64)"))
                connection.execute(
                    text("CREATE UNIQUE INDEX IF NOT EXISTS uq_development_tasks_plan_task_key ON development_tasks(plan_id, task_key)")
                )

        await conn.run_sync(migrate_sqlite)
        await conn.run_sync(Base.metadata.create_all)


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
