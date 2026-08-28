"""Unit tests for FastAPI lifespan shutdown lifecycle cleanup (Stage 4.3)."""
from unittest.mock import AsyncMock, patch
import pytest

from app.core.lifecycle import lifespan
from app.main import app


@pytest.mark.asyncio
async def test_lifecycle_shutdown_cleans_up_managed_ollama():
    """Test 1: When Python Core shuts down, managed Ollama process is stopped via process_manager.stop()."""
    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patch("app.db.database.init_db", new_callable=AsyncMock), \
         patch("app.services.settings_service.SettingsService.ensure_defaults", new_callable=AsyncMock), \
         patch("app.services.activity_service.ActivityService.create_event", new_callable=AsyncMock):

        mock_pm.managed_pid = 99999
        mock_pm.stop = AsyncMock(return_value={"success": True, "state": "stopped"})

        async with lifespan(app):
            pass  # startup runs here

        # Shutdown completed here
        mock_pm.stop.assert_called_once()


@pytest.mark.asyncio
async def test_lifecycle_shutdown_ignores_external_ollama():
    """Test 2: When Ollama is running externally (managed_pid == None), shutdown does NOT call stop()."""
    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patch("app.db.database.init_db", new_callable=AsyncMock), \
         patch("app.services.settings_service.SettingsService.ensure_defaults", new_callable=AsyncMock), \
         patch("app.services.activity_service.ActivityService.create_event", new_callable=AsyncMock):

        mock_pm.managed_pid = None
        mock_pm.stop = AsyncMock()

        async with lifespan(app):
            pass

        mock_pm.stop.assert_not_called()


@pytest.mark.asyncio
async def test_lifecycle_shutdown_when_stopped():
    """Test 3: When Ollama is stopped (managed_pid == None), shutdown completes cleanly."""
    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patch("app.db.database.init_db", new_callable=AsyncMock), \
         patch("app.services.settings_service.SettingsService.ensure_defaults", new_callable=AsyncMock), \
         patch("app.services.activity_service.ActivityService.create_event", new_callable=AsyncMock):

        mock_pm.managed_pid = None

        async with lifespan(app):
            pass


@pytest.mark.asyncio
async def test_lifecycle_shutdown_with_stale_managed_pid():
    """Test 4: When managed_pid is set but process already exited, cleanup handles it without crashing."""
    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patch("app.db.database.init_db", new_callable=AsyncMock), \
         patch("app.services.settings_service.SettingsService.ensure_defaults", new_callable=AsyncMock), \
         patch("app.services.activity_service.ActivityService.create_event", new_callable=AsyncMock):

        mock_pm.managed_pid = 7777
        mock_pm.stop = AsyncMock(return_value={"success": True, "state": "stopped"})

        async with lifespan(app):
            pass

        mock_pm.stop.assert_called_once()


@pytest.mark.asyncio
async def test_lifecycle_shutdown_resilient_to_stop_error():
    """Test 5: If process_manager.stop() raises an exception during shutdown, error is caught and shutdown completes."""
    with patch("app.ai.services.ollama_process_manager.process_manager") as mock_pm, \
         patch("app.db.database.init_db", new_callable=AsyncMock), \
         patch("app.services.settings_service.SettingsService.ensure_defaults", new_callable=AsyncMock), \
         patch("app.services.activity_service.ActivityService.create_event", new_callable=AsyncMock):

        mock_pm.managed_pid = 4444
        mock_pm.stop = AsyncMock(side_effect=RuntimeError("Process kill failed"))

        # Should not raise RuntimeError out of lifespan
        async with lifespan(app):
            pass

        mock_pm.stop.assert_called_once()
