"""High-level AI assistant service."""
import httpx

from app.ai.providers.kimi import KimiProviderError
from app.ai.providers.openrouter import OpenRouterProviderError
from app.ai.services.conversation_service import ConversationService
from app.ai.services.provider_manager import ProviderManager
from app.core.logging import get_logger
from app.services.activity_service import ActivityService

logger = get_logger(__name__)

READY_PROVIDER_STATES = {"online", "ready", "no_models"}


class AIUnavailable(ValueError):
    def __init__(
        self,
        message: str,
        code: str = "ai_not_ready",
        provider: str | None = None,
        retryable: bool = False,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = code  # backward-compat
        self.provider = provider
        self.retryable = retryable
        self.details = details or {}


def _provider_url(config: dict) -> str | None:
    if config["provider"] == "ollama":
        return config.get("ollama_url")
    if config["provider"] == "openrouter":
        return config.get("openrouter_base_url")
    if config["provider"] == "kimi":
        return config.get("kimi_base_url")
    return None


def _safe_error_detail(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, (KimiProviderError, OpenRouterProviderError)):
        return exc.status, getattr(exc, "retryable", False)
    if isinstance(exc, httpx.TimeoutException):
        return "provider_timeout", True
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return "provider_unreachable", True
    return exc.__class__.__name__, False


class AIService:
    def __init__(
        self,
        providers: ProviderManager,
        conversations: ConversationService,
        activity: ActivityService,
    ) -> None:
        self._providers = providers
        self._conversations = conversations
        self._activity = activity

    async def get_status(self) -> dict:
        config = await self._providers.get_config()
        provider = await self._providers.get_provider()
        health = await provider.health_check()
        models = []
        selected_model = config["selected_model"]
        model_status = "not_selected"

        if health.status in READY_PROVIDER_STATES:
            try:
                models = await provider.list_models()
                names = {model.name for model in models}
                if not models:
                    model_status = "no_models"
                elif selected_model and selected_model in names:
                    model_status = "ready"
                elif selected_model:
                    model_status = "missing"
                else:
                    model_status = "not_selected"
            except Exception as exc:
                health = type(health)(
                    status=getattr(exc, "status", "unreachable"),
                    configured=True,
                    message=str(exc),
                    version=health.version,
                )

        return {
            "provider": config["provider"],
            "provider_status": health.status,
            "configured": health.configured,
            "provider_url": _provider_url(config),
            "provider_version": health.version,
            "message": health.message,
            "model": selected_model,
            "model_status": model_status,
            "models_count": len(models),
            "ai_ready": health.status in READY_PROVIDER_STATES and model_status == "ready",
            "installed": getattr(health, "installed", None),
            "service_running": getattr(health, "service_running", None),
            "api_responding": getattr(health, "api_responding", None),
            "models_available": getattr(health, "models_available", None),
            "executable_path": getattr(health, "executable_path", None),
        }

    async def list_models(self) -> list:
        config = await self._providers.get_config()
        provider = await self._providers.get_provider()
        health = await provider.health_check()
        if health.status not in READY_PROVIDER_STATES:
            await self._activity.create_event(
                f"{str(config['provider']).title()} connection failed",
                category="ai",
                level="WARNING",
                details={"provider": config["provider"], "status": health.status},
            )
            code = (
                "ollama_not_installed" if health.status == "not_installed"
                else "ollama_service_stopped" if health.status == "stopped"
                else health.status
            )
            retryable = health.status == "stopped" or health.status == "unreachable"
            raise AIUnavailable(
                health.message or "AI provider is not reachable",
                code=code,
                provider=str(config["provider"]),
                retryable=retryable,
            )
        return await provider.list_models()

    async def chat(
        self,
        conversation_id: str | None,
        message: str,
        client_message_id: str | None = None,
    ) -> dict:
        config = await self._providers.get_config()
        provider_name = str(config["provider"])
        selected_model = config.get("selected_model")

        if not selected_model:
            raise AIUnavailable(
                "No AI model selected. Please select a model in Settings.",
                code="ai_not_ready",
                provider=provider_name,
                retryable=False,
            )

        provider = await self._providers.get_provider()

        # Validate provider-specific configuration & status
        if provider_name == "kimi" and not config.get("kimi_api_key_configured"):
            raise AIUnavailable("Kimi API key is not configured", code="provider_not_configured", provider="kimi", retryable=False)
        if provider_name == "openrouter" and not config.get("openrouter_api_key_configured"):
            raise AIUnavailable("OpenRouter API key is not configured", code="provider_not_configured", provider="openrouter", retryable=False)

        if provider_name == "ollama":
            health = await provider.health_check()
            if health.status == "not_installed":
                raise AIUnavailable(
                    health.message or "Ollama is not installed on this computer.",
                    code="ollama_not_installed",
                    provider="ollama",
                    retryable=False,
                )
            if health.status == "stopped":
                raise AIUnavailable(
                    health.message or "Ollama service is not running on 127.0.0.1:11434.",
                    code="ollama_service_stopped",
                    provider="ollama",
                    retryable=True,
                )
            if health.status == "no_models":
                raise AIUnavailable(
                    health.message or "No AI models are installed in Ollama.",
                    code="ollama_no_models",
                    provider="ollama",
                    retryable=False,
                )
            if health.status in ("online", "ready"):
                models = await provider.list_models()
                model_names = {m.name for m in models}
                model_found = (
                    selected_model in model_names
                    or f"{selected_model}:latest" in model_names
                    or any(m.name.startswith(f"{selected_model}:") for m in models)
                )
                if not model_found:
                    raise AIUnavailable(
                        f"Selected model '{selected_model}' is not installed in Ollama. Run 'ollama pull {selected_model}' to download it.",
                        code="ollama_model_missing",
                        provider="ollama",
                        retryable=False,
                    )

        # Get or create conversation
        if conversation_id:
            conversation = await self._conversations.get(conversation_id)
            await self._conversations.add_message(conversation, "user", message, client_message_id=client_message_id)
            conversation = await self._conversations.get(conversation.id)
        else:
            conversation = await self._conversations.create(provider_name, str(selected_model))
            await self._activity.create_event("AI conversation created", category="ai", details={"provider": provider_name, "model": selected_model})
            await self._conversations.add_message(conversation, "user", message, client_message_id=client_message_id)
            conversation = await self._conversations.get(conversation.id)

        provider_messages = self._conversations.build_context_messages(conversation)
        await self._conversations._session.commit()

        logger.info("AI chat request started", provider=provider_name, model=selected_model, conversation_id=conversation.id)

        try:
            answer = await provider.chat(str(selected_model), provider_messages)
        except httpx.TimeoutException as exc:
            logger.warning("AI chat request timed out", provider=provider_name, model=selected_model)
            await self._activity.create_event(
                "AI request timed out",
                category="ai",
                level="ERROR",
                details={"provider": provider_name, "reason": "timeout"},
            )
            raise AIUnavailable("AI response timed out", code="provider_timeout", provider=provider_name, retryable=True) from exc
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            logger.warning("AI chat network error", provider=provider_name, model=selected_model)
            await self._activity.create_event(
                "AI connection failed",
                category="ai",
                level="ERROR",
                details={"provider": provider_name, "reason": "network_error"},
            )
            if provider_name == "ollama":
                from app.ai.providers.ollama import find_ollama_executable
                exe_found = find_ollama_executable() is not None
                code = "ollama_service_stopped" if exe_found else "ollama_not_installed"
                msg = "Ollama service is not running on 127.0.0.1:11434." if exe_found else "Ollama executable is not installed."
                raise AIUnavailable(msg, code=code, provider="ollama", retryable=exe_found) from exc
            raise AIUnavailable(f"Could not reach {provider_name.title()} service (network error)", code="provider_unreachable", provider=provider_name, retryable=True) from exc
        except (OpenRouterProviderError, KimiProviderError) as exc:
            logger.warning("AI provider error", provider=provider_name, model=selected_model, status=exc.status)
            await self._activity.create_event(
                "AI request failed",
                category="ai",
                level="ERROR",
                details={"provider": provider_name, "reason": exc.status},
            )
            raise AIUnavailable(str(exc), code=exc.status, provider=provider_name, retryable=getattr(exc, "retryable", False)) from exc
        except Exception as exc:
            reason, retryable = _safe_error_detail(exc)
            logger.error("Unexpected AI error", provider=provider_name, model=selected_model, reason=reason)
            await self._activity.create_event(
                "AI request failed",
                category="ai",
                level="ERROR",
                details={"provider": provider_name, "reason": reason},
            )
            raise AIUnavailable(str(exc), code=reason, provider=provider_name, retryable=retryable) from exc

        assistant_message = await self._conversations.add_message(conversation, "assistant", answer)
        logger.info("AI chat request completed", provider=provider_name, model=selected_model, conversation_id=conversation.id)
        await self._activity.create_event(
            "AI request completed",
            category="ai",
            details={"provider": provider_name, "model": selected_model},
        )
        return {"conversation": conversation, "message": assistant_message}

    async def pull_model(self, model_name: str) -> dict:
        config = await self._providers.get_config()
        if config["provider"] != "ollama":
            raise AIUnavailable(
                "Model downloading is only supported for Ollama.",
                code="provider_not_supported",
                provider=str(config["provider"]),
                retryable=False,
            )

        provider = await self._providers.get_provider()
        health = await provider.health_check()

        if health.status in ("not_installed", "stopped"):
            code = "ollama_not_installed" if health.status == "not_installed" else "ollama_service_stopped"
            raise AIUnavailable(
                health.message or "Ollama service is not available.",
                code=code,
                provider="ollama",
                retryable=health.status == "stopped",
            )

        # Storage & disk space check
        storage_info = await provider.get_storage_info()
        required_bytes = MODEL_ESTIMATED_BYTES.get(model_name)
        if not required_bytes:
            for k, v in MODEL_ESTIMATED_BYTES.items():
                if model_name.startswith(k) or k.startswith(model_name):
                    required_bytes = v
                    break
        if not required_bytes:
            required_bytes = DEFAULT_ESTIMATED_BYTES

        available_bytes = storage_info.get("available_bytes", 0)
        if available_bytes > 0 and available_bytes < required_bytes:
            raise AIUnavailable(
                f"Not enough free disk space to download {model_name}.",
                code="insufficient_disk_space",
                provider="ollama",
                retryable=False,
                details={
                    "required_bytes": required_bytes,
                    "available_bytes": available_bytes,
                    "drive": storage_info.get("drive"),
                    "location": storage_info.get("location"),
                },
            )

        from app.ai.providers.ollama import invalidate_storage_cache
        from app.ai.services.ollama_download_manager import download_manager

        result = download_manager.start_pull(
            config["ollama_url"], model_name, on_complete=invalidate_storage_cache
        )
        await self._activity.create_event(
            f"Started downloading Ollama model {model_name}",
            category="ai",
            details={"model": model_name},
        )
        return result

    async def get_pull_status(self) -> dict:
        from app.ai.services.ollama_download_manager import download_manager
        return download_manager.get_status()

    async def cancel_pull(self) -> dict:
        from app.ai.services.ollama_download_manager import download_manager
        result = download_manager.cancel_pull()
        await self._activity.create_event("Cancelled model download", category="ai")
        return result

    async def delete_model(self, model_name: str) -> dict:
        config = await self._providers.get_config()
        if config["provider"] != "ollama":
            raise AIUnavailable(
                "Model deletion is only supported for Ollama.",
                code="provider_not_supported",
                provider=str(config["provider"]),
                retryable=False,
            )

        provider = await self._providers.get_provider()
        health = await provider.health_check()
        if health.status in ("not_installed", "stopped"):
            code = "ollama_not_installed" if health.status == "not_installed" else "ollama_service_stopped"
            raise AIUnavailable(
                health.message or "Ollama service is not available.",
                code=code,
                provider="ollama",
                retryable=health.status == "stopped",
            )

        try:
            await provider.delete_model(model_name)
        except Exception as exc:
            raise AIUnavailable(
                f"Failed to delete model {model_name}: {str(exc)}",
                code="model_delete_failed",
                provider="ollama",
                retryable=True,
            ) from exc

        selected_model = config.get("selected_model")
        if selected_model and (selected_model == model_name or selected_model.startswith(f"{model_name}:")):
            await self._providers.update_config(str(config["provider"]), config.get("ollama_url"), selected_model=None)

        await self._activity.create_event(
            f"Deleted Ollama model {model_name}",
            category="ai",
            details={"model": model_name},
        )
        return {"success": True, "model": model_name}

    async def get_storage_info(self) -> dict:
        config = await self._providers.get_config()
        if config["provider"] != "ollama":
            return {"location": None, "drive": None, "available_bytes": None, "used_bytes": None}

        provider = await self._providers.get_provider()
        if hasattr(provider, "get_storage_info"):
            return await provider.get_storage_info()
        return {"location": None, "drive": None, "available_bytes": None, "used_bytes": None}

    async def start_ollama_process(self) -> dict:
        config = await self._providers.get_config()
        base_url = config.get("ollama_url") or "http://127.0.0.1:11434"
        from app.ai.services.ollama_process_manager import process_manager
        res = await process_manager.start(base_url=base_url)
        if not res.get("success"):
            code = res.get("code", "ollama_start_failed")
            raise AIUnavailable(
                res.get("message", "Failed to start Ollama service."),
                code=code,
                provider="ollama",
                retryable=False,
            )
        await self._activity.create_event(
            f"Ollama process start operation: {res.get('state')}",
            category="ai",
            details=res,
        )
        return res

    async def stop_ollama_process(self) -> dict:
        from app.ai.services.ollama_process_manager import process_manager
        res = await process_manager.stop()
        if not res.get("success"):
            code = res.get("code", "not_managed")
            raise AIUnavailable(
                res.get("message", "Cannot stop Ollama service."),
                code=code,
                provider="ollama",
                retryable=False,
            )
        await self._activity.create_event(
            "Stopped managed Ollama process",
            category="ai",
            details=res,
        )
        return res

    async def restart_ollama_process(self) -> dict:
        config = await self._providers.get_config()
        base_url = config.get("ollama_url") or "http://127.0.0.1:11434"
        from app.ai.services.ollama_process_manager import process_manager
        res = await process_manager.restart(base_url=base_url)
        if not res.get("success"):
            code = res.get("code", "ollama_start_failed")
            raise AIUnavailable(
                res.get("message", "Failed to restart Ollama service."),
                code=code,
                provider="ollama",
                retryable=False,
            )
        await self._activity.create_event(
            "Restarted managed Ollama process",
            category="ai",
            details=res,
        )
        return res

    async def get_ollama_process_status(self) -> dict:
        config = await self._providers.get_config()
        base_url = config.get("ollama_url") or "http://127.0.0.1:11434"
        from app.ai.services.ollama_process_manager import process_manager
        from app.ai.providers.ollama import find_ollama_executable, get_ollama_models_path

        state_info = await process_manager.get_state_info(base_url=base_url)
        exe_path = find_ollama_executable()
        storage_path = get_ollama_models_path()

        return {
            "state": state_info["state"],
            "running": state_info["running"],
            "managed": state_info["managed"],
            "managed_pid": state_info["managed_pid"],
            "executable_path": exe_path,
            "base_url": base_url,
            "storage_location": str(storage_path),
        }



MODEL_ESTIMATED_BYTES: dict[str, int] = {
    "qwen2.5-coder:7b": 4_700_000_000,
    "qwen3:8b": 5_200_000_000,
    "llama3.2:3b": 2_000_000_000,
    "mistral:7b": 4_100_000_000,
    "nomic-embed-text:latest": 300_000_000,
    "llama3.1:8b": 4_700_000_000,
    "qwen2.5-coder:1.5b-base": 1_000_000_000,
}
DEFAULT_ESTIMATED_BYTES = 5_000_000_000


