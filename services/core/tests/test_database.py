"""Tests — Database initialization and schema."""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import get_settings, Settings
from app.db.database import Base, init_db


@pytest.mark.asyncio
async def test_tables_are_created(engine):
    async with engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
    assert "app_settings" in tables
    assert "activity_events" in tables
    assert "permission_grants" in tables
    assert "audit_events" in tables


@pytest.mark.asyncio
async def test_settings_table_columns(engine):
    async with engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [
                col["name"]
                for col in inspect(sync_conn).get_columns("app_settings")
            ]
        )
    assert "key" in columns
    assert "value" in columns
    assert "updated_at" in columns


@pytest.mark.asyncio
async def test_activity_table_columns(engine):
    async with engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: [
                col["name"]
                for col in inspect(sync_conn).get_columns("activity_events")
            ]
        )
    assert "id" in columns
    assert "timestamp" in columns
    assert "level" in columns
    assert "category" in columns
    assert "message" in columns
    assert "details" in columns


@pytest.mark.asyncio
async def test_database_connectivity(engine):
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


@pytest.mark.asyncio
async def test_migration_adds_plan_id_and_task_key_to_legacy_db():
    """init_db must migrate a pre-8.2A database safely and idempotently."""
    import app.db.database as db_module
    original_settings_fn = db_module.get_settings
    original_engine = db_module._engine
    original_factory = db_module._session_factory

    with tempfile.TemporaryDirectory() as tmpdir:
        legacy_path = os.path.join(tmpdir, "legacy.db")
        legacy_url = f"sqlite+aiosqlite:///{legacy_path}"

        engine = create_async_engine(legacy_url, connect_args={"check_same_thread": False})

        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: c.execute(
                    text(
                        """
                        CREATE TABLE development_tasks (
                            task_id VARCHAR(64) PRIMARY KEY,
                            project_id VARCHAR(100) NOT NULL,
                            title VARCHAR(200) NOT NULL,
                            description TEXT NOT NULL,
                            status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
                            priority INTEGER NOT NULL DEFAULT 0
                        )
                        """
                    )
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO development_tasks (task_id, project_id, title, description) "
                    "VALUES ('task_legacy1', 'proj', 'Legacy', 'Desc')"
                )
            )

        legacy_settings = Settings(db_path=legacy_path)
        db_module._engine = None
        db_module._session_factory = None
        if hasattr(original_settings_fn, "cache_clear"):
            original_settings_fn.cache_clear()
        db_module.get_settings = lambda: legacy_settings

        created_engine = None
        try:
            await init_db()
            created_engine = db_module._engine

            async with engine.connect() as conn:
                columns = await conn.run_sync(
                    lambda c: [col["name"] for col in inspect(c).get_columns("development_tasks")]
                )
                assert "plan_id" in columns
                assert "task_key" in columns

                result = await conn.execute(text("SELECT task_id, project_id, title FROM development_tasks"))
                rows = result.fetchall()
                assert len(rows) == 1
                assert rows[0].task_id == "task_legacy1"
                assert rows[0].project_id == "proj"

                indexes = await conn.run_sync(
                    lambda c: [idx["name"] for idx in inspect(c).get_indexes("development_tasks")]
                )
                assert "uq_development_tasks_plan_task_key" in indexes

            await init_db()

            async with engine.connect() as conn:
                columns = await conn.run_sync(
                    lambda c: [col["name"] for col in inspect(c).get_columns("development_tasks")]
                )
                assert "plan_id" in columns
                assert "task_key" in columns

                result = await conn.execute(text("SELECT COUNT(*) FROM development_tasks"))
                assert result.scalar() == 1

                result = await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='uq_development_tasks_plan_task_key'"
                    )
                )
                assert result.scalar() == 1
        finally:
            db_module.get_settings = original_settings_fn
            db_module._engine = original_engine
            db_module._session_factory = original_factory
            if created_engine is not None:
                await created_engine.dispose()
            await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_creates_new_tables_and_columns():
    """init_db must work correctly on a fresh database."""
    import app.db.database as db_module
    original_settings_fn = db_module.get_settings
    original_engine = db_module._engine
    original_factory = db_module._session_factory

    with tempfile.TemporaryDirectory() as tmpdir:
        fresh_path = os.path.join(tmpdir, "fresh.db")
        fresh_url = f"sqlite+aiosqlite:///{fresh_path}"

        engine = create_async_engine(fresh_url, connect_args={"check_same_thread": False})

        fresh_settings = Settings(db_path=fresh_path)
        db_module._engine = None
        db_module._session_factory = None
        if hasattr(original_settings_fn, "cache_clear"):
            original_settings_fn.cache_clear()
        db_module.get_settings = lambda: fresh_settings

        created_engine = None
        try:
            await init_db()
            created_engine = db_module._engine

            async with engine.connect() as conn:
                tables = await conn.run_sync(
                    lambda c: inspect(c).get_table_names()
                )
                assert "development_tasks" in tables
                assert "plan_fingerprints" in tables
                assert "task_dependencies" in tables

                columns = await conn.run_sync(
                    lambda c: [col["name"] for col in inspect(c).get_columns("development_tasks")]
                )
                assert "plan_id" in columns
                assert "task_key" in columns
                assert "task_id" in columns

                indexes = await conn.run_sync(
                    lambda c: [idx["name"] for idx in inspect(c).get_indexes("development_tasks")]
                )
                assert "uq_development_tasks_plan_task_key" in indexes
        finally:
            db_module.get_settings = original_settings_fn
            db_module._engine = original_engine
            db_module._session_factory = original_factory
            if created_engine is not None:
                await created_engine.dispose()
            await engine.dispose()
