"""High-level AI assistant service."""
import httpx

from app.ai.providers.kimi import KimiProviderError
from app.ai.services.conversation_service import ConversationService
from app.ai.services.provider_manager import ProviderManager
from app.services.activity_service import ActivityService


READY_PROVIDER_STATES = {"online", "ready"}


class AIUnavailable(ValueError):
    def __init__(self, message: str, status: str = "unavailable") -> None:
        super().__init__(message)
        self.status = status


def _provider_url(config: dict) -> str | None:
    if config["provider"] == "ollama":
        return config.get("ollama_url")
    if config["provider"] == "kimi":
        return config.get("kimi_base_url")
    return None


def _safe_error_detail(exc: Exception) -> str:
    if isinstance(exc, KimiProviderError):
        return exc.status
    return exc.__class__.__name__


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
            raise AIUnavailable(health.message or "AI provider is not reachable", status=health.status)
        return await provider.list_models()

    async def chat(self, conversation_id: str | None, message: str) -> dict:
        status = await self.get_status()
        if not status["ai_ready"]:
            reason = f"provider={status['provider_status']}, model={status['model_status']}"
            raise AIUnavailable(f"AI is not ready: {reason}", status=str(status["provider_status"]))

        config = await self._providers.get_config()
        provider = await self._providers.get_provider()
        if conversation_id:
            conversation = await self._conversations.get(conversation_id)
        else:
            conversation = await self._conversations.create(str(config["provider"]), str(config["selected_model"]))
            await self._activity.create_event("AI conversation created", category="ai")

        await self._conversations.add_message(conversation, "user", message)
        current = await self._conversations.get(conversation.id)
        provider_messages = self._conversations.build_context_messages(current)

        try:
            answer = await provider.chat(str(config["selected_model"]), provider_messages)
        except httpx.TimeoutException as exc:
            await self._activity.create_event(
                "AI request failed",
                category="ai",
                level="ERROR",
                details={"provider": config["provider"], "reason": "timeout"},
            )
            raise AIUnavailable("AI response timed out", status="timeout") from exc
        except Exception as exc:
            reason = _safe_error_detail(exc)
            await self._activity.create_event(
                "AI request failed",
                category="ai",
                level="ERROR",
                details={"provider": config["provider"], "reason": reason},
            )
            raise AIUnavailable(str(exc), status=reason) from exc

        assistant_message = await self._conversations.add_message(conversation, "assistant", answer)
        await self._activity.create_event(
            "AI request completed",
            category="ai",
            details={"provider": config["provider"], "model": config["selected_model"]},
        )
        return {"conversation": conversation, "message": assistant_message}
