"""Tests — System status and diagnostic endpoint."""
import pytest


@pytest.mark.asyncio
async def test_system_status_returns_200(client):
    response = await client.get("/api/system/status")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_system_status_schema(client):
    response = await client.get("/api/system/status")
    data = response.json()
    assert "core" in data
    assert "database" in data
    assert "ai" in data


@pytest.mark.asyncio
async def test_core_diagnostics_fields(client):
    response = await client.get("/api/system/status")
    core = response.json()["core"]
    assert core["status"] == "running"
    assert core["version"] == "0.1.0"
    assert isinstance(core["uptime_seconds"], (int, float))
    assert isinstance(core["start_time"], str)


@pytest.mark.asyncio
async def test_database_diagnostics_fields(client):
    response = await client.get("/api/system/status")
    db = response.json()["database"]
    assert db["status"] == "connected"
    assert db["engine"] == "SQLite (aiosqlite)"
    assert db["migration_status"] == "up_to_date"
    assert db["schema_version"] == "1.0"


@pytest.mark.asyncio
async def test_ai_diagnostics_without_secret_leakage(client):
    response = await client.get("/api/system/status")
    ai = response.json()["ai"]
    assert ai["status"] in {"ready", "online", "no_models", "stopped", "not_installed", "unreachable", "not_configured", "error"}
    assert "provider" in ai
    assert "configured" in ai
    # Ensure no API secret fields are exposed in response
    assert "openrouter_api_key" not in ai
    assert "kimi_api_key" not in ai
    assert "api_key" not in ai
