"""Tests — Database initialization and schema."""
import pytest
from sqlalchemy import text, inspect


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
