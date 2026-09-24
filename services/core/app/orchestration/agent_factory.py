"""
Development Agent Factory — Composition Root for Coding Agents.

Resolves configured AI coding providers (OllamaCodingAdapter, OpenRouterCodingAdapter,
KiloDevelopmentAgent) and model parameters from ProviderManager to instantiate
provider-neutral BaseDevelopmentAgent instances for autonomous task execution.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.ai.providers.coding_base import CodingConstraints
from app.ai.services.provider_manager import AIConfigError
from app.orchestration.agent_interface import DevelopmentAgent
from app.orchestration.agents.base_agent import BaseDevelopmentAgent

if TYPE_CHECKING:
    from app.ai.services.provider_manager import ProviderManager
    from app.tools.tool_execution_service import ToolExecutionService

from app.tools.models import AutonomyMode

logger = logging.getLogger(__name__)

# Default model choices if none configured in settings
DEFAULT_OLLAMA_CODING_MODEL = "qwen2.5-coder:7b"
DEFAULT_OPENROUTER_CODING_MODEL = "deepseek/deepseek-coder"


class DevelopmentAgentFactory:
    """
    Factory for instantiating concrete DevelopmentAgent instances.

    Isolated at the Composition Root layer to maintain complete provider
    neutrality in AutonomousTaskOrchestrator and BaseDevelopmentAgent.
    """

    def __init__(
        self,
        provider_manager: ProviderManager,
        default_constraints: CodingConstraints | None = None,
        tool_execution_service: ToolExecutionService | None = None,
        autonomy_mode: AutonomyMode = AutonomyMode.AUTONOMOUS,
    ) -> None:
        self._provider_manager = provider_manager
        self._default_constraints = default_constraints
        self._tool_execution_service = tool_execution_service
        self._autonomy_mode = autonomy_mode

    async def create_agent(
        self,
        constraints: CodingConstraints | None = None,
    ) -> DevelopmentAgent:
        """
        Create a fresh DevelopmentAgent instance configured for the active provider.

        Args:
            constraints: Optional execution constraints override.

        Returns:
            Provider-neutral DevelopmentAgent instance.
        """
        config = await self._provider_manager.get_config()
        provider_type = config.get("provider")

        if provider_type == "kilo":
            selected_model = config.get("selected_model")
            model = (
                selected_model.strip()
                if selected_model and isinstance(selected_model, str) and selected_model.strip()
                else None
            )
            effective_constraints = constraints or self._default_constraints

            from app.orchestration.agents.kilo_agent import KiloDevelopmentAgent

            kilo_executable = config.get("kilo_executable_path") or ""
            if not kilo_executable:
                raise AIConfigError(
                    "Kilo executable path is not configured. Set kilo_executable_path in settings."
                )

            kilo_auto_approve_raw = config.get("kilo_auto_approve") or "false"
            kilo_auto_approve = str(kilo_auto_approve_raw).lower() in ("true", "1", "yes")

            logger.info(
                "Creating KiloDevelopmentAgent with executable '%s' and model '%s'",
                kilo_executable,
                model or "(default)",
            )

            return KiloDevelopmentAgent(
                kilo_executable_path=kilo_executable,
                default_model=model,
                pure_mode=True,
                kill_timeout_seconds=15.0,
                auto_approve=kilo_auto_approve,
            )

        coding_provider = await self._provider_manager.get_coding_provider()

        selected_model = config.get("selected_model")

        if selected_model and isinstance(selected_model, str) and selected_model.strip():
            model = selected_model.strip()
        elif provider_type == "ollama":
            model = DEFAULT_OLLAMA_CODING_MODEL
        elif provider_type == "openrouter":
            model = DEFAULT_OPENROUTER_CODING_MODEL
        else:
            model = "default"

        effective_constraints = constraints or self._default_constraints

        logger.info(
            "Creating DevelopmentAgent with provider '%s' and model '%s'",
            coding_provider.name,
            model,
        )

        return BaseDevelopmentAgent(
            provider=coding_provider,
            model=model,
            constraints=effective_constraints,
            tool_execution_service=self._tool_execution_service,
            autonomy_mode=self._autonomy_mode,
        )
