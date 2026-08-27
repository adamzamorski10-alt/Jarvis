"""Asynchronous task manager for Ollama model pulls."""
import asyncio
from datetime import datetime, timezone
import json
import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


class OllamaDownloadManager:
    def __init__(self) -> None:
        self.active: bool = False
        self.model: str | None = None
        self.status: str = "idle"
        self.phase: str | None = None
        self.completed: int | None = None
        self.total: int | None = None
        self.percentage: float | None = None
        self.message: str | None = None
        self.error: dict | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self._cancel_event: asyncio.Event | None = None
        self._current_task: asyncio.Task | None = None

    def get_status(self) -> dict:
        if not self.active and self.status == "idle" and self.started_at is None:
            return {"active": False}

        return {
            "active": self.active,
            "model": self.model,
            "status": self.status,
            "phase": self.phase or self.status,
            "completed": self.completed,
            "total": self.total,
            "percentage": self.percentage,
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def start_pull(self, base_url: str, model_name: str, on_complete=None) -> dict:
        from app.ai.services.ai_service import AIUnavailable

        if self.active:
            raise AIUnavailable(
                f"Another model is currently downloading ({self.model}).",
                code="model_download_in_progress",
                provider="ollama",
                retryable=False,
            )

        self.active = True
        self.model = model_name
        self.status = "queued"
        self.phase = "queued"
        self.completed = 0
        self.total = None
        self.percentage = 0.0
        self.message = f"Queued download for {model_name}..."
        self.error = None
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at = None
        self._cancel_event = asyncio.Event()

        self._current_task = asyncio.create_task(
            self._run_pull(base_url, model_name, on_complete)
        )
        return self.get_status()

    async def _run_pull(self, base_url: str, model_name: str, on_complete=None) -> None:
        url = f"{base_url.rstrip('/')}/api/pull"
        logger.info("Starting Ollama model pull stream", model=model_name, url=url)

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", url, json={"name": model_name, "stream": True}) as response:
                    if response.status_code != 200:
                        body_text = await response.aread()
                        err_msg = f"Ollama returned HTTP {response.status_code}: {body_text.decode('utf-8', errors='ignore')}"
                        self._set_error("model_download_failed", err_msg)
                        return

                    async for line in response.aiter_lines():
                        if self._cancel_event and self._cancel_event.is_set():
                            self._set_cancelled()
                            return

                        if not line or not line.strip():
                            continue

                        try:
                            data = json.loads(line)
                        except Exception:
                            continue

                        if "error" in data and data["error"]:
                            err_msg = str(data["error"])
                            self._set_error("model_download_failed", err_msg)
                            return

                        raw_status = str(data.get("status", "")).strip()
                        if not raw_status:
                            continue

                        self._process_status_update(raw_status, data)

                        if raw_status == "success":
                            self.active = False
                            self.status = "success"
                            self.phase = "success"
                            self.percentage = 100.0
                            self.message = f"Model {model_name} downloaded successfully."
                            self.finished_at = datetime.now(timezone.utc).isoformat()
                            logger.info("Ollama model pull completed successfully", model=model_name)
                            if on_complete:
                                try:
                                    res = on_complete()
                                    if asyncio.iscoroutine(res):
                                        await res
                                except Exception as exc:
                                    logger.warning("Error running pull completion callback", error=str(exc))
                            return

            if self.active and self.status not in ("success", "cancelled", "error"):
                self._set_error("model_download_failed", "Download stream closed unexpectedly before completion.")

        except httpx.ConnectError:
            self._set_error("ollama_service_stopped", "Ollama service disconnected during pull.")
        except Exception as exc:
            if self._cancel_event and self._cancel_event.is_set():
                self._set_cancelled()
            else:
                self._set_error("model_download_failed", f"Download failed: {str(exc)}")

    def _process_status_update(self, raw_status: str, data: dict) -> None:
        completed = data.get("completed")
        total = data.get("total")

        if isinstance(completed, int):
            self.completed = completed
        if isinstance(total, int) and total > 0:
            self.total = total

        if self.total and self.total > 0 and self.completed is not None:
            self.percentage = round((self.completed / self.total) * 100, 2)

        if raw_status.startswith("pulling manifest"):
            self.status = "pulling_manifest"
            self.phase = "pulling_manifest"
            self.message = "Pulling model manifest..."
        elif raw_status.startswith("downloading"):
            self.status = "downloading"
            self.phase = "downloading"
            digest = data.get("digest")
            if digest:
                self.message = f"Downloading layer {digest[:12]}..."
            else:
                self.message = "Downloading model layers..."
        elif raw_status.startswith("verifying"):
            self.status = "verifying"
            self.phase = "verifying"
            self.message = "Verifying layer checksums..."
        elif raw_status.startswith("writing manifest"):
            self.status = "writing_manifest"
            self.phase = "writing_manifest"
            self.message = "Writing model manifest..."
        else:
            self.status = "downloading"
            self.phase = "downloading"
            self.message = raw_status

    def _set_cancelled(self) -> None:
        self.active = False
        self.status = "cancelled"
        self.phase = "cancelled"
        self.message = "Download cancelled by user."
        self.finished_at = datetime.now(timezone.utc).isoformat()
        logger.info("Ollama model pull cancelled", model=self.model)

    def _set_error(self, code: str, message: str) -> None:
        self.active = False
        self.status = "error"
        self.phase = "error"
        self.message = message
        self.error = {
            "code": code,
            "message": message,
            "provider": "ollama",
            "retryable": True,
        }
        self.finished_at = datetime.now(timezone.utc).isoformat()
        logger.error("Ollama model pull failed", model=self.model, code=code, message=message)

    def cancel_pull(self) -> dict:
        if self.active:
            if self._cancel_event:
                self._cancel_event.set()
            self._set_cancelled()
        return self.get_status()


# Module-level singleton
download_manager = OllamaDownloadManager()
