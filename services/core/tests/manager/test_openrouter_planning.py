"""Tests for Stage 8.4 OpenRouter planning provider integration."""
import json
import logging

import httpx
import pytest

from app.ai.models import AIResponse
from app.ai.normalization import normalize_tool_calls
from app.manager.models import PlanningConstraints, ProviderTaskProposal, UserPlan
from app.manager.output_parser import PlanningOutputParser
from app.manager.providers.base import (
    PlanProviderContextLimitError,
    PlanProviderEmptyResponseError,
    PlanProviderError,
    PlanProviderMalformedResponseError,
    PlanProviderRateLimitError,
    PlanProviderTimeoutError,
    PlanProviderUnavailableError,
)
from app.manager.prompt_builder import build_messages, build_system_prompt, build_user_prompt
from app.manager.providers.openrouter import OpenRouterPlanningAdapter

ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def mock_async_client(monkeypatch, handler):
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: ORIGINAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler), timeout=k.get("timeout")
        ),
    )


def _successful_plan_response(tasks):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "planning.propose_plan",
                                    "arguments": json.dumps({"tasks": tasks}),
                                },
                            }
                        ]
                    }
                }
            ]
        },
    )


def _make_user_plan():
    return UserPlan(plan_id="plan1", user_request="build a todo app", project_id="proj")


def _make_constraints():
    return PlanningConstraints()


# ── Output parser tests ───────────────────────────────────────────────────────


class TestPlanningOutputParser:
    def test_valid_tool_call_returns_proposals(self):
        tool_call = normalize_tool_calls(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "planning.propose_plan",
                        "arguments": json.dumps(
                            {
                                "tasks": [
                                    {
                                        "proposal_key": "auth",
                                        "title": "Auth",
                                        "description": "Add auth",
                                    }
                                ]
                            }
                        ),
                    },
                }
            ]
        )[0]
        response = AIResponse(tool_calls=[tool_call])
        result = PlanningOutputParser.parse(response)
        assert len(result) == 1
        assert result[0].proposal_key == "auth"

    def test_missing_tool_calls_raises_empty_response(self):
        response = AIResponse(content="some text")
        with pytest.raises(PlanProviderEmptyResponseError):
            PlanningOutputParser.parse(response)

    def test_wrong_tool_name_raises(self):
        tool_call = normalize_tool_calls(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "planning.other_tool",
                        "arguments": json.dumps(
                            {"tasks": [{"proposal_key": "t1", "title": "T", "description": "D"}]}
                        ),
                    },
                }
            ]
        )[0]
        response = AIResponse(tool_calls=[tool_call])
        with pytest.raises(PlanProviderMalformedResponseError, match="planning.other_tool"):
            PlanningOutputParser.parse(response)

    def test_multiple_tool_calls_raises(self):
        tool_calls = normalize_tool_calls(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "planning.propose_plan",
                        "arguments": json.dumps({"tasks": []}),
                    },
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "planning.propose_plan",
                        "arguments": json.dumps({"tasks": []}),
                    },
                },
            ]
        )
        response = AIResponse(tool_calls=tool_calls)
        with pytest.raises(PlanProviderMalformedResponseError, match="2 tool calls"):
            PlanningOutputParser.parse(response)

    def test_missing_tasks_field_raises(self):
        tool_call = normalize_tool_calls(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "planning.propose_plan",
                        "arguments": json.dumps({"other": "value"}),
                    },
                }
            ]
        )[0]
        response = AIResponse(tool_calls=[tool_call])
        with pytest.raises(PlanProviderMalformedResponseError, match="missing required 'tasks'"):
            PlanningOutputParser.parse(response)

    def test_non_list_tasks_raises(self):
        tool_call = normalize_tool_calls(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "planning.propose_plan",
                        "arguments": json.dumps({"tasks": "not a list"}),
                    },
                }
            ]
        )[0]
        response = AIResponse(tool_calls=[tool_call])
        with pytest.raises(PlanProviderMalformedResponseError, match="'tasks' must be a list"):
            PlanningOutputParser.parse(response)

    def test_invalid_task_rejected_by_pydantic(self):
        tool_call = normalize_tool_calls(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "planning.propose_plan",
                        "arguments": json.dumps(
                            {"tasks": [{"proposal_key": "t1"}]}  # missing title, description
                        ),
                    },
                }
            ]
        )[0]
        response = AIResponse(tool_calls=[tool_call])
        with pytest.raises(PlanProviderMalformedResponseError, match="failed validation"):
            PlanningOutputParser.parse(response)

    def test_extra_task_field_rejected(self):
        tool_call = normalize_tool_calls(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "planning.propose_plan",
                        "arguments": json.dumps(
                            {
                                "tasks": [
                                    {
                                        "proposal_key": "t1",
                                        "title": "T",
                                        "description": "D",
                                        "extra_field": "value",
                                    }
                                ]
                            }
                        ),
                    },
                }
            ]
        )[0]
        response = AIResponse(tool_calls=[tool_call])
        with pytest.raises(PlanProviderMalformedResponseError):
            PlanningOutputParser.parse(response)


# ── Adapter tests ─────────────────────────────────────────────────────────────


class TestOpenRouterPlanningAdapter:
    async def test_valid_propose_plan_returns_tasks(self, monkeypatch):
        async def handler(request):
            return _successful_plan_response(
                [
                    {
                        "proposal_key": "auth",
                        "title": "Add authentication",
                        "description": "Implement JWT",
                        "priority": 1,
                        "dependencies": [],
                    }
                ]
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        result = await adapter.generate_plan(_make_user_plan(), _make_constraints())
        assert len(result) == 1
        assert result[0].proposal_key == "auth"

    async def test_missing_tool_calls_raises_empty_response(self, monkeypatch):
        async def handler(request):
            return httpx.Response(200, json={"choices": [{"message": {}}]})

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderEmptyResponseError):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())

    async def test_wrong_tool_name_raises(self, monkeypatch):
        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "other_tool",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderMalformedResponseError, match="other_tool"):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())

    async def test_multiple_tool_calls_raises(self, monkeypatch):
        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "planning.propose_plan",
                                            "arguments": "{}",
                                        },
                                    },
                                    {
                                        "id": "call_2",
                                        "type": "function",
                                        "function": {
                                            "name": "planning.propose_plan",
                                            "arguments": "{}",
                                        },
                                    },
                                ]
                            }
                        }
                    ]
                },
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderMalformedResponseError, match="2 tool calls"):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())

    async def test_missing_tasks_field_raises(self, monkeypatch):
        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "planning.propose_plan",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderMalformedResponseError, match="missing required 'tasks'"):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())

    async def test_non_list_tasks_raises(self, monkeypatch):
        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "planning.propose_plan",
                                            "arguments": json.dumps({"tasks": "not a list"}),
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderMalformedResponseError, match="'tasks' must be a list"):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())

    async def test_invalid_task_rejected_by_pydantic(self, monkeypatch):
        async def handler(request):
            return _successful_plan_response(
                [{"proposal_key": "t1"}]  # missing title and description
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderMalformedResponseError, match="failed validation"):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())

    async def test_extra_task_field_rejected(self, monkeypatch):
        async def handler(request):
            return _successful_plan_response(
                [
                    {
                        "proposal_key": "t1",
                        "title": "T",
                        "description": "D",
                        "extra_field": "value",
                    }
                ]
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderMalformedResponseError):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())

    async def test_retry_on_timeout(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
            if calls[0] < 3:
                raise httpx.TimeoutException("Timed out")
            return _successful_plan_response(
                [{"proposal_key": "t1", "title": "T", "description": "D"}]
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        result = await adapter.generate_plan(_make_user_plan(), _make_constraints())
        assert len(result) == 1
        assert calls[0] == 3

    async def test_retry_on_rate_limit(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
            if calls[0] < 2:
                return httpx.Response(429, json={"error": {"message": "Rate limited"}})
            return _successful_plan_response(
                [{"proposal_key": "t1", "title": "T", "description": "D"}]
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        result = await adapter.generate_plan(_make_user_plan(), _make_constraints())
        assert len(result) == 1
        assert calls[0] == 2

    async def test_retry_on_unavailable_server_error(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
            if calls[0] < 2:
                return httpx.Response(503, json={"error": {"message": "Unavailable"}})
            return _successful_plan_response(
                [{"proposal_key": "t1", "title": "T", "description": "D"}]
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        result = await adapter.generate_plan(_make_user_plan(), _make_constraints())
        assert len(result) == 1
        assert calls[0] == 2

    async def test_no_retry_on_malformed_structured_output(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
            return _successful_plan_response(
                [{"proposal_key": "t1"}]  # missing title/description
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderMalformedResponseError):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())
        assert calls[0] == 1

    async def test_no_retry_on_authentication_error(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
            return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderError):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())
        assert calls[0] == 1

    async def test_retry_exhaustion(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
            raise httpx.TimeoutException("Timed out")

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        with pytest.raises(PlanProviderTimeoutError):
            await adapter.generate_plan(_make_user_plan(), _make_constraints())
        assert calls[0] == 3

    async def test_tool_choice_forced_in_request(self, monkeypatch):
        captured = []

        async def handler(request):
            captured.append(request)
            return _successful_plan_response(
                [{"proposal_key": "t1", "title": "T", "description": "D"}]
            )

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        await adapter.generate_plan(_make_user_plan(), _make_constraints())

        assert len(captured) == 1
        body = json.loads(captured[0].content)
        assert body.get("tool_choice") == {
            "type": "function",
            "function": {"name": "planning.propose_plan"},
        }

    async def test_default_model_is_openrouter_free(self):
        adapter = OpenRouterPlanningAdapter(api_key="sk-test")
        assert adapter._model == "openrouter/free"

    async def test_custom_model_can_be_supplied(self):
        adapter = OpenRouterPlanningAdapter(
            api_key="sk-test", model="google/gemini-2.0-flash-exp:free"
        )
        assert adapter._model == "google/gemini-2.0-flash-exp:free"

    async def test_api_key_not_exposed_in_errors(self, monkeypatch):
        async def handler(request):
            return httpx.Response(401, json={"error": {"message": "Unauthorized"}})

        mock_async_client(monkeypatch, handler)
        adapter = OpenRouterPlanningAdapter(api_key="super-secret-key-12345")
        user_plan = _make_user_plan()
        constraints = _make_constraints()

        with pytest.raises(PlanProviderError) as exc_info:
            await adapter.generate_plan(user_plan, constraints)

        error_message = str(exc_info.value)
        assert "super-secret-key-12345" not in error_message


class TestPromptBuilderSecurity:
    def test_user_request_curly_braces_do_not_crash_prompt(self):
        user_plan = UserPlan(
            plan_id="plan1",
            user_request="build app with {dangerous} instruction",
            project_id="proj",
        )
        constraints = PlanningConstraints()
        system_prompt = build_system_prompt(user_plan, constraints)
        user_prompt = build_user_prompt(user_plan, constraints)
        assert "{dangerous}" in user_prompt
        assert "build app with {{dangerous}} instruction" in user_prompt

    def test_acceptance_criteria_curly_braces_do_not_crash_prompt(self):
        user_plan = UserPlan(
            plan_id="plan1",
            user_request="build app",
            project_id="proj",
            acceptance_criteria=["must handle {special} chars", "normal criterion"],
        )
        constraints = PlanningConstraints()
        system_prompt = build_system_prompt(user_plan, constraints)
        user_prompt = build_user_prompt(user_plan, constraints)
        assert "{special}" in system_prompt
        assert "must handle {{special}} chars" in user_prompt

    def test_project_id_curly_braces_do_not_crash_prompt(self):
        user_plan = UserPlan(
            plan_id="plan1",
            user_request="build app",
            project_id="proj{1}",
        )
        constraints = PlanningConstraints()
        system_prompt = build_system_prompt(user_plan, constraints)
        user_prompt = build_user_prompt(user_plan, constraints)
        assert "proj{{1}}" in system_prompt
        assert "proj{{1}}" in user_prompt
