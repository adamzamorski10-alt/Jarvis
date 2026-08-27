"""Tests — Settings endpoint and service."""
import pytest


# --- Service unit tests ---

@pytest.mark.asyncio
async def test_defaults_are_seeded(settings_service):
    all_settings = await settings_service.get_all()
    assert "theme" in all_settings
    assert "language" in all_settings
    assert "autonomy_mode" in all_settings


@pytest.mark.asyncio
async def test_default_theme_is_dark(settings_service):
    value = await settings_service.get_one("theme")
    assert value == "dark"


@pytest.mark.asyncio
async def test_default_autonomy_mode_is_manual(settings_service):
    value = await settings_service.get_one("autonomy_mode")
    assert value == "manual"


@pytest.mark.asyncio
async def test_update_setting(settings_service):
    await settings_service.update({"theme": "light"})
    value = await settings_service.get_one("theme")
    assert value == "light"


@pytest.mark.asyncio
async def test_update_multiple_settings(settings_service):
    await settings_service.update({"theme": "light", "language": "pl"})
    all_settings = await settings_service.get_all()
    assert all_settings["theme"] == "light"
    assert all_settings["language"] == "pl"


@pytest.mark.asyncio
async def test_get_unknown_key_returns_none(settings_service):
    value = await settings_service.get_one("nonexistent_key")
    assert value is None


# --- API integration tests ---

@pytest.mark.asyncio
async def test_get_settings_endpoint(client):
    response = await client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "settings" in data
    assert isinstance(data["settings"], dict)


@pytest.mark.asyncio
async def test_get_settings_has_defaults(client):
    response = await client.get("/api/settings")
    settings = response.json()["settings"]
    assert settings.get("theme") == "dark"
    assert settings.get("autonomy_mode") == "manual"


@pytest.mark.asyncio
async def test_put_settings_valid(client):
    response = await client.put(
        "/api/settings",
        json={"settings": {"theme": "light"}},
    )
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["theme"] == "light"


@pytest.mark.asyncio
async def test_put_settings_invalid_theme(client):
    response = await client.put(
        "/api/settings",
        json={"settings": {"theme": "rainbow"}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_settings_invalid_autonomy_mode(client):
    response = await client.put(
        "/api/settings",
        json={"settings": {"autonomy_mode": "godmode"}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_settings_allows_extension_key(client):
    response = await client.put(
        "/api/settings",
        json={"settings": {"unknown": "value"}},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_put_settings_allows_null_value(client):
    response = await client.put(
        "/api/settings",
        json={"settings": {"custom_key": None}},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_settings_persist_across_calls(client):
    # Update
    await client.put("/api/settings", json={"settings": {"theme": "light"}})
    # Read back
    response = await client.get("/api/settings")
    assert response.json()["settings"]["theme"] == "light"
