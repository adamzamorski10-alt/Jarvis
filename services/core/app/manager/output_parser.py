"""Parse provider tool-call responses into ProviderTaskProposal lists.

The parser is provider-neutral.  It accepts a normalized tool-call arguments
dict from any provider and returns a validated list of ProviderTaskProposal
instances.  It does not know about OpenRouter, Kimi, or any specific API.
"""
from app.ai.models import AIResponse
from app.manager.models import ProviderTaskProposal
from app.manager.providers.base import (
    PlanProviderEmptyResponseError,
    PlanProviderError,
    PlanProviderMalformedResponseError,
)


class PlanningOutputParser:
    @staticmethod
    def parse(response: AIResponse) -> list[ProviderTaskProposal]:
        if not response.tool_calls:
            raise PlanProviderEmptyResponseError(
                "Provider returned no tool calls.  A structured plan is required."
            )

        if len(response.tool_calls) > 1:
            raise PlanProviderMalformedResponseError(
                f"Provider returned {len(response.tool_calls)} tool calls; expected exactly 1 propose_plan call."
            )

        first_call = response.tool_calls[0]
        if first_call.tool_id != "planning.propose_plan":
            raise PlanProviderMalformedResponseError(
                f"Provider returned tool call '{first_call.tool_id}'; expected 'planning.propose_plan'."
            )

        arguments = first_call.arguments
        if not isinstance(arguments, dict):
            raise PlanProviderMalformedResponseError(
                f"Tool call arguments must be a dict, got {type(arguments).__name__}"
            )

        tasks_data = arguments.get("tasks")
        if tasks_data is None:
            raise PlanProviderMalformedResponseError(
                "Tool call arguments missing required 'tasks' field."
            )

        if not isinstance(tasks_data, list):
            raise PlanProviderMalformedResponseError(
                f"'tasks' must be a list, got {type(tasks_data).__name__}"
            )

        proposals: list[ProviderTaskProposal] = []
        for index, task_data in enumerate(tasks_data):
            if not isinstance(task_data, dict):
                raise PlanProviderMalformedResponseError(
                    f"Task at index {index} must be a dict, got {type(task_data).__name__}"
                )
            try:
                proposal = ProviderTaskProposal.model_validate(task_data)
            except Exception as exc:
                raise PlanProviderMalformedResponseError(
                    f"Task at index {index} failed validation: {exc}"
                ) from exc
            proposals.append(proposal)

        if not proposals:
            raise PlanProviderMalformedResponseError(
                "Tool call 'tasks' array is empty.  At least one task is required."
            )

        return proposals
