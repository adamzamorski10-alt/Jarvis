"""
Provider-Neutral Coding Output Parser.

Parses AIResponse objects into validated CodingStepResult domain objects.

FAIL-CLOSED BEHAVIOR:
- No tool calls          -> raises CodingParserEmptyResponseError
- Wrong tool name        -> raises CodingParserMalformedResponseError
- Multiple tool calls    -> raises CodingParserMalformedResponseError
- Empty operations list  -> raises CodingParserMalformedResponseError
- Invalid arguments / malformed JSON -> raises CodingParserMalformedResponseError
- Failed Pydantic validation -> raises CodingParserMalformedResponseError
- Zero fallback parsing from markdown or plain text.

RETRY RESPONSIBILITY:
  This parser performs NO retry. It raises a typed exception on any malformed
  response and lets the caller decide how to proceed. Provider-level transport
  retries belong in future CodingProvider adapter implementations.
  Agent-level execution limits are enforced by BaseDevelopmentAgent.
  Timeout/cancellation authority belongs to RunController.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.ai.models import AIResponse
from app.ai.providers.coding_base import CodingStepResult


class CodingParserError(ValueError):
    """Base exception for coding parser failures."""
    pass


class CodingParserEmptyResponseError(CodingParserError):
    """Raised when the provider returns no tool calls."""
    pass


class CodingParserMalformedResponseError(CodingParserError):
    """Raised when provider output or tool call arguments are malformed."""
    pass


class CodingOutputParser:
    """
    Provider-neutral output parser for coding provider responses.

    No retry is performed here. All failures raise typed exceptions.
    """

    TARGET_TOOL_ID = "coding.propose_step"

    @classmethod
    def parse(cls, response: AIResponse) -> CodingStepResult:
        """
        Parse an AIResponse into a validated CodingStepResult.

        Policy: exactly ONE ``coding.propose_step`` tool call per response.
        Zero or multiple calls are both rejected. Empty operations are rejected.
        No retry is performed; raise and let the caller decide.

        Args:
            response: Raw provider AIResponse (or duck-typed equivalent).

        Returns:
            Validated CodingStepResult with at least one operation.

        Raises:
            CodingParserEmptyResponseError: If response has no tool calls.
            CodingParserMalformedResponseError: If response contains invalid,
                ambiguous, empty-operations, or schema-violating tool calls.
        """
        if not response.tool_calls:
            raise CodingParserEmptyResponseError(
                "Provider returned no tool calls. A structured coding step is required via coding.propose_step."
            )

        if len(response.tool_calls) > 1:
            raise CodingParserMalformedResponseError(
                f"Provider returned {len(response.tool_calls)} tool calls; expected exactly 1 '{cls.TARGET_TOOL_ID}' call."
            )

        first_call = response.tool_calls[0]
        if first_call.tool_id != cls.TARGET_TOOL_ID:
            raise CodingParserMalformedResponseError(
                f"Provider returned tool call '{first_call.tool_id}'; expected '{cls.TARGET_TOOL_ID}'."
            )

        arguments = first_call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise CodingParserMalformedResponseError(
                    f"Tool call arguments JSON decode failed: {exc}"
                ) from exc

        if not isinstance(arguments, dict):
            raise CodingParserMalformedResponseError(
                f"Tool call arguments must be a dict, got {type(arguments).__name__}"
            )

        try:
            result = CodingStepResult.model_validate(arguments)
        except (ValidationError, ValueError) as exc:
            raise CodingParserMalformedResponseError(
                f"Coding step validation failed: {exc}"
            ) from exc

        if not result.operations:
            raise CodingParserMalformedResponseError(
                "Provider returned a coding step with no operations. At least one operation is required."
            )

        return result
