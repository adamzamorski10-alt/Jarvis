"""Tests — System status endpoint."""
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
async def test_core_is_running(client):
    response = await client.get("/api/system/status")
    assert response.json()["core"]["status"] == "running"


@pytest.mark.asyncio
async def test_database_is_connected(client):
    response = await client.get("/api/system/status")
    assert response.json()["database"]["status"] == "connected"


@pytest.mark.asyncio
async def test_ai_component_reports_real_provider_state(client):
    response = await client.get("/api/system/status")
    ai = response.json()["ai"]
    assert ai["status"] in {"ready", "online", "unreachable", "timeout", "error"}
    assert ai["message"] is not None
