"""
OpenRouter AI Manager Adapter — Provider-Specific Decision Adapter.

Connects the provider-neutral AI Manager protocol (ManagerPromptBuilder,
ManagerOutputParser) to low-level AIProvider (OpenRouterProvider).

SECURITY & BOUNDARIES:
- Does NOT execute actions.
- Does NOT access filesystem directly.
- Does NOT execute shell/subprocess commands.
- Does NOT perform Git, database, credential, or permission operations.
- Does NOT modify task/run/workspace/verification state.
- Does NOT perform hidden retries or repair loops on malformed model output
  beyond bounded transport retries for transient failures.
- Does NOT call ToolExecutionService, PermissionEngine, VerificationGate,
  AutonomousPlanExecutor, PlanExecutionService, or RunController.

RETRY:
  Bounded retry (max 3) for transient transport/provider errors only:
  - timeout
  - rate limit
  - unavailable/server error
  
  Never retries:
  - malformed semantic decisions
  - policy rejection
  - validation failures
  - authentication/authorization errors
"""
from __future__ import annotations

import asyncio
import logging
from typing import List

import httpx

from app.ai.models import AIResponse
from app.ai.normalization import normalize_tool_definitions
from app.ai.providers.base import AIProvider
from app.ai.providers.openrouter import OpenRouterProvider, OpenRouterProviderError
from app.ai_manager.errors import (
    AIManagerContextLimitError,
    AIManagerEmptyResponseError,
    AIManagerInvalidDecisionError,
    AIManagerMalformedDecisionError,
    AIManagerPolicyRejectionError,
    AIManagerProviderError,
    AIManagerRateLimitedError,
    AIManagerTimeoutError,
    AIManagerUnavailableError,
)
from app.ai_manager.models import UserIntent
from app.ai_manager.output_parser import ManagerOutputParser
from app.ai_manager.prompt_builder import ManagerPromptBuilder, build_manager_tool_definition
from app.ai_manager.providers.base import (
    AIManagerProvider,
    AIManagerProviderContextLimitError,
    AIManagerProviderEmptyResponseError,
    AIManagerProviderMalformedResponseError,
    AIManagerProviderRateLimitError,
    AIManagerProviderTimeoutError,
    AIManagerProviderUnavailableError,
)

logger = logging.getLogger(__name__)


class OpenRouterAIManagerAdapter(AIManagerProvider):
    """
    OpenRouter adapter implementing provider-neutral AIManagerProvider.

    Wraps an existing OpenRouterProvider for structured AI Manager decisions.
    """

    name = "openrouter"

    def __init__(self, provider: AIProvider, model: str = "openrouter/free", timeout: float = 60.0) -> None:
        if not isinstance(provider, AIProvider):
            raise ValueError("provider must be an instance of AIProvider")
        self._provider = provider
        self._model = model
        self._timeout = timeout

    async def decide(self, intent: UserIntent) -> RawManagerDecision:
        """
        Produce a structured manager decision from user intent using OpenRouter.

        Args:
            intent: Application-controlled user intent.

        Returns:
            RawManagerDecision — untrusted model output.

        Raises:
            AIManagerProviderTimeoutError
            AIManagerProviderRateLimitError
            AIManagerProviderContextLimitError
            AIManagerProviderEmptyResponseError
            AIManagerProviderMalformedResponseError
            AIManagerProviderUnavailableError
        """
        messages = ManagerPromptBuilder.build_messages(intent=intent)
        tool_definition = build_manager_tool_definition()

        last_exc: BaseException | None = None
        for attempt in range(3):
            try:
                response = await self._call_provider(messages, tool_definition)
                return ManagerOutputParser.parse(response)
            except AIManagerProviderMalformedResponseError:
                raise
            except AIManagerProviderError as exc:
                last_exc = exc
                if not self._is_retryable(exc):
                    raise
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        raise AIManagerProviderError("OpenRouter manager decision failed after retries")

    async def _call_provider(self, messages: list[dict[str, str]], tool_definition: dict) -> AIResponse:
        payload = {
            "model": self._model,
            "messages": messages,
            "tools": normalize_tool_definitions([tool_definition]),
            "tool_choice": {"type": "function", "function": {"name": "ai_manager.propose_decision"}},
        }

        try:
            headers = self._provider._headers()
        except OpenRouterProviderError as exc:
            raise AIManagerProviderError(str(exc), retryable=False) from exc

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._provider.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException:
            raise AIManagerProviderTimeoutError("OpenRouter manager request timed out") from None
        except (httpx.ConnectError, httpx.NetworkError):
            raise AIManagerProviderUnavailableError("OpenRouter network error") from None

        if response.status_code != 200:
            raise self._map_openrouter_error(
                self._provider._map_http_error(response)
            ) from None

        body = response.json()
        try:
            choice = body["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if not isinstance(content, str) and content is not None:
                raise AIManagerProviderMalformedResponseError(
                    "Invalid OpenRouter chat content type"
                )
            tool_calls = None
            raw_tool_calls = message.get("tool_calls")
            if isinstance(raw_tool_calls, list) and raw_tool_calls:
                from app.ai.normalization import normalize_tool_calls
                tool_calls = normalize_tool_calls(raw_tool_calls)
            finish_reason = choice.get("finish_reason") or ("tool_calls" if tool_calls else "stop")
            return AIResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )
        except AIManagerProviderMalformedResponseError:
            raise
        except Exception as exc:
            raise AIManagerProviderMalformedResponseError(
                f"Invalid OpenRouter manager response: {exc}"
            ) from exc

    @staticmethod
    def _map_openrouter_error(exc: OpenRouterProviderError) -> AIManagerProviderError:
        status = exc.status
        if status == "timeout":
            return AIManagerProviderTimeoutError(str(exc))
        if status == "rate_limited":
            return AIManagerProviderRateLimitError(str(exc))
        if status in ("model_unavailable", "server_error", "network_error"):
            return AIManagerProviderUnavailableError(str(exc))
        if status == "provider_not_configured":
            return AIManagerProviderError(str(exc), retryable=False)
        if status in (
            "invalid_api_key",
            "provider_quota_exceeded",
            "unauthorized",
            "model_not_found",
        ):
            return AIManagerProviderError(str(exc), retryable=False)
        return AIManagerProviderError(str(exc), retryable=False)

    @staticmethod
    def _is_retryable(error: AIManagerProviderError) -> bool:
        return isinstance(
            error,
            (
                AIManagerProviderTimeoutError,
                AIManagerProviderRateLimitError,
                AIManagerProviderUnavailableError,
                AIManagerProviderEmptyResponseError,
            ),
        )
