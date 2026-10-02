"""Unit & API tests for Ollama Process Manager REST endpoints (Stage 4.2)."""
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.identity.session import SessionStore
from app.identity.bootstrap import BootstrapSecret

client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth_client():
    session_store = SessionStore()
    bootstrap_secret = BootstrapSecret()
    app.state.session_store = session_store
    app.state.bootstrap_secret = bootstrap_secret

    loop = asyncio.new_event_loop()
    try:
        session = loop.run_until_complete(session_store.create(user_id="test_user"))
        client.headers["Authorization"] = f"Bearer {session.session_id}"
        yield
    finally:
        loop.close()
        app.state.session_store = None
        app.state.bootstrap_secret = None


def test_get_ollama_process_status():
    """Test 9: GET /api/ai/ollama/process returns current process status info."""
    with patch("app.ai.services.ollama_process_manager.process_manager.get_state_info", new_callable=AsyncMock) as mock_state, \
         patch("app.ai.providers.ollama.find_ollama_executable", return_value="C:\\Ollama\\ollama.exe"), \
         patch("app.ai.providers.ollama.get_ollama_models_path", return_value="F:\\AI-Models\\.ollama\\models"):

        mock_state.return_value = {
            "state": "running_external",
            "running": True,
            "managed": False,
            "managed_pid": None,
        }

        res = client.get("/api/ai/ollama/process")
        assert res.status_code == 200
        data = res.json()
        assert data["state"] == "running_external"
        assert data["running"] is True
        assert data["managed"] is False
        assert data["managed_pid"] is None
        assert data["executable_path"] == "C:\\Ollama\\ollama.exe"
        assert data["storage_location"] == "F:\\AI-Models\\.ollama\\models"


def test_start_ollama_when_running_external():
    """Test 1: POST /api/ai/ollama/start when running external -> returns 200 without spawning secondary process."""
    with patch("app.ai.services.ollama_process_manager.process_manager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = {
            "success": True,
            "already_running": True,
            "state": "running_external",
            "managed": False,
            "managed_pid": None,
            "message": "Ollama service is already running (external process).",
        }

        res = client.post("/api/ai/ollama/start")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["already_running"] is True
        assert data["managed"] is False
        assert data["managed_pid"] is None


def test_start_ollama_success():
    """Test 2: POST /api/ai/ollama/start when stopped -> spawns process, returns 200 with managed_pid."""
    with patch("app.ai.services.ollama_process_manager.process_manager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = {
            "success": True,
            "already_running": False,
            "state": "running_managed",
            "managed": True,
            "managed_pid": 11223,
            "message": "Ollama service started successfully (PID 11223).",
        }

        res = client.post("/api/ai/ollama/start")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["already_running"] is False
        assert data["managed"] is True
        assert data["managed_pid"] == 11223


def test_stop_managed_ollama():
    """Test 3: POST /api/ai/ollama/stop for managed process -> stops process, returns 200."""
    with patch("app.ai.services.ollama_process_manager.process_manager.stop", new_callable=AsyncMock) as mock_stop:
        mock_stop.return_value = {
            "success": True,
            "message": "Managed Ollama service (PID 11223) stopped successfully.",
            "state": "stopped",
            "managed": False,
            "managed_pid": None,
        }

        res = client.post("/api/ai/ollama/stop")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["state"] == "stopped"


def test_stop_unmanaged_external_ollama():
    """Test 4: POST /api/ai/ollama/stop for external process -> returns 409 Conflict, code not_managed."""
    with patch("app.ai.services.ollama_process_manager.process_manager.stop", new_callable=AsyncMock) as mock_stop:
        mock_stop.return_value = {
            "success": False,
            "code": "not_managed",
            "message": "Cannot stop Ollama service: process was not started by AI Operator.",
            "state": "running_external",
            "managed": False,
            "managed_pid": None,
        }

        res = client.post("/api/ai/ollama/stop")
        assert res.status_code == 409
        data = res.json()
        assert data["detail"]["error"]["code"] == "not_managed"
        assert "not started by AI Operator" in data["detail"]["message"]


def test_restart_managed_ollama():
    """Test 5: POST /api/ai/ollama/restart for managed process -> returns 200."""
    with patch("app.ai.services.ollama_process_manager.process_manager.restart", new_callable=AsyncMock) as mock_restart:
        mock_restart.return_value = {
            "success": True,
            "already_running": False,
            "state": "running_managed",
            "managed": True,
            "managed_pid": 99881,
            "message": "Ollama service started successfully (PID 99881).",
        }

        res = client.post("/api/ai/ollama/restart")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["managed_pid"] == 99881


def test_restart_unmanaged_external_ollama():
    """Test 6: POST /api/ai/ollama/restart for external process -> returns 409 Conflict, code not_managed."""
    with patch("app.ai.services.ollama_process_manager.process_manager.restart", new_callable=AsyncMock) as mock_restart:
        mock_restart.return_value = {
            "success": False,
            "code": "not_managed",
            "message": "Cannot restart Ollama service: current running instance was not started by AI Operator.",
            "state": "running_external",
            "managed": False,
            "managed_pid": None,
        }

        res = client.post("/api/ai/ollama/restart")
        assert res.status_code == 409
        data = res.json()
        assert data["detail"]["error"]["code"] == "not_managed"


def test_start_ollama_timeout_error():
    """Test 7: Startup timeout -> returns 409 Conflict with code ollama_startup_timeout."""
    with patch("app.ai.services.ollama_process_manager.process_manager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = {
            "success": False,
            "code": "ollama_startup_timeout",
            "message": "Ollama service started (PID 1234), but API did not become ready within 15s.",
            "state": "failed",
            "managed": False,
            "managed_pid": None,
        }

        res = client.post("/api/ai/ollama/start")
        assert res.status_code == 409
        data = res.json()
        assert data["detail"]["error"]["code"] == "ollama_startup_timeout"


def test_start_ollama_crash_error():
    """Test 8: Process crash / start failure -> returns 409 Conflict with code ollama_start_failed."""
    with patch("app.ai.services.ollama_process_manager.process_manager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = {
            "success": False,
            "code": "ollama_start_failed",
            "message": "Failed to spawn Ollama process: Permission denied.",
            "state": "failed",
            "managed": False,
            "managed_pid": None,
        }

        res = client.post("/api/ai/ollama/start")
        assert res.status_code == 409
        data = res.json()
        assert data["detail"]["error"]["code"] == "ollama_start_failed"
