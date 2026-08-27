"""Tests — Health endpoint."""
import pytest


@pytest.mark.asyncio
async def test_health_returns_200(client):
    response = await client.get("/api/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_response_schema(client):
    response = await client.get("/api/health")
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "ai-operator-core"
    assert "version" in data
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_tauri_production_origin_is_allowed(client):
    response = await client.options(
        "/api/health",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


@pytest.mark.asyncio
async def test_health_version_format(client):
    response = await client.get("/api/health")
    version = response.json()["version"]
    parts = version.split(".")
    assert len(parts) == 3, "Version should be in semver format x.y.z"
