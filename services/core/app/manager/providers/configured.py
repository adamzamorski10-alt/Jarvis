"""
Configured AI provider adapter for the provider-neutral PlanManager.

It reuses the existing provider-neutral planning prompt, tool contract and
output parser. No execution authority is granted to the model.
"""
from __future__ import annotations

from app.ai.models import AIResponse
from app.ai.normalization import normalize_tool_definitions
from app.ai.providers.base import AIProvider
from app.manager.models import PlanningConstraints, ProviderTaskProposal, UserPlan
from app.manager.output_parser import PlanningOutputParser
from app.manager.providers.base import (
    PlanProvider,
    PlanProviderError,
    PlanProviderMalformedResponseError,
)
from app.manager.prompt_builder import build_messages, build_planning_tool_definition


class ConfiguredPlanProvider(PlanProvider):
    """Adapt the configured AI Operator provider to PlanManager."""

    def __init__(self, provider: AIProvider, model: str) -> None:
        self._provider = provider
        self._model = model

    async def generate_plan(
        self,
        user_plan: UserPlan,
        constraints: PlanningConstraints,
    ) -> list[ProviderTaskProposal]:
        messages = build_messages(user_plan, constraints)
        tool_definition = build_planning_tool_definition()
        try:
            response: AIResponse = await self._provider.chat(
                self._model,
                messages,
                tools=normalize_tool_definitions([tool_definition]),
            )
        except Exception as exc:
            raise PlanProviderError(f"Planning provider failed: {exc}") from exc

        try:
            return PlanningOutputParser.parse(response)
        except PlanProviderMalformedResponseError:
            raise
        except Exception as exc:
            raise PlanProviderMalformedResponseError(
                f"Planning response could not be parsed: {exc}"
            ) from exc


__all__ = ["ConfiguredPlanProvider"]
