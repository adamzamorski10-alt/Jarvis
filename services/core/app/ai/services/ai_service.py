"""High-level AI assistant service."""
import httpx

from app.ai.providers.kimi import KimiProviderError
from app.ai.providers.openrouter import OpenRouterProviderError
from app.ai.services.conversation_service import ConversationService
from app.ai.services.provider_manager import ProviderManager
from app.core.logging import get_logger
from app.services.activity_service import ActivityService

logger = get_logger(__name__)

READY_PROVIDER_STATES = {"online", "ready"}


class AIUnavailable(ValueError):
    def __init__(
        self,
        message: str,
        code: str = "ai_not_ready",
        provider: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = code  # backward-compat
        self.provider = provider
        self.retryable = retryable


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
            raise AIUnavailable(
                health.message or "AI provider is not reachable",
                code=health.status,
                provider=str(config["provider"]),
                retryable=True,
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

        # Validate provider-specific configuration
        if provider_name == "kimi" and not config.get("kimi_api_key_configured"):
            raise AIUnavailable("Kimi API key is not configured", code="provider_not_configured", provider="kimi", retryable=False)
        if provider_name == "openrouter" and not config.get("openrouter_api_key_configured"):
            raise AIUnavailable("OpenRouter API key is not configured", code="provider_not_configured", provider="openrouter", retryable=False)

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
