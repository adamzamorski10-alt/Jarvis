"""Tests — Error handling scenarios."""
import pytest


@pytest.mark.asyncio
async def test_put_settings_missing_body(client):
    """Settings update with missing body returns 422."""
    response = await client.put("/api/settings", json={})
    # Missing 'settings' key
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_settings_null_value(client):
    """Settings can be set to null (to unset)."""
    response = await client.put(
        "/api/settings",
        json={"settings": {"custom_key": None}},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_activity_limit_capped(client):
    """Activity endpoint enforces max limit."""
    response = await client.get("/api/activity?limit=300")
    # Should get 422 because limit > 200 (Query constraint)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_negative_offset_rejected(client):
    response = await client.get("/api/activity?offset=-1")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_route_returns_404(client):
    response = await client.get("/api/nonexistent")
    assert response.status_code == 404
