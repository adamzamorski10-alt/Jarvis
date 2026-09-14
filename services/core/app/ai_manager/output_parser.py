"""
AI Manager — Provider-Neutral Output Parser.

Parses AIResponse objects into validated RawManagerDecision domain objects.

FAIL-CLOSED BEHAVIOR:
- No tool calls          -> raises AIManagerEmptyResponseError
- Wrong tool name        -> raises AIManagerMalformedDecisionError
- Multiple tool calls    -> raises AIManagerMalformedDecisionError
- Missing required fields -> raises AIManagerMalformedDecisionError
- Invalid enum values    -> raises AIManagerMalformedDecisionError
- Oversized arrays/strings -> raises AIManagerMalformedDecisionError
- Failed Pydantic validation -> raises AIManagerMalformedDecisionError
- Zero fallback parsing from markdown or plain text.

RETRY RESPONSIBILITY:
  This parser performs NO retry. It raises a typed exception on any malformed
  response and lets the caller decide how to proceed. Provider-level transport
  retries belong in adapter implementations. Semantic validation (dependency
  cycles, execution state injection, policy enforcement) belongs in
  normalize_raw_decision().
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.ai_manager.errors import (
    AIManagerEmptyResponseError,
    AIManagerInvalidDecisionError,
    AIManagerMalformedDecisionError,
)
from app.ai_manager.models import (
    _MAX_ACTIONS,
    _MAX_CAPABILITIES,
    _MAX_DEPENDENCIES,
    _MAX_RATIONALE_LENGTH,
    _MAX_WARNINGS,
    _MAX_WARNING_LENGTH,
    DecisionOutcome,
    RawManagerDecision,
    RiskLevel,
)
from app.ai.models import AIResponse


class ManagerOutputParser:
    """
    Provider-neutral output parser for AI Manager provider responses.

    No retry is performed here. All failures raise typed exceptions.
    """

    TARGET_TOOL_ID = "ai_manager.propose_decision"

    @classmethod
    def parse(cls, response: AIResponse) -> RawManagerDecision:
        """
        Parse an AIResponse into a validated RawManagerDecision.

        Policy: exactly ONE ``ai_manager.propose_decision`` tool call per response.
        Zero or multiple calls are both rejected.

        Args:
            response: Raw provider AIResponse (or duck-typed equivalent).

        Returns:
            Validated RawManagerDecision.

        Raises:
            AIManagerEmptyResponseError: If response has no tool calls.
            AIManagerMalformedDecisionError: If response contains invalid,
                ambiguous, or schema-violating tool calls.
            AIManagerInvalidDecisionError: If decision structure is invalid.
        """
        if not response.tool_calls:
            raise AIManagerEmptyResponseError(
                "Provider returned no tool calls. A structured decision is required via ai_manager.propose_decision."
            )

        if len(response.tool_calls) > 1:
            raise AIManagerMalformedDecisionError(
                f"Provider returned {len(response.tool_calls)} tool calls; expected exactly 1 '{cls.TARGET_TOOL_ID}' call."
            )

        first_call = response.tool_calls[0]
        if first_call.tool_id != cls.TARGET_TOOL_ID:
            raise AIManagerMalformedDecisionError(
                f"Provider returned tool call '{first_call.tool_id}'; expected '{cls.TARGET_TOOL_ID}'."
            )

        arguments = first_call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise AIManagerMalformedDecisionError(
                    f"Tool call arguments JSON decode failed: {exc}"
                ) from exc

        if not isinstance(arguments, dict):
            raise AIManagerMalformedDecisionError(
                f"Tool call arguments must be a dict, got {type(arguments).__name__}"
            )

        # Structural validation of top-level fields before Pydantic validation
        outcome_raw = arguments.get("outcome")
        if outcome_raw is None:
            raise AIManagerMalformedDecisionError("Missing required field: outcome")
        if not isinstance(outcome_raw, str):
            raise AIManagerMalformedDecisionError(
                f"'outcome' must be a string, got {type(outcome_raw).__name__}"
            )
        try:
            DecisionOutcome(outcome_raw)
        except ValueError:
            raise AIManagerMalformedDecisionError(
                f"'outcome' must be one of {sorted(DecisionOutcome.__members__.values())}, got {outcome_raw!r}"
            )

        goal = arguments.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise AIManagerMalformedDecisionError("Missing or empty required field: goal")

        proposed_actions = arguments.get("proposed_actions", [])
        if not isinstance(proposed_actions, list):
            raise AIManagerMalformedDecisionError(
                f"'proposed_actions' must be a list, got {type(proposed_actions).__name__}"
            )
        if len(proposed_actions) > _MAX_ACTIONS:
            raise AIManagerMalformedDecisionError(
                f"'proposed_actions' exceeds max length {_MAX_ACTIONS}"
            )

        required_capabilities = arguments.get("required_capabilities", [])
        if not isinstance(required_capabilities, list):
            raise AIManagerMalformedDecisionError(
                f"'required_capabilities' must be a list, got {type(required_capabilities).__name__}"
            )
        if len(required_capabilities) > _MAX_CAPABILITIES:
            raise AIManagerMalformedDecisionError(
                f"'required_capabilities' exceeds max length {_MAX_CAPABILITIES}"
            )

        risk_level_raw = arguments.get("risk_level", "LOW")
        if not isinstance(risk_level_raw, str):
            raise AIManagerMalformedDecisionError(
                f"'risk_level' must be a string, got {type(risk_level_raw).__name__}"
            )
        try:
            RiskLevel(risk_level_raw)
        except ValueError:
            raise AIManagerMalformedDecisionError(
                f"'risk_level' must be one of {sorted(RiskLevel.__members__.values())}, got {risk_level_raw!r}"
            )

        approval_required = arguments.get("approval_required", False)
        if not isinstance(approval_required, bool):
            raise AIManagerMalformedDecisionError(
                f"'approval_required' must be a bool, got {type(approval_required).__name__}"
            )

        rationale = arguments.get("rationale")
        if rationale is not None:
            if not isinstance(rationale, str):
                raise AIManagerMalformedDecisionError(
                    f"'rationale' must be a string or null, got {type(rationale).__name__}"
                )
            if len(rationale) > _MAX_RATIONALE_LENGTH:
                raise AIManagerMalformedDecisionError(
                    f"'rationale' exceeds {_MAX_RATIONALE_LENGTH} chars"
                )

        warnings = arguments.get("warnings", [])
        if not isinstance(warnings, list):
            raise AIManagerMalformedDecisionError(
                f"'warnings' must be a list, got {type(warnings).__name__}"
            )
        if len(warnings) > _MAX_WARNINGS:
            raise AIManagerMalformedDecisionError(
                f"'warnings' exceeds max length {_MAX_WARNINGS}"
            )
        for warning in warnings:
            if not isinstance(warning, str):
                raise AIManagerMalformedDecisionError(
                    f"Each warning must be a string, got {type(warning).__name__}"
                )
            if len(warning) > _MAX_WARNING_LENGTH:
                raise AIManagerMalformedDecisionError(
                    f"A warning exceeds {_MAX_WARNING_LENGTH} chars"
                )

        # Validate action array structure
        for idx, action_data in enumerate(proposed_actions):
            if not isinstance(action_data, dict):
                raise AIManagerMalformedDecisionError(
                    f"Action at index {idx} must be a dict, got {type(action_data).__name__}"
                )

            action_id = action_data.get("action_id")
            if not isinstance(action_id, str) or not action_id:
                raise AIManagerMalformedDecisionError(
                    f"Action at index {idx} has missing or empty 'action_id'"
                )

            capability_id = action_data.get("capability_id")
            if not isinstance(capability_id, str) or not capability_id:
                raise AIManagerMalformedDecisionError(
                    f"Action at index {idx} has missing or empty 'capability_id'"
                )

            action_type = action_data.get("action_type")
            if not isinstance(action_type, str) or not action_type:
                raise AIManagerMalformedDecisionError(
                    f"Action at index {idx} has missing or empty 'action_type'"
                )

            action_args = action_data.get("arguments", {})
            if not isinstance(action_args, dict):
                raise AIManagerMalformedDecisionError(
                    f"Action 'arguments' must be a dict, got {type(action_args).__name__}"
                )
            if len(action_args) > 50:
                raise AIManagerMalformedDecisionError(
                    f"Action 'arguments' exceeds max length 50"
                )
            for key, value in action_args.items():
                if not isinstance(key, str):
                    raise AIManagerMalformedDecisionError(
                        f"Action argument key must be a string, got {type(key).__name__}"
                    )
                if len(key) > 128:
                    raise AIManagerMalformedDecisionError(
                        f"Action argument key exceeds 128 chars"
                    )
                if isinstance(value, str) and len(value) > 4096:
                    raise AIManagerMalformedDecisionError(
                        f"Action argument value for '{key}' exceeds 4096 chars"
                    )
                if isinstance(value, (list, dict)):
                    raise AIManagerMalformedDecisionError(
                        f"Action argument value for '{key}' must be a scalar, not {type(value).__name__}"
                    )

            dependencies = action_data.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise AIManagerMalformedDecisionError(
                    f"Action 'dependencies' must be a list, got {type(dependencies).__name__}"
                )
            if len(dependencies) > _MAX_DEPENDENCIES:
                raise AIManagerMalformedDecisionError(
                    f"Action 'dependencies' exceeds max length {_MAX_DEPENDENCIES}"
                )

            summary = action_data.get("summary", "")
            if not isinstance(summary, str) or not summary.strip():
                raise AIManagerMalformedDecisionError(
                    f"Action at index {idx} has missing or empty 'summary'"
                )

        # Validate required_capabilities format
        for cap in required_capabilities:
            if not isinstance(cap, str):
                raise AIManagerMalformedDecisionError(
                    f"required_capabilities entry must be a string, got {type(cap).__name__}"
                )

        uncertainty = arguments.get("uncertainty")
        if uncertainty is not None:
            if not isinstance(uncertainty, (int, float)):
                raise AIManagerMalformedDecisionError(
                    f"'uncertainty' must be a number or null, got {type(uncertainty).__name__}"
                )
            if not (0.0 <= float(uncertainty) <= 1.0):
                raise AIManagerInvalidDecisionError(
                    f"'uncertainty' must be between 0.0 and 1.0, got {uncertainty}"
                )

        try:
            raw_decision = RawManagerDecision.model_validate(arguments)
        except ValidationError as exc:
            raise AIManagerMalformedDecisionError(
                f"RawManagerDecision validation failed: {exc}"
            ) from exc

        return raw_decision
