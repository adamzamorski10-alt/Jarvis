"""Ollama AI provider integration with structured diagnostics."""
import asyncio
import os
import shutil
from pathlib import Path
import httpx

from app.ai.models import AIResponse
from app.ai.normalization import normalize_tool_calls, normalize_tool_definitions
from app.ai.providers.base import AIModel, AIProvider, ProviderHealth


def find_ollama_executable() -> str | None:
    """
    Safely locates the ollama executable on Windows or system PATH.
    Checks:
      1. System PATH via shutil.which
      2. Standard Windows LocalAppData install location
      3. Standard ProgramFiles location
    Does not require admin rights.
    """
    found = shutil.which("ollama") or shutil.which("ollama.exe")
    if found:
        return found

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        win_user_path = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
        if win_user_path.is_file():
            return str(win_user_path)

    program_files = os.environ.get("ProgramFiles")
    if program_files:
        win_sys_path = Path(program_files) / "Ollama" / "ollama.exe"
        if win_sys_path.is_file():
            return str(win_sys_path)

    return None


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def health_check(self) -> ProviderHealth:
        if not self.base_url:
            return ProviderHealth(
                status="not_configured",
                configured=False,
                message="Ollama URL is not configured",
                installed=None,
                service_running=False,
                api_responding=False,
                models_available=False,
            )

        exe_path = find_ollama_executable()
        is_installed = exe_path is not None

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                version_resp = await client.get(f"{self.base_url}/api/version")

            if version_resp.status_code != 200:
                return ProviderHealth(
                    status="unreachable",
                    configured=True,
                    message=f"HTTP {version_resp.status_code} from Ollama version endpoint",
                    installed=is_installed,
                    service_running=True,
                    api_responding=False,
                    models_available=False,
                    executable_path=exe_path,
                )

            version_data = version_resp.json()
            version = version_data.get("version") if isinstance(version_data, dict) else None

            # Inspect models list via /api/tags
            async with httpx.AsyncClient(timeout=5.0) as client:
                models_resp = await client.get(f"{self.base_url}/api/tags")

            if models_resp.status_code == 200:
                models_body = models_resp.json()
                models_list = models_body.get("models", []) if isinstance(models_body, dict) else []
                if isinstance(models_list, list) and len(models_list) > 0:
                    return ProviderHealth(
                        status="ready",
                        configured=True,
                        message=f"Ollama is ready with {len(models_list)} model(s)",
                        version=version,
                        installed=True,
                        service_running=True,
                        api_responding=True,
                        models_available=True,
                        executable_path=exe_path,
                    )
                else:
                    return ProviderHealth(
                        status="no_models",
                        configured=True,
                        message="Ollama service is running, but no models are installed. Run 'ollama pull <model>' to download one.",
                        version=version,
                        installed=True,
                        service_running=True,
                        api_responding=True,
                        models_available=False,
                        executable_path=exe_path,
                    )
            else:
                return ProviderHealth(
                    status="online",
                    configured=True,
                    message="Ollama API is responding, but models endpoint returned non-200 status",
                    version=version,
                    installed=True,
                    service_running=True,
                    api_responding=True,
                    models_available=False,
                    executable_path=exe_path,
                )

        except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException):
            if is_installed:
                return ProviderHealth(
                    status="stopped",
                    configured=True,
                    message=f"Ollama executable detected ({exe_path}), but service is not running on {self.base_url}.",
                    installed=True,
                    service_running=False,
                    api_responding=False,
                    models_available=False,
                    executable_path=exe_path,
                )
            else:
                return ProviderHealth(
                    status="not_installed",
                    configured=True,
                    message=f"Ollama executable was not found on this computer, and service on {self.base_url} is unreachable.",
                    installed=False,
                    service_running=False,
                    api_responding=False,
                    models_available=False,
                    executable_path=None,
                )
        except Exception as exc:
            return ProviderHealth(
                status="unreachable",
                configured=True,
                message=str(exc),
                installed=is_installed,
                service_running=False,
                api_responding=False,
                models_available=False,
                executable_path=exe_path,
            )

    async def list_models(self) -> list[AIModel]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.base_url}/api/tags")
        response.raise_for_status()
        body = response.json()
        models = body.get("models")
        if not isinstance(models, list):
            raise ValueError("Invalid Ollama models response")
        return [
            AIModel(
                name=str(item.get("name", "")),
                size=item.get("size") if isinstance(item.get("size"), int) else None,
                modified_at=item.get("modified_at") if isinstance(item.get("modified_at"), str) else None,
            )
            for item in models
            if item.get("name")
        ]

    async def chat(self, model: str, messages: list[dict[str, str]], tools: list[dict] | None = None) -> AIResponse:
        payload = {"model": model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = normalize_tool_definitions(tools)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
        response.raise_for_status()
        body = response.json()
        message = body.get("message")
        if not isinstance(message, dict):
            raise ValueError("Invalid Ollama chat response")
        content = message.get("content")
        if not isinstance(content, str) and content is not None:
            raise ValueError("Invalid Ollama chat content")
        tool_calls = None
        raw_tool_calls = message.get("tool_calls")
        if isinstance(raw_tool_calls, list) and raw_tool_calls:
            tool_calls = normalize_tool_calls(raw_tool_calls)
        finish_reason = "tool_calls" if tool_calls else (body.get("done_reason") or "stop")
        return AIResponse(content=content, tool_calls=tool_calls, finish_reason=finish_reason)

    async def delete_model(self, model: str) -> bool:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request("DELETE", f"{self.base_url}/api/delete", json={"name": model})
        if response.status_code not in (200, 204):
            raise ValueError(f"Ollama returned status code {response.status_code} while deleting model '{model}'")
        invalidate_storage_cache()
        return True

    async def get_storage_info(self) -> dict:
        storage_path = get_ollama_models_path()
        drive = get_ollama_storage_drive(storage_path)
        available_bytes = 0
        try:
            target_dir = storage_path if storage_path.exists() else storage_path.parent
            available_bytes = shutil.disk_usage(target_dir).free
        except Exception:
            pass

        used_bytes = get_ollama_used_bytes(storage_path)

        return {
            "location": str(storage_path),
            "drive": drive,
            "available_bytes": available_bytes,
            "used_bytes": used_bytes,
        }


def get_ollama_models_path() -> Path:
    env_path = os.environ.get("OLLAMA_MODELS")
    if env_path:
        return Path(env_path)
    return Path.home() / ".ollama" / "models"


def get_ollama_storage_drive(path: Path) -> str:
    if path.drive:
        return path.drive
    parts = path.parts
    if parts:
        return parts[0].rstrip("\\/")
    return "C:"


_used_space_cache: tuple[float, int] | None = None


def get_ollama_used_bytes(path: Path, force_refresh: bool = False) -> int:
    global _used_space_cache
    now = 0.0
    try:
        loop = asyncio.get_running_loop()
        now = loop.time()
    except RuntimeError:
        pass

    if not force_refresh and _used_space_cache is not None:
        cached_time, cached_bytes = _used_space_cache
        if now - cached_time < 30.0:
            return cached_bytes

    if not path.exists():
        return 0

    total_bytes = 0
    try:
        for p in path.glob("**/*"):
            if p.is_file():
                try:
                    total_bytes += p.stat().st_size
                except OSError:
                    pass
    except Exception:
        pass

    _used_space_cache = (now, total_bytes)
    return total_bytes


def invalidate_storage_cache() -> None:
    global _used_space_cache
    _used_space_cache = None

