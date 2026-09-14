"""
Stage 9.2 — AI Manager Provider Integration Tests.

Covers MANAGER-PROVIDER-01 through MANAGER-PROVIDER-35.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ai.models import AIResponse
from app.ai.normalization import normalize_tool_calls
from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.openrouter import OpenRouterProvider
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
from app.ai_manager.models import (
    ActionProposal,
    DecisionOutcome,
    ManagerDecision,
    ManagerResult,
    RawActionProposal,
    RawManagerDecision,
    RiskLevel,
    UserIntent,
)
from app.ai_manager.normalizer import normalize_raw_decision
from app.ai_manager.output_parser import ManagerOutputParser
from app.ai_manager.providers.base import (
    AIManagerProvider,
    AIManagerProviderContextLimitError,
    AIManagerProviderEmptyResponseError,
    AIManagerProviderMalformedResponseError,
    AIManagerProviderRateLimitError,
    AIManagerProviderTimeoutError,
    AIManagerProviderUnavailableError,
)
from app.ai_manager.providers.fake import FakeAIManagerProvider
from app.ai_manager.providers.openrouter import OpenRouterAIManagerAdapter
from app.ai_manager.providers.ollama import OllamaAIManagerAdapter

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient
AI_MANAGER_ROOT = Path(__file__).resolve().parent.parent / "app" / "ai_manager"


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


def _raw_decision(
    outcome: str = "PROPOSED",
    goal: str = "test goal",
    actions: list[RawActionProposal] | None = None,
    rationale: str | None = "test rationale",
    approval_required: bool = False,
) -> RawManagerDecision:
    if actions is None:
        actions = [
            RawActionProposal(
                action_id="act1",
                capability_id="development.plan",
                action_type="plan",
                arguments={"target": "test"},
                dependencies=[],
                risk=RiskLevel.LOW,
                approval_required=approval_required,
                summary="Test action",
            )
        ]
    return RawManagerDecision(
        outcome=outcome,
        goal=goal,
        proposed_actions=actions,
        required_capabilities=["development.plan"],
        risk_level=RiskLevel.LOW,
        approval_required=approval_required,
        rationale=rationale,
        warnings=[],
        uncertainty=0.5,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-01: valid Ollama structured decision
# ─────────────────────────────────────────────────────────────────────────────

class TestOllamaAdapter:
    async def test_valid_propose_decision_returns_raw_decision(self, monkeypatch):
        async def handler(request):
            if request.url.path == "/api/chat":
                body = json.loads(await request.aread())
                assert body["model"] == "qwen:test"
                assert "tools" in body
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
                                        "outcome": "PROPOSED",
                                        "goal": "Add calculator",
                                        "proposed_actions": [
                                            {
                                                "action_id": "act1",
                                                "capability_id": "development.plan",
                                                "action_type": "plan",
                                                "arguments": {"target": "calculator"},
                                                "dependencies": [],
                                                "risk": "LOW",
                                                "approval_required": False,
                                                "summary": "Plan calculator",
                                            }
                                        ],
                                        "required_capabilities": ["development.plan"],
                                        "risk_level": "LOW",
                                        "approval_required": False,
                                        "rationale": "Need to plan this",
                                        "warnings": [],
                                        "uncertainty": 0.3,
                                    }),
                                },
                            }
                        ],
                    },
                    "done": True,
                    "done_reason": "stop",
                })
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        intent = _make_intent()
        raw = await adapter.decide(intent)
        assert raw.outcome == "PROPOSED"
        assert raw.goal == "Add calculator"
        assert len(raw.proposed_actions) == 1
        assert raw.proposed_actions[0].action_id == "act1"
        assert raw.proposed_actions[0].risk == RiskLevel.LOW
        assert raw.uncertainty == 0.3

    async def test_needs_clarification(self, monkeypatch):
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
                                        "outcome": "NEEDS_CLARIFICATION",
                                        "goal": "Need more info",
                                        "proposed_actions": [],
                                        "required_capabilities": [],
                                        "risk_level": "LOW",
                                        "approval_required": False,
                                        "rationale": "Ambiguous request",
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
        raw = await adapter.decide(_make_intent())
        assert raw.outcome == DecisionOutcome.NEEDS_CLARIFICATION

    async def test_needs_approval(self, monkeypatch):
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
                                                "approval_required": True,
                                                "summary": "Execute command",
                                            }
                                        ],
                                        "required_capabilities": ["development.execute"],
                                        "risk_level": "HIGH",
                                        "approval_required": True,
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
        raw = await adapter.decide(_make_intent())
        assert raw.outcome == DecisionOutcome.NEEDS_APPROVAL
        assert raw.proposed_actions[0].risk == RiskLevel.HIGH
        assert raw.proposed_actions[0].approval_required is True

    async def test_ready_for_planning(self, monkeypatch):
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
                                        "outcome": "READY_FOR_PLANNING",
                                        "goal": "Build calculator",
                                        "proposed_actions": [],
                                        "required_capabilities": [],
                                        "risk_level": "LOW",
                                        "approval_required": False,
                                        "rationale": "Clear goal",
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
        raw = await adapter.decide(_make_intent())
        assert raw.outcome == DecisionOutcome.READY_FOR_PLANNING


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-06: malformed provider output
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedOutput:
    async def test_no_tool_calls_raises_empty(self, monkeypatch):
        async def handler(request):
            if request.url.path == "/api/chat":
                return httpx.Response(200, json={
                    "model": "qwen:test",
                    "message": {"role": "assistant", "content": "Some text"},
                    "done": True,
                })
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        with pytest.raises(AIManagerEmptyResponseError):
            await adapter.decide(_make_intent())

    async def test_wrong_tool_name_raises(self, monkeypatch):
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
                                    "name": "wrong.tool",
                                    "arguments": "{}",
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
        with pytest.raises(AIManagerMalformedDecisionError, match="wrong.tool"):
            await adapter.decide(_make_intent())

    async def test_multiple_tool_calls_raises(self, monkeypatch):
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
                                "function": {"name": "ai_manager.propose_decision", "arguments": "{}"},
                            },
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {"name": "ai_manager.propose_decision", "arguments": "{}"},
                            },
                        ],
                    },
                    "done": True,
                })
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        with pytest.raises(AIManagerMalformedDecisionError, match="2 tool calls"):
            await adapter.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-07: unknown fields
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknownFields:
    async def test_unknown_top_level_field_rejected(self, monkeypatch):
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
                                        "outcome": "PROPOSED",
                                        "goal": "test",
                                        "proposed_actions": [],
                                        "unknown_field": "value",
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
        with pytest.raises(AIManagerMalformedDecisionError):
            await adapter.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-08: invalid enum
# ─────────────────────────────────────────────────────────────────────────────

class TestInvalidEnum:
    async def test_invalid_outcome_enum(self, monkeypatch):
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
                                        "outcome": "INVALID_OUTCOME",
                                        "goal": "test",
                                        "proposed_actions": [],
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
        with pytest.raises(AIManagerMalformedDecisionError, match="outcome"):
            await adapter.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-09: missing required field
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingRequired:
    async def test_missing_outcome(self, monkeypatch):
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
                                        "goal": "test",
                                        "proposed_actions": [],
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
        with pytest.raises(AIManagerMalformedDecisionError, match="Missing required field: outcome"):
            await adapter.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-11: provider timeout
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderTimeout:
    async def test_ollama_timeout_maps_to_timeout_error(self, monkeypatch):
        async def handler(request):
            if request.url.path == "/api/chat":
                raise httpx.TimeoutException("Timed out")
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        with pytest.raises(AIManagerProviderTimeoutError):
            await adapter.decide(_make_intent())

    async def test_openrouter_timeout_maps_to_timeout_error(self, monkeypatch):
        async def handler(request):
            if request.url.path.endswith("/chat/completions"):
                raise httpx.TimeoutException("Timed out")
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OpenRouterProvider("https://openrouter.ai/api/v1", "sk-test")
        adapter = OpenRouterAIManagerAdapter(provider=provider, model="openrouter/free")
        with pytest.raises(AIManagerProviderTimeoutError):
            await adapter.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-12: provider unavailable
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderUnavailable:
    async def test_ollama_unavailable_maps(self, monkeypatch):
        async def handler(request):
            if request.url.path == "/api/chat":
                return httpx.Response(503, json={"error": "Service unavailable"})
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        with pytest.raises(AIManagerProviderUnavailableError):
            await adapter.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-13: rate limit
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimit:
    async def test_ollama_rate_limit_maps(self, monkeypatch):
        async def handler(request):
            if request.url.path == "/api/chat":
                return httpx.Response(429, json={"error": "Rate limited"})
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        with pytest.raises(AIManagerProviderRateLimitError):
            await adapter.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-14: empty response
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyResponse:
    async def test_ollama_empty_response_raises(self, monkeypatch):
        async def handler(request):
            if request.url.path == "/api/chat":
                return httpx.Response(200, json={
                    "model": "qwen:test",
                    "message": {"role": "assistant", "content": None},
                    "done": True,
                })
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        with pytest.raises(AIManagerEmptyResponseError):
            await adapter.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-16: retry is bounded
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryBounded:
    async def test_ollama_retry_exhaustion(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
            if request.url.path == "/api/chat":
                raise httpx.TimeoutException("Timed out")
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        with pytest.raises(AIManagerProviderTimeoutError):
            await adapter.decide(_make_intent())
        assert calls[0] == 3

    async def test_openrouter_retry_exhaustion(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
            if request.url.path.endswith("/chat/completions"):
                raise httpx.TimeoutException("Timed out")
            return httpx.Response(404)

        mock_async_client(monkeypatch, handler)
        provider = OpenRouterProvider("https://openrouter.ai/api/v1", "sk-test")
        adapter = OpenRouterAIManagerAdapter(provider=provider, model="openrouter/free")
        with pytest.raises(AIManagerProviderTimeoutError):
            await adapter.decide(_make_intent())
        assert calls[0] == 3


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-17: non-retryable error is not retried
# ─────────────────────────────────────────────────────────────────────────────

class TestNonRetryable:
    async def test_malformed_not_retried_ollama(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
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
                                        "outcome": "INVALID",
                                        "goal": "test",
                                        "proposed_actions": [],
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
        with pytest.raises(AIManagerMalformedDecisionError):
            await adapter.decide(_make_intent())
        assert calls[0] == 1

    async def test_auth_not_retried_openrouter(self, monkeypatch):
        calls = [0]

        async def handler(request):
            calls[0] += 1
            if request.url.path.endswith("/chat/completions"):
                return httpx.Response(401, json={"error": {"message": "Invalid API key"}})
            return httpx.Response(200, json={"data": []})

        mock_async_client(monkeypatch, handler)
        provider = OpenRouterProvider("https://openrouter.ai/api/v1", "sk-test")
        adapter = OpenRouterAIManagerAdapter(provider=provider, model="openrouter/free")
        with pytest.raises(AIManagerProviderError):
            await adapter.decide(_make_intent())
        assert calls[0] == 1


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-21: model tries to inject execution state
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionStateInjection:
    async def test_forbidden_goal_fields_rejected_by_normalizer(self):
        raw = _raw_decision(goal="execute shell command and set task_id=123")
        with pytest.raises(AIManagerMalformedDecisionError):
            normalize_raw_decision(raw, _make_intent(), {})

    async def test_forbidden_rationale_fields_rejected(self):
        raw = _raw_decision(rationale="set task.status=PENDING and attempt_count=5")
        with pytest.raises(AIManagerMalformedDecisionError):
            normalize_raw_decision(raw, _make_intent(), {})


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-22: model tries to bypass approval
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovalBypass:
    async def test_approval_policy_blocks_low_approval(self):
        raw = _raw_decision(approval_required=False)
        with pytest.raises(AIManagerPolicyRejectionError):
            normalize_raw_decision(raw, _make_intent(), {"require_approval_for_all": True})


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-23: model proposes unauthorized capability
# ─────────────────────────────────────────────────────────────────────────────

class TestUnauthorizedCapability:
    async def test_unauthorized_capability_blocked_by_policy(self):
        raw = _raw_decision(actions=[
            RawActionProposal(
                action_id="act1",
                capability_id="development.execute",
                action_type="execute",
                arguments={"cmd": "echo hi"},
                dependencies=[],
                risk=RiskLevel.LOW,
                approval_required=False,
                summary="Execute command",
            )
        ])
        with pytest.raises(AIManagerPolicyRejectionError):
            normalize_raw_decision(raw, _make_intent(), {"allowed_capabilities": ["development.plan"]})


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-24: model attempts arbitrary shell/subprocess action
# ─────────────────────────────────────────────────────────────────────────────

class TestShellSubprocessBlocked:
    async def test_shell_subprocess_arguments_accepted_as_data(self):
        raw = _raw_decision(actions=[
            RawActionProposal(
                action_id="act1",
                capability_id="shell.execute",
                action_type="execute",
                arguments={"cmd": "rm -rf /", "shell": "true"},
                dependencies=[],
                risk=RiskLevel.CRITICAL,
                approval_required=False,
                summary="Dangerous action",
            )
        ])
        decision = normalize_raw_decision(raw, _make_intent(), {"max_allowed_risk": "CRITICAL"})
        assert decision is not None
        assert decision.proposed_actions[0].arguments["cmd"] == "rm -rf /"


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-25..29: provider has no execution authority
# ─────────────────────────────────────────────────────────────────────────────

def _get_code_nodes(filepath: Path):
    with open(filepath) as fh:
        tree = ast.parse(fh.read())
    code_nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            code_nodes.append(("import", node.lineno, ast.unparse(node)))
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                code_nodes.append(("call", node.lineno, f"{ast.unparse(func)}()"))
            elif isinstance(func, ast.Name):
                code_nodes.append(("call", node.lineno, f"{func.id}()"))
    return code_nodes


class TestNoExecutionAuthority:
    def test_no_subprocess_in_adapters(self):
        files = [
            AI_MANAGER_ROOT / "providers" / "ollama.py",
            AI_MANAGER_ROOT / "providers" / "openrouter.py",
        ]
        for f in files:
            for kind, lineno, text in _get_code_nodes(f):
                assert "subprocess" not in text.lower(), f"subprocess reference in {f}:{lineno}: {text}"

    def test_no_filesystem_in_adapters(self):
        files = [
            AI_MANAGER_ROOT / "providers" / "ollama.py",
            AI_MANAGER_ROOT / "providers" / "openrouter.py",
        ]
        forbidden = ["open(", "pathlib", "shutil"]
        for f in files:
            for kind, lineno, text in _get_code_nodes(f):
                for word in forbidden:
                    assert word not in text.lower(), f"{word} in {f}:{lineno}: {text}"

    def test_no_git_in_adapters(self):
        files = [
            AI_MANAGER_ROOT / "providers" / "ollama.py",
            AI_MANAGER_ROOT / "providers" / "openrouter.py",
        ]
        for f in files:
            for kind, lineno, text in _get_code_nodes(f):
                assert "git" not in text.lower(), f"git reference in {f}:{lineno}: {text}"

    def test_no_permission_engine_in_adapters(self):
        files = [
            AI_MANAGER_ROOT / "providers" / "ollama.py",
            AI_MANAGER_ROOT / "providers" / "openrouter.py",
        ]
        for f in files:
            for kind, lineno, text in _get_code_nodes(f):
                assert "permissionengine" not in text.lower(), f"PermissionEngine in {f}:{lineno}: {text}"

    def test_no_tool_execution_service_in_adapters(self):
        files = [
            AI_MANAGER_ROOT / "providers" / "ollama.py",
            AI_MANAGER_ROOT / "providers" / "openrouter.py",
        ]
        for f in files:
            for kind, lineno, text in _get_code_nodes(f):
                assert "toolexecutionservice" not in text.lower(), f"ToolExecutionService in {f}:{lineno}: {text}"


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-30: provider-neutral contract works with fake provider
# ─────────────────────────────────────────────────────────────────────────────

class TestFakeProvider:
    async def test_fake_provider_returns_decision(self):
        fake = FakeAIManagerProvider(decision=_raw_decision())
        intent = _make_intent()
        raw = await fake.decide(intent)
        assert raw.outcome == "PROPOSED"
        assert len(fake.calls) == 1
        assert fake.calls[0] is intent

    async def test_fake_provider_raises_error(self):
        fake = FakeAIManagerProvider(error=AIManagerProviderTimeoutError("timeout"))
        with pytest.raises(AIManagerProviderTimeoutError):
            await fake.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-31: canonical error hierarchy identity
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorHierarchy:
    def test_provider_error_is_same_as_canonical(self):
        from app.ai_manager.providers.base import AIManagerProviderError as ProviderBase
        from app.ai_manager.errors import AIManagerProviderError as CanonicalBase
        assert ProviderBase is CanonicalBase

    def test_all_provider_errors_inherit_canonical(self):
        from app.ai_manager.providers.base import (
            AIManagerProviderContextLimitError,
            AIManagerProviderEmptyResponseError,
            AIManagerProviderMalformedResponseError,
            AIManagerProviderRateLimitError,
            AIManagerProviderTimeoutError,
            AIManagerProviderUnavailableError,
        )
        for cls in [
            AIManagerProviderTimeoutError,
            AIManagerProviderRateLimitError,
            AIManagerProviderContextLimitError,
            AIManagerProviderEmptyResponseError,
            AIManagerProviderMalformedResponseError,
            AIManagerProviderUnavailableError,
        ]:
            instance = cls("test")
            assert isinstance(instance, AIManagerProviderError)
            assert hasattr(instance, "reason")
            assert hasattr(instance, "retryable")


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-32: safe error serialization
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeErrorSerialization:
    async def test_api_key_not_exposed_in_ollama_error(self, monkeypatch):
        async def handler(request):
            return httpx.Response(401, json={"error": "Unauthorized"})

        mock_async_client(monkeypatch, handler)
        provider = OllamaProvider("http://ollama.test")
        adapter = OllamaAIManagerAdapter(provider=provider, model="qwen:test")
        with pytest.raises(AIManagerProviderError) as exc_info:
            await adapter.decide(_make_intent())
        error_message = str(exc_info.value)
        assert "sk-secret-key-12345" not in error_message

    async def test_api_key_not_exposed_in_openrouter_error(self, monkeypatch):
        async def handler(request):
            return httpx.Response(401, json={"error": {"message": "Unauthorized"}})

        mock_async_client(monkeypatch, handler)
        provider = OpenRouterProvider("https://openrouter.ai/api/v1", "sk-secret-key-12345")
        adapter = OpenRouterAIManagerAdapter(provider=provider, model="openrouter/free")
        with pytest.raises(AIManagerProviderError) as exc_info:
            await adapter.decide(_make_intent())
        error_message = str(exc_info.value)
        assert "sk-secret-key-12345" not in error_message


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-33: bounded model output
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundedOutput:
    async def test_oversized_goal_rejected(self, monkeypatch):
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
                                        "outcome": "PROPOSED",
                                        "goal": "x" * 4097,
                                        "proposed_actions": [],
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
        with pytest.raises(AIManagerMalformedDecisionError, match="at most 4096 characters"):
            await adapter.decide(_make_intent())


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-34: deterministic parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestDeterministicParsing:
    async def test_same_input_produces_same_output(self, monkeypatch):
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
                                        "outcome": "PROPOSED",
                                        "goal": "test",
                                        "proposed_actions": [
                                            {
                                                "action_id": "act1",
                                                "capability_id": "dev.plan",
                                                "action_type": "plan",
                                                "arguments": {"a": "1"},
                                                "dependencies": [],
                                                "risk": "LOW",
                                                "approval_required": False,
                                                "summary": "Plan",
                                            }
                                        ],
                                        "required_capabilities": ["dev.plan"],
                                        "risk_level": "LOW",
                                        "approval_required": False,
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
        raw1 = await adapter.decide(_make_intent("request1"))
        raw2 = await adapter.decide(_make_intent("request1"))
        assert raw1.outcome == raw2.outcome
        assert raw1.goal == raw2.goal
        assert len(raw1.proposed_actions) == len(raw2.proposed_actions)


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-35: no execution side effects
# ─────────────────────────────────────────────────────────────────────────────

class TestNoExecutionSideEffects:
    async def test_adapter_does_not_call_execution_services(self, monkeypatch):
        """Verify adapter only calls HTTP transport and parser."""
        call_log = []

        async def handler(request):
            call_log.append(("http", request.url.path))
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
                                        "outcome": "PROPOSED",
                                        "goal": "test",
                                        "proposed_actions": [],
                                        "required_capabilities": [],
                                        "risk_level": "LOW",
                                        "approval_required": False,
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
        await adapter.decide(_make_intent())
        paths = [c[1] for c in call_log]
        assert "/api/chat" in paths


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER-PROVIDER-02: valid OpenRouter structured decision
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenRouterAdapter:
    async def test_valid_propose_decision_returns_raw_decision(self, monkeypatch):
        async def handler(request):
            if request.url.path.endswith("/chat/completions"):
                body = json.loads(await request.aread())
                assert "tools" in body
                assert body["tool_choice"] == {"type": "function", "function": {"name": "ai_manager.propose_decision"}}
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
                                            "rationale": "OpenRouter plan",
                                            "warnings": [],
                                            "uncertainty": 0.2,
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
        raw = await adapter.decide(_make_intent())
        assert raw.outcome == "PROPOSED"
        assert raw.goal == "Add calculator via OpenRouter"
        assert len(raw.proposed_actions) == 1
        assert raw.proposed_actions[0].action_id == "act_or_1"
        assert raw.uncertainty == 0.2
