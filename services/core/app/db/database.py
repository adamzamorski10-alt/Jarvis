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
        kilo_session,
        event,
        scheduled_job,
        notification,
        memory,
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
            if "kilo_sessions" not in inspector.get_table_names():
                connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS kilo_sessions (
                        app_session_id VARCHAR(64) PRIMARY KEY,
                        task_id VARCHAR(74) NOT NULL,
                        project_id VARCHAR(100) NOT NULL,
                        workspace_path VARCHAR(500) NOT NULL,
                        execution_id VARCHAR(64) NOT NULL,
                        kilo_session_id VARCHAR(128),
                        kilo_session_state VARCHAR(32),
                        state VARCHAR(20) NOT NULL DEFAULT 'NEW',
                        recovery_eligible INTEGER NOT NULL DEFAULT 1,
                        pid INTEGER,
                        process_started_at TIMESTAMP WITH TIME ZONE,
                        process_terminated_at TIMESTAMP WITH TIME ZONE,
                        authorization_version VARCHAR(64),
                        approval_scope_hash VARCHAR(64),
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        last_observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        completed_at TIMESTAMP WITH TIME ZONE,
                        error_message TEXT,
                        raw_event_snapshot TEXT
                    )
                """))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_kilo_sessions_task_id ON kilo_sessions(task_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_kilo_sessions_state ON kilo_sessions(state)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_kilo_sessions_kilo_session_id ON kilo_sessions(kilo_session_id)"))
            if "events" not in inspector.get_table_names():
                connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS events (
                        event_id VARCHAR(64) PRIMARY KEY,
                        event_type VARCHAR(128) NOT NULL,
                        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        source VARCHAR(128) NOT NULL,
                        event_version VARCHAR(32) NOT NULL DEFAULT '1.0',
                        correlation_id VARCHAR(128),
                        causation_id VARCHAR(64),
                        task_id VARCHAR(74),
                        plan_id VARCHAR(64),
                        run_id VARCHAR(64),
                        execution_id VARCHAR(64),
                        actor VARCHAR(128),
                        payload TEXT,
                        metadata TEXT,
                        sensitivity VARCHAR(20) NOT NULL DEFAULT 'internal'
                    )
                """))
            if "scheduled_jobs" not in inspector.get_table_names():
                connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS scheduled_jobs (
                        job_id VARCHAR(64) PRIMARY KEY,
                        name VARCHAR(200) NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        schedule_type VARCHAR(20) NOT NULL,
                        schedule_expr VARCHAR(200) NOT NULL,
                        timezone VARCHAR(64),
                        target_type VARCHAR(20) NOT NULL,
                        target_id VARCHAR(64) NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                        last_run_at TIMESTAMP WITH TIME ZONE,
                        next_run_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        occurrence_id VARCHAR(128),
                        claim_token VARCHAR(64),
                        claim_sequence INTEGER NOT NULL DEFAULT 0,
                        claimed_at TIMESTAMP WITH TIME ZONE,
                        lease_until TIMESTAMP WITH TIME ZONE,
                        execution_id VARCHAR(64),
                        job_metadata TEXT
                    )
                """))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run_at ON scheduled_jobs(next_run_at)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_enabled ON scheduled_jobs(enabled)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_target ON scheduled_jobs(target_type, target_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_status ON scheduled_jobs(status)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_occurrence_id ON scheduled_jobs(occurrence_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_claim_token ON scheduled_jobs(claim_token)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_lease_until ON scheduled_jobs(lease_until)"))
            if "scheduled_jobs" in inspector.get_table_names():
                columns = {col["name"] for col in inspector.get_columns("scheduled_jobs")}
                if "claim_sequence" not in columns:
                    connection.execute(text("ALTER TABLE scheduled_jobs ADD COLUMN claim_sequence INTEGER NOT NULL DEFAULT 0"))
            if "notifications" not in inspector.get_table_names():
                connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS notifications (
                        notification_id VARCHAR(64) PRIMARY KEY,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        notification_type VARCHAR(64) NOT NULL,
                        severity VARCHAR(20) NOT NULL DEFAULT 'info',
                        title VARCHAR(200) NOT NULL,
                        body TEXT,
                        source_event_id VARCHAR(64) NOT NULL,
                        correlation_id VARCHAR(128),
                        task_id VARCHAR(74),
                        plan_id VARCHAR(64),
                        run_id VARCHAR(64),
                        execution_id VARCHAR(64),
                        read_at TIMESTAMP WITH TIME ZONE,
                        expires_at TIMESTAMP WITH TIME ZONE,
                        metadata TEXT
                    )
                """))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_created_at ON notifications(created_at)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_notification_type ON notifications(notification_type)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_severity ON notifications(severity)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_source_event_id ON notifications(source_event_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_correlation_id ON notifications(correlation_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_task_id ON notifications(task_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_plan_id ON notifications(plan_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_execution_id ON notifications(execution_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_read_at ON notifications(read_at)"))
                connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_source_event_type ON notifications(source_event_id, notification_type)"))
            if "memories" not in inspector.get_table_names():
                connection.execute(text("""
                    CREATE TABLE IF NOT EXISTS memories (
                        memory_id VARCHAR(64) PRIMARY KEY,
                        category VARCHAR(32) NOT NULL,
                        scope VARCHAR(16) NOT NULL,
                        scope_id VARCHAR(100),
                        content TEXT NOT NULL,
                        trust_level VARCHAR(16) NOT NULL,
                        confidence REAL NOT NULL DEFAULT 1.0,
                        source VARCHAR(32) NOT NULL,
                        source_id VARCHAR(128),
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        archived_at TIMESTAMP WITH TIME ZONE,
                        expires_at TIMESTAMP WITH TIME ZONE,
                        superseded_by VARCHAR(64),
                        deleted_at TIMESTAMP WITH TIME ZONE,
                        metadata TEXT
                    )
                """))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, scope_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_trust ON memories(trust_level)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_deleted_at ON memories(deleted_at)"))


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
