"""Unit tests for OllamaProcessManager (Stage 4.1 backend process lifecycle manager)."""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.ai.services.ollama_process_manager import OllamaProcessManager


@pytest.fixture
def manager():
    return OllamaProcessManager()


@pytest.mark.asyncio
async def test_start_when_already_running_externally(manager):
    """Test 1: If Ollama is already running externally, start() does not spawn a second process."""
    with patch.object(manager, "check_api_responding", return_value=True):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            res = await manager.start()

            assert res["success"] is True
            assert res["already_running"] is True
            assert res["state"] == "running_external"
            assert res["managed"] is False
            assert res["managed_pid"] is None
            assert manager.managed_pid is None
            mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_start_when_not_running_spawns_process(manager):
    """Test 2: If Ollama is not running and executable exists, start() spawns process and sets managed_pid."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.returncode = None

    # Step 1 check_api_responding returns False (not running), then wait_until_ready check_api_responding returns True (ready)
    with patch.object(manager, "check_api_responding", side_effect=[False, True]), \
         patch("app.ai.services.ollama_process_manager.find_ollama_executable", return_value="C:\\Ollama\\ollama.exe"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process) as mock_exec:

        res = await manager.start()

        assert res["success"] is True
        assert res["already_running"] is False
        assert res["state"] == "running_managed"
        assert res["managed"] is True
        assert res["managed_pid"] == 12345
        assert manager.managed_pid == 12345
        mock_exec.assert_called_once()


@pytest.mark.asyncio
async def test_wait_until_ready(manager):
    """Test 3: Manager polls API readiness until ready."""
    with patch.object(manager, "check_api_responding", side_effect=[False, False, True]):
        ready = await manager.wait_until_ready(timeout=5.0)
        assert ready is True


@pytest.mark.asyncio
async def test_start_timeout_cleans_up_process(manager):
    """Test 4: If API does not become ready in timeout, process is cleaned up and error returned."""
    mock_process = MagicMock()
    mock_process.pid = 9999
    mock_process.returncode = None

    with patch.object(manager, "check_api_responding", return_value=False), \
         patch("app.ai.services.ollama_process_manager.find_ollama_executable", return_value="C:\\Ollama\\ollama.exe"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):

        res = await manager.start(timeout=0.2)

        assert res["success"] is False
        assert res["code"] == "ollama_startup_timeout"
        assert res["state"] == "failed"
        assert manager.managed_pid is None
        mock_process.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_stop_managed_process(manager):
    """Test 5: stop() terminates managed process."""
    mock_process = MagicMock()
    mock_process.pid = 8888
    mock_process.returncode = None

    manager._process = mock_process
    manager._managed_pid = 8888
    manager.state = "running_managed"

    res = await manager.stop()

    assert res["success"] is True
    assert res["state"] == "stopped"
    assert manager.managed_pid is None
    mock_process.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_stop_unmanaged_external_process_safely(manager):
    """Test 6: stop() when managed_pid == None DOES NOT kill external process."""
    with patch.object(manager, "check_api_responding", return_value=True):
        res = await manager.stop()

        assert res["success"] is False
        assert res["code"] == "not_managed"
        assert res["state"] == "running_external"
        assert manager.managed_pid is None


@pytest.mark.asyncio
async def test_restart_managed_process(manager):
    """Test 7: restart() on managed process stops then starts process."""
    with patch.object(manager, "stop", new_callable=AsyncMock, return_value={"success": True}) as mock_stop, \
         patch.object(manager, "start", new_callable=AsyncMock, return_value={"success": True, "state": "running_managed"}) as mock_start:

        manager._managed_pid = 5555
        with patch.object(manager, "check_api_responding", return_value=True):
            res = await manager.restart()

            assert res["success"] is True
            mock_stop.assert_called_once()
            mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_restart_unmanaged_external_process(manager):
    """Test 8: restart() on external process does NOT kill process and returns not_managed."""
    with patch.object(manager, "check_api_responding", return_value=True):
        res = await manager.restart()

        assert res["success"] is False
        assert res["code"] == "not_managed"
        assert res["state"] == "running_external"


@pytest.mark.asyncio
async def test_ollama_models_environment_passed(manager, monkeypatch):
    """Test 9: OLLAMA_MODELS is passed to the spawned process environment."""
    monkeypatch.setenv("OLLAMA_MODELS", "F:\\AI-Models\\.ollama\\models")

    mock_process = MagicMock()
    mock_process.pid = 7777
    mock_process.returncode = None

    captured_env = {}

    async def mock_exec(*args, **kwargs):
        nonlocal captured_env
        captured_env = kwargs.get("env", {})
        return mock_process

    with patch.object(manager, "check_api_responding", side_effect=[False, True]), \
         patch("app.ai.services.ollama_process_manager.find_ollama_executable", return_value="C:\\Ollama\\ollama.exe"), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_exec):

        res = await manager.start()

        assert res["success"] is True
        assert captured_env.get("OLLAMA_MODELS") == "F:\\AI-Models\\.ollama\\models"


@pytest.mark.asyncio
async def test_executable_not_found(manager):
    """Test 10: Missing executable returns controlled error ollama_not_installed."""
    with patch.object(manager, "check_api_responding", return_value=False), \
         patch("app.ai.services.ollama_process_manager.find_ollama_executable", return_value=None):

        res = await manager.start()

        assert res["success"] is False
        assert res["code"] == "ollama_not_installed"
        assert res["state"] == "failed"
        assert manager.managed_pid is None
