"""Unit tests for Ollama autostart feature in FastAPI lifespan (Stage 4.5)."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.core.lifecycle import lifespan
from app.main import app


def _make_common_patches(autostart_val="false", ollama_url="http://127.0.0.1:11434"):
    """Helper that returns a dict of common patches used across autostart tests."""
    return {
        "init_db": patch("app.db.database.init_db", new_callable=AsyncMock),
        "ensure_defaults": patch(
            "app.services.settings_service.SettingsService.ensure_defaults",
            new_callable=AsyncMock,
        ),
        "activity_event": patch(
            "app.services.activity_service.ActivityService.create_event",
            new_callable=AsyncMock,
        ),
        "get_one": patch(
            "app.services.settings_service.SettingsService.get_one",
            new_callable=AsyncMock,
            side_effect=lambda key: (
                autostart_val if key == "ai_ollama_autostart"
                else ollama_url if key == "ai_ollama_url"
                else None
            ),
        ),
    }


@pytest.mark.asyncio
async def test_autostart_off_does_not_start_ollama():
    """Test 1: autostart_ollama = false → process_manager.start() is NOT called."""
    patches = _make_common_patches(autostart_val="false")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"], \
         patches["get_one"]:

        mock_pm.managed_pid = None
        mock_pm.start = AsyncMock()
        mock_pm.is_running = AsyncMock(return_value=False)

        async with lifespan(app):
            pass

        mock_pm.start.assert_not_called()


@pytest.mark.asyncio
async def test_autostart_on_starts_ollama_when_stopped():
    """Test 2: autostart_ollama = true + Ollama stopped → process_manager.start() is called."""
    patches = _make_common_patches(autostart_val="true")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"], \
         patches["get_one"]:

        mock_pm.managed_pid = None
        mock_pm.is_running = AsyncMock(return_value=False)
        mock_pm.start = AsyncMock(return_value={"success": True, "state": "running_managed", "managed_pid": 12345})
        mock_pm.stop = AsyncMock(return_value={"success": True})

        async with lifespan(app):
            pass

        mock_pm.start.assert_called_once()


@pytest.mark.asyncio
async def test_autostart_on_does_not_start_when_ollama_running_external():
    """Test 3: autostart_ollama = true + Ollama already running → process_manager.start() NOT called."""
    patches = _make_common_patches(autostart_val="true")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"], \
         patches["get_one"]:

        mock_pm.managed_pid = None
        mock_pm.is_running = AsyncMock(return_value=True)  # already running (external)
        mock_pm.start = AsyncMock()
        mock_pm.stop = AsyncMock()

        async with lifespan(app):
            pass

        mock_pm.start.assert_not_called()


@pytest.mark.asyncio
async def test_autostart_on_does_not_start_when_already_managed():
    """Test 4: autostart_ollama = true + Ollama running managed → process_manager.start() NOT called."""
    patches = _make_common_patches(autostart_val="true")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"], \
         patches["get_one"]:

        mock_pm.managed_pid = 99999
        mock_pm.is_running = AsyncMock(return_value=True)  # already running managed
        mock_pm.start = AsyncMock()
        mock_pm.stop = AsyncMock(return_value={"success": True})

        async with lifespan(app):
            pass

        mock_pm.start.assert_not_called()


@pytest.mark.asyncio
async def test_autostart_on_start_failure_does_not_crash_core():
    """Test 5: autostart_ollama = true + start() fails → Core still starts up (no exception raised)."""
    patches = _make_common_patches(autostart_val="true")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"], \
         patches["get_one"]:

        mock_pm.managed_pid = None
        mock_pm.is_running = AsyncMock(return_value=False)
        mock_pm.start = AsyncMock(return_value={"success": False, "state": "failed", "error": "Ollama not found"})
        mock_pm.stop = AsyncMock()

        # Should not raise
        async with lifespan(app):
            pass


@pytest.mark.asyncio
async def test_autostart_on_start_exception_does_not_crash_core():
    """Test 6: autostart_ollama = true + start() raises exception → Core still starts (exception caught)."""
    patches = _make_common_patches(autostart_val="true")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"], \
         patches["get_one"]:

        mock_pm.managed_pid = None
        mock_pm.is_running = AsyncMock(return_value=False)
        mock_pm.start = AsyncMock(side_effect=TimeoutError("Ollama startup timed out"))
        mock_pm.stop = AsyncMock()

        # Should not raise
        async with lifespan(app):
            pass


@pytest.mark.asyncio
async def test_autostart_ollama_models_env_preserved():
    """Test 7: OLLAMA_MODELS env var is preserved when autostart is triggered."""
    import os

    original = os.environ.get("OLLAMA_MODELS")
    os.environ["OLLAMA_MODELS"] = r"F:\AI-Models\.ollama\models"

    patches = _make_common_patches(autostart_val="true")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"], \
         patches["get_one"]:

        mock_pm.managed_pid = None
        mock_pm.is_running = AsyncMock(return_value=False)
        mock_pm.start = AsyncMock(return_value={"success": True, "state": "running_managed", "managed_pid": 55555})
        mock_pm.stop = AsyncMock(return_value={"success": True})

        async with lifespan(app):
            pass

        # Env var must remain unchanged throughout
        assert os.environ.get("OLLAMA_MODELS") == r"F:\AI-Models\.ollama\models"

    # Restore
    if original is None:
        del os.environ["OLLAMA_MODELS"]
    else:
        os.environ["OLLAMA_MODELS"] = original


@pytest.mark.asyncio
async def test_autostart_shutdown_stops_managed_ollama():
    """Test 8: After autostart, shutdown still stops managed Ollama process."""
    patches = _make_common_patches(autostart_val="true")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"], \
         patches["get_one"]:

        mock_pm.is_running = AsyncMock(return_value=False)
        mock_pm.start = AsyncMock(return_value={"success": True, "state": "running_managed", "managed_pid": 22222})
        mock_pm.stop = AsyncMock(return_value={"success": True})

        # Simulate that after start(), managed_pid is set
        mock_pm.managed_pid = 22222

        async with lifespan(app):
            pass

        mock_pm.stop.assert_called_once()


@pytest.mark.asyncio
async def test_autostart_shutdown_leaves_external_ollama_running():
    """Test 9: External Ollama (managed_pid=None) is NOT stopped during shutdown even if autostart is on."""
    patches = _make_common_patches(autostart_val="true")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"], \
         patches["get_one"]:

        mock_pm.managed_pid = None
        mock_pm.is_running = AsyncMock(return_value=True)  # already running externally
        mock_pm.start = AsyncMock()
        mock_pm.stop = AsyncMock()

        async with lifespan(app):
            pass

        mock_pm.stop.assert_not_called()
        mock_pm.start.assert_not_called()


@pytest.mark.asyncio
async def test_autostart_setting_persisted_via_settings_service():
    """Test 10: ai_ollama_autostart is read via SettingsService.get_one() on startup."""
    patches = _make_common_patches(autostart_val="true")

    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patches["init_db"], patches["ensure_defaults"], patches["activity_event"] as _, \
         patches["get_one"] as mock_get_one:

        mock_pm.managed_pid = None
        mock_pm.is_running = AsyncMock(return_value=False)
        mock_pm.start = AsyncMock(return_value={"success": True, "managed_pid": 33333})
        mock_pm.stop = AsyncMock(return_value={"success": True})
        mock_pm.managed_pid = 33333

        async with lifespan(app):
            pass

        # Confirm that ai_ollama_autostart was queried
        called_keys = [call.args[0] for call in mock_get_one.call_args_list]
        assert "ai_ollama_autostart" in called_keys
