"""
Stage 9.2 — AI Manager Integration Test (Decision-Only).

Proves:
  UserIntent -> AIManager -> selected provider adapter -> normalized ManagerResult

without involving:
  PlanExecutionService, AutonomousPlanExecutor, RunController,
  DevelopmentAgent, ToolExecutionService, PermissionEngine,
  VerificationGate, Git, workspace mutation.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai_manager.ai_manager import AIManager
from app.ai_manager.models import DecisionOutcome, ManagerResult, UserIntent
from app.ai_manager.normalizer import normalize_raw_decision
from app.ai_manager.output_parser import ManagerOutputParser
from app.ai_manager.providers.fake import FakeAIManagerProvider
from app.ai_manager.providers.openrouter import OpenRouterAIManagerAdapter
from app.ai_manager.providers.ollama import OllamaAIManagerAdapter

ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def mock_async_client(monkeypatch, handler):
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: ORIGINAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler), timeout=k.get("timeout")
        ),
    )


def _make_intent(user_request: str = "test request") -> UserIntent:
    return UserIntent(
        intent_id="test-intent",
        user_request=user_request,
        normalized_goal="test goal",
        context={"key": "value"},
        constraints={"limit": "10"},
    )


def _manager_tool_response(outcome: str = "PROPOSED", goal: str = "test goal") -> dict:
    return {
        "model": "test-model",
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "ai_manager.propose_decision",
                        "arguments": json.dumps({
                            "outcome": outcome,
                            "goal": goal,
                            "proposed_actions": [
                                {
                                    "action_id": "act1",
                                    "capability_id": "development.plan",
                                    "action_type": "plan",
                                    "arguments": {"target": "test"},
                                    "dependencies": [],
                                    "risk": "LOW",
                                    "approval_required": False,
                                    "summary": "Test action",
                                }
                            ],
                            "required_capabilities": ["development.plan"],
                            "risk_level": "LOW",
                            "approval_required": False,
                            "rationale": "test",
                        }),
                    },
                }
            ],
        },
        "done": True,
        "done_reason": "stop",
    }


class TestAIManagerIntegration:
    async def test_full_pipeline_with_fake_provider(self):
        fake = FakeAIManagerProvider(decision=None)
        # Manually inject a decision to simulate successful provider call
        from app.ai_manager.models import RawManagerDecision, RawActionProposal, RiskLevel
        fake._decision = RawManagerDecision(
            outcome="PROPOSED",
            goal="Plan calculator",
            proposed_actions=[
                RawActionProposal(
                    action_id="act1",
                    capability_id="development.plan",
                    action_type="plan",
                    arguments={"target": "calculator"},
                    dependencies=[],
                    risk=RiskLevel.LOW,
                    approval_required=False,
                    summary="Plan calculator",
                )
            ],
            required_capabilities=["development.plan"],
            risk_level=RiskLevel.LOW,
            approval_required=False,
            rationale="test",
        )

        manager = AIManager(provider=fake)
        result = await manager.decide(_make_intent("build a calculator"))
        assert isinstance(result, ManagerResult)
        assert result.outcome == DecisionOutcome.PROPOSED
        assert result.decision is not None
        assert result.decision.goal == "Plan calculator"
        assert len(result.decision.proposed_actions) == 1
        assert result.decision.proposed_actions[0].action_id == "act1"
        assert result.error is None

    async def test_full_pipeline_ollama_adapter(self, monkeypatch):
        async def handler(request):
            if request.url.path == "/api/chat":
                return httpx.Response(200, json=_manager_tool_response("READY_FOR_PLANNING", "Build calculator"))
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        manager = AIManager(provider=adapter)
        result = await manager.decide(_make_intent("plan how to add calculator"))
        assert result.outcome == DecisionOutcome.READY_FOR_PLANNING
        assert result.decision is not None
        assert result.decision.goal == "Build calculator"

    async def test_full_pipeline_openrouter_adapter(self, monkeypatch):
        async def handler(request):
            if request.url.path.endswith("/chat/completions"):
                return httpx.Response(200, json={
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_or_1",
                                    "type": "function",
                                    "function": {
                                        "name": "ai_manager.propose_decision",
                                        "arguments": json.dumps({
                                            "outcome": "PROPOSED",
                                            "goal": "Add calculator via OpenRouter",
                                            "proposed_actions": [
                                                {
                                                    "action_id": "act_or_1",
                                                    "capability_id": "development.plan",
                                                    "action_type": "plan",
                                                    "arguments": {"target": "calculator"},
                                                    "dependencies": [],
                                                    "risk": "LOW",
                                                    "approval_required": False,
                                                    "summary": "Plan calculator via OpenRouter",
                                                }
                                            ],
                                            "required_capabilities": ["development.plan"],
                                            "risk_level": "LOW",
                                            "approval_required": False,
                                        }),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }]
                })
            return httpx.Response(200, json={"data": []})

        mock_async_client(monkeypatch, handler)
        provider = OpenRouterProvider("https://openrouter.ai/api/v1", "sk-test")
        adapter = OpenRouterAIManagerAdapter(provider=provider, model="openrouter/free")
        manager = AIManager(provider=adapter)
        result = await manager.decide(_make_intent("plan calculator via OpenRouter"))
        assert result.outcome == DecisionOutcome.PROPOSED
        assert result.decision is not None
        assert result.decision.proposed_actions[0].action_id == "act_or_1"

    async def test_full_pipeline_with_normalizer_policy_enforcement(self, monkeypatch):
        async def handler(request):
            if request.url.path == "/api/chat":
                return httpx.Response(200, json={
                    "model": "qwen:test",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "ai_manager.propose_decision",
                                    "arguments": json.dumps({
                                        "outcome": "NEEDS_APPROVAL",
                                        "goal": "High-risk action",
                                        "proposed_actions": [
                                            {
                                                "action_id": "act1",
                                                "capability_id": "development.execute",
                                                "action_type": "execute",
                                                "arguments": {"cmd": "echo hi"},
                                                "dependencies": [],
                                                "risk": "HIGH",
                                                "approval_required": False,
                                                "summary": "Execute command",
                                            }
                                        ],
                                        "required_capabilities": ["development.execute"],
                                        "risk_level": "HIGH",
                                        "approval_required": False,
                                        "rationale": "High risk action",
                                    }),
                                },
                            }
                        ],
                    },
                    "done": True,
                })
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        manager = AIManager(provider=adapter, application_policy={"max_allowed_risk": "MEDIUM"})
        result = await manager.decide(_make_intent("high risk action"))
        assert result.outcome == DecisionOutcome.REJECTED
        assert result.decision is None
        assert result.error is not None
        assert "exceeds policy maximum" in str(result.error)

    async def test_full_pipeline_provider_error_maps_to_result_error(self, monkeypatch):
        async def handler(request):
            if request.url.path == "/api/chat":
                raise httpx.TimeoutException("Timed out")
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        manager = AIManager(provider=adapter)
        result = await manager.decide(_make_intent())
        assert result.outcome == DecisionOutcome.REJECTED
        assert result.decision is None
        assert result.error is not None
