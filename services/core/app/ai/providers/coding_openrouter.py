"""
OpenRouter Coding Adapter — Provider-Specific Code Generation Adapter.

Connects the provider-neutral coding protocol (CodingPromptBuilder,
CodingOutputParser) to low-level AIProvider (e.g. OpenRouterProvider).

SECURITY & BOUNDARIES:
- Does NOT access filesystem directly.
- Does NOT execute shell/subprocess commands.
- Does NOT perform Git, database, credential, or permission operations.
- Does NOT perform hidden retries or repair loops on malformed model output.
"""
from __future__ import annotations

from typing import Tuple

from app.ai.models import AIResponse
from app.ai.providers.base import AIProvider
from app.ai.providers.coding_base import (
    CodingConstraints,
    CodingOperation,
    CodingProvider,
    CodingStepResult,
)
from app.ai.providers.coding_parser import CodingOutputParser
from app.ai.providers.coding_prompt import CodingPromptBuilder


class OpenRouterCodingAdapter(CodingProvider):
    """
    OpenRouter adapter implementing provider-neutral CodingProvider.
    """

    name = "openrouter"

    def __init__(self, provider: AIProvider) -> None:
        if not isinstance(provider, AIProvider):
            raise ValueError("provider must be an instance of AIProvider")
        self._provider = provider

    async def plan_next_step(
        self,
        model: str,
        task_description: str,
        workspace_snapshot: dict[str, str],
        operation_history: Tuple[CodingOperation, ...],
        constraints: CodingConstraints | None = None,
    ) -> CodingStepResult:
        """
        Plan next structured coding step using OpenRouter AIProvider.

        Args:
            model: Model identifier string.
            task_description: Human-readable task description.
            workspace_snapshot: Map of workspace relative file paths to content.
            operation_history: Tuple of prior CodingOperations.
            constraints: Optional CodingConstraints.

        Returns:
            CodingStepResult containing at least one CodingOperation.

        Raises:
            ValueError: If input arguments are invalid.
            CodingParserEmptyResponseError: If OpenRouter returns no tool calls.
            CodingParserMalformedResponseError: If tool calls are malformed or fail schema.
            Exception: Low-level provider errors propagate uncaught.
        """
        if not model or not isinstance(model, str):
            raise ValueError("model must be a non-empty string")
        if not task_description or not isinstance(task_description, str):
            raise ValueError("task_description must be a non-empty string")
        if not isinstance(workspace_snapshot, dict):
            raise ValueError("workspace_snapshot must be a dict")
        if not isinstance(operation_history, tuple):
            raise ValueError("operation_history must be a tuple")

        messages = CodingPromptBuilder.build_messages(
            task_description=task_description,
            workspace_snapshot=workspace_snapshot,
            operation_history=operation_history,
            constraints=constraints,
        )
        tool_definition = CodingPromptBuilder.build_tool_definition()

        response: AIResponse = await self._provider.chat(
            model=model,
            messages=messages,
            tools=[tool_definition],
        )

        return CodingOutputParser.parse(response)
