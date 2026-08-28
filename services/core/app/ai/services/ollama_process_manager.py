"""
Asynchronous process manager for Ollama service (ollama serve).
Provides safe lifecycle control (start, stop, restart, readiness check)
and strictly distinguishes managed processes (spawned by AI Operator)
from external user processes.
"""
import asyncio
import os
import subprocess
import sys
import httpx

from app.ai.providers.ollama import find_ollama_executable
from app.core.logging import get_logger

logger = get_logger(__name__)


class OllamaProcessManager:
    def __init__(self) -> None:
        self.state: str = "stopped"
        self._process: subprocess.Popen | asyncio.subprocess.Process | None = None
        self._managed_pid: int | None = None

    @property
    def managed_pid(self) -> int | None:
        return self._managed_pid

    async def check_api_responding(self, base_url: str = "http://127.0.0.1:11434", timeout: float = 2.0) -> bool:
        """Pings Ollama version endpoint to check if HTTP API is responsive."""
        url = f"{base_url.rstrip('/')}/api/version"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.get(url)
                return res.status_code == 200
        except Exception:
            return False

    async def is_running(self, base_url: str = "http://127.0.0.1:11434") -> bool:
        """Returns True if Ollama HTTP API is responding."""
        return await self.check_api_responding(base_url)

    async def get_state_info(self, base_url: str = "http://127.0.0.1:11434") -> dict:
        """
        Returns full diagnostic state dictionary:
        - state: 'stopped' | 'starting' | 'running_managed' | 'running_external' | 'failed'
        - managed: bool
        - managed_pid: int | None
        - running: bool
        """
        api_up = await self.check_api_responding(base_url)

        if api_up:
            if self._is_child_alive():
                self.state = "running_managed"
                return {
                    "state": "running_managed",
                    "running": True,
                    "managed": True,
                    "managed_pid": self._managed_pid,
                }
            else:
                self.state = "running_external"
                return {
                    "state": "running_external",
                    "running": True,
                    "managed": False,
                    "managed_pid": None,
                }
        else:
            if self.state != "failed":
                self.state = "stopped"
            return {
                "state": self.state,
                "running": False,
                "managed": False,
                "managed_pid": None,
            }

    def _is_child_alive(self) -> bool:
        """Checks if internal child process handles are set and process is active."""
        if self._process is None or self._managed_pid is None:
            return False
        returncode = getattr(self._process, "returncode", None)
        if isinstance(returncode, int):
            return False
        poll_fn = getattr(self._process, "poll", None)
        if callable(poll_fn):
            try:
                res = poll_fn()
                if isinstance(res, int):
                    return False
            except Exception:
                pass
        return True

    async def wait_until_ready(self, base_url: str = "http://127.0.0.1:11434", timeout: float = 15.0) -> bool:
        """Polls API readiness every 0.5 seconds until ready or timeout expires."""
        start_time = asyncio.get_running_loop().time()
        while (asyncio.get_running_loop().time() - start_time) < timeout:
            if not self._is_child_alive() and self._process is not None:
                logger.warning("Spawned Ollama process exited prematurely during startup wait")
                return False
            if await self.check_api_responding(base_url, timeout=1.0):
                return True
            await asyncio.sleep(0.5)
        return False

    async def start(self, base_url: str = "http://127.0.0.1:11434", timeout: float = 15.0) -> dict:
        """
        Safely starts 'ollama serve'.
        - If Ollama is already running (external or managed), DOES NOT spawn a second process.
        - Inherits environment including OLLAMA_MODELS.
        - Waits up to timeout seconds for API readiness.
        """
        if await self.check_api_responding(base_url):
            if self._is_child_alive():
                self.state = "running_managed"
                return {
                    "success": True,
                    "already_running": True,
                    "state": "running_managed",
                    "managed": True,
                    "managed_pid": self._managed_pid,
                    "message": f"Ollama service is already running (managed, PID {self._managed_pid}).",
                }
            else:
                self.state = "running_external"
                return {
                    "success": True,
                    "already_running": True,
                    "state": "running_external",
                    "managed": False,
                    "managed_pid": None,
                    "message": "Ollama service is already running (external process).",
                }

        exe_path = find_ollama_executable()
        if not exe_path:
            self.state = "failed"
            return {
                "success": False,
                "code": "ollama_not_installed",
                "message": "Ollama executable was not found on this computer.",
                "state": "failed",
                "managed": False,
                "managed_pid": None,
            }

        self.state = "starting"
        env = dict(os.environ)
        logger.info("Spawning Ollama service process", executable=exe_path, base_url=base_url)

        try:
            process = await asyncio.create_subprocess_exec(
                exe_path,
                "serve",
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._process = process
            self._managed_pid = process.pid
        except Exception as exc:
            self.state = "failed"
            self._process = None
            self._managed_pid = None
            logger.error("Failed to spawn Ollama process", error=str(exc))
            return {
                "success": False,
                "code": "ollama_start_failed",
                "message": f"Failed to spawn Ollama process: {str(exc)}",
                "state": "failed",
                "managed": False,
                "managed_pid": None,
            }

        ready = await self.wait_until_ready(base_url=base_url, timeout=timeout)
        if ready:
            self.state = "running_managed"
            logger.info("Ollama service started and ready", pid=self._managed_pid)
            return {
                "success": True,
                "already_running": False,
                "state": "running_managed",
                "managed": True,
                "managed_pid": self._managed_pid,
                "message": f"Ollama service started successfully (PID {self._managed_pid}).",
            }
        else:
            logger.error("Ollama service failed to become ready within timeout. Cleaning up.", pid=self._managed_pid)
            await self._cleanup_failed_process()
            self.state = "failed"
            return {
                "success": False,
                "code": "ollama_startup_timeout",
                "message": f"Ollama service started (PID {self._managed_pid}), but API did not become ready within {timeout}s.",
                "state": "failed",
                "managed": False,
                "managed_pid": None,
            }

    async def _cleanup_failed_process(self) -> None:
        if self._process is not None:
            try:
                term_fn = getattr(self._process, "terminate", None)
                if callable(term_fn):
                    term_res = term_fn()
                    if asyncio.iscoroutine(term_res):
                        await term_res
            except Exception:
                try:
                    kill_fn = getattr(self._process, "kill", None)
                    if callable(kill_fn):
                        kill_res = kill_fn()
                        if asyncio.iscoroutine(kill_res):
                            await kill_res
                except Exception:
                    pass
        self._process = None
        self._managed_pid = None

    async def stop(self) -> dict:
        """
        Safely stops Ollama process ONLY if it is managed by AI Operator.
        If managed_pid is None, WILL NOT kill external processes and returns code 'not_managed'.
        """
        if self._managed_pid is None or self._process is None:
            api_up = await self.check_api_responding()
            current_state = "running_external" if api_up else "stopped"
            logger.warning("Attempted to stop Ollama, but process is not managed by AI Operator", current_state=current_state)
            return {
                "success": False,
                "code": "not_managed",
                "message": "Cannot stop Ollama service: process was not started by AI Operator.",
                "state": current_state,
                "managed": False,
                "managed_pid": None,
            }

        pid_to_stop = self._managed_pid
        logger.info("Stopping managed Ollama process", pid=pid_to_stop)

        try:
            term_fn = getattr(self._process, "terminate", None)
            if callable(term_fn):
                res = term_fn()
                if asyncio.iscoroutine(res):
                    await res
            wait_fn = getattr(self._process, "wait", None)
            if callable(wait_fn):
                try:
                    wait_res = wait_fn()
                    if asyncio.iscoroutine(wait_res):
                        await asyncio.wait_for(wait_res, timeout=3.0)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Error stopping Ollama process", pid=pid_to_stop, error=str(exc))

        self._process = None
        self._managed_pid = None
        self.state = "stopped"

        return {
            "success": True,
            "message": f"Managed Ollama service (PID {pid_to_stop}) stopped successfully.",
            "state": "stopped",
            "managed": False,
            "managed_pid": None,
        }

    async def restart(self, base_url: str = "http://127.0.0.1:11434", timeout: float = 15.0) -> dict:
        """
        Restarts managed Ollama process.
        If running external (managed_pid is None), DOES NOT kill external process and returns 'not_managed'.
        """
        api_up = await self.check_api_responding(base_url)
        if api_up and self._managed_pid is None:
            return {
                "success": False,
                "code": "not_managed",
                "message": "Cannot restart Ollama service: current running instance was not started by AI Operator.",
                "state": "running_external",
                "managed": False,
                "managed_pid": None,
            }

        if self._managed_pid is not None:
            await self.stop()

        return await self.start(base_url=base_url, timeout=timeout)


# Module-level singleton
process_manager = OllamaProcessManager()
