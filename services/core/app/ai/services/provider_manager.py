"""Provider selection and AI configuration."""
from pydantic import AnyHttpUrl, ValidationError

from app.ai.providers.base import AIProvider
from app.ai.providers.future import FutureProvider
from app.ai.providers.kimi import KimiProvider
from app.ai.providers.ollama import OllamaProvider
from app.ai.services.credential_service import CredentialService, CredentialStoreError
from app.services.settings_service import SettingsService

SUPPORTED_PROVIDERS = ("ollama", "kimi", "omniroute")
DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"


class AIConfigError(ValueError):
    pass


class ProviderManager:
    def __init__(self, settings: SettingsService) -> None:
        self._settings = settings
        self._credentials = CredentialService()

    async def get_config(self) -> dict[str, str | bool | None]:
        all_settings = await self._settings.get_all()
        encrypted_key = all_settings.get("ai_kimi_api_key_encrypted") or ""
        return {
            "provider": all_settings.get("ai_provider") or "ollama",
            "ollama_url": all_settings.get("ai_ollama_url") or "http://127.0.0.1:11434",
            "kimi_base_url": all_settings.get("ai_kimi_base_url") or DEFAULT_KIMI_BASE_URL,
            "kimi_api_key_configured": bool(encrypted_key),
            "kimi_api_key_hint": self._safe_key_hint(encrypted_key),
            "selected_model": all_settings.get("ai_selected_model") or None,
        }

    async def update_config(
        self,
        provider: str,
        ollama_url: str | None,
        selected_model: str | None,
        kimi_base_url: str | None = None,
        kimi_api_key: str | None = None,
    ) -> dict[str, str | bool | None]:
        if provider not in SUPPORTED_PROVIDERS:
            raise AIConfigError(f"Unsupported provider: {provider}")
        if provider == "omniroute":
            raise AIConfigError("OmniRoute is prepared as a future provider but is not available yet")
        if provider == "ollama" and not ollama_url:
            raise AIConfigError("Ollama URL is required")
        if ollama_url:
            self._validate_url(ollama_url, "Ollama URL")
        kimi_url = kimi_base_url or DEFAULT_KIMI_BASE_URL
        self._validate_url(kimi_url, "Kimi base URL")

        updates: dict[str, str | None] = {
            "ai_provider": provider,
            "ai_ollama_url": (ollama_url or "http://127.0.0.1:11434").rstrip("/"),
            "ai_kimi_base_url": kimi_url.rstrip("/"),
            "ai_selected_model": selected_model or "",
        }
        if kimi_api_key is not None:
            try:
                updates["ai_kimi_api_key_encrypted"] = self._credentials.encrypt(kimi_api_key.strip()) if kimi_api_key.strip() else ""
            except CredentialStoreError as exc:
                raise AIConfigError("Could not securely store Kimi API key") from exc

        await self._settings.update(updates)
        return await self.get_config()

    async def get_provider(self) -> AIProvider:
        config = await self.get_config()
        provider = config["provider"]
        if provider == "ollama":
            return OllamaProvider(str(config["ollama_url"]))
        if provider == "kimi":
            return KimiProvider(str(config["kimi_base_url"]), await self.get_kimi_api_key())
        return FutureProvider(str(provider))

    async def list_supported_providers(self) -> list[dict[str, str | bool]]:
        return [
            {"id": "ollama", "label": "Ollama", "available": True},
            {"id": "kimi", "label": "Kimi", "available": True},
            {"id": "omniroute", "label": "OmniRoute", "available": False},
        ]

    async def get_kimi_api_key(self) -> str | None:
        encrypted = await self._settings.get_one("ai_kimi_api_key_encrypted")
        if not encrypted:
            return None
        try:
            return self._credentials.decrypt(encrypted)
        except CredentialStoreError as exc:
            raise AIConfigError("Could not read securely stored Kimi API key") from exc

    def _safe_key_hint(self, encrypted: str) -> str | None:
        if not encrypted:
            return None
        try:
            return self._credentials.hint(encrypted)
        except CredentialStoreError:
            return "****"

    def _validate_url(self, value: str, label: str) -> None:
        try:
            AnyHttpUrl(value)
        except ValidationError as exc:
            raise AIConfigError(f"{label} must be a valid HTTP URL") from exc
