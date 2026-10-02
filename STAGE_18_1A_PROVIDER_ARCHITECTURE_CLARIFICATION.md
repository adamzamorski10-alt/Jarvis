# Stage 18.1A — Provider Architecture Clarification

## Executive Summary

This document clarifies the relationship between the two provider hierarchies in the AI Operator repository and determines whether the dual hierarchy blocks Stage 18 (File Tool Expansion).

**CONCLUSION: PROCEED_WITH_STAGE_18**

The dual provider hierarchy is **not a blocker** for Stage 18. The `app/ai/providers/` hierarchy is the single active production provider layer. The `app/ai_manager/providers/` hierarchy is **dead code** — never instantiated in any production route, service, or orchestrator. File tools integrate with `ToolExecutionService` and `PermissionEngine`, not with any provider hierarchy directly.

**Verdict: STAGE_18_1A_CLARIFICATION_COMPLETE**

---

## 1. Executive Conclusion

**FACT:** The repository contains two provider hierarchies:
1. `app/ai/providers/` — active production code
2. `app/ai_manager/providers/` — dead code (tests only)

**FACT:** A third hierarchy exists: `app/manager/providers/` containing `PlanProvider` — also dead code.

**OBSERVATION:** The `app/ai_manager/providers/` hierarchy was built in anticipation of the AI Manager planning/governance subsystem, but that subsystem is **not wired into any production flow**. It exists only for tests and potential future use.

**RECOMMENDATION:** 
- **DO NOT block Stage 18** on provider unification
- **DEFERRED:** Document the dead code status of `app/ai_manager/providers/` and `app/manager/providers/`
- **DEFERRED:** Decide whether to delete dead code or keep it for future Model Router work
- **DEFERRED:** Reconcile hierarchies when Model Router is built (Stage 21+)

---

## 2. Current Provider Architecture

### 2.1 Active Hierarchy: `app/ai/providers/`

**Purpose:** General-purpose AI provider abstraction for chat and autonomous coding.

**Base Interface:** `AIProvider(ABC)` in `base.py`
```python
class AIProvider(ABC):
    async def health_check(self) -> ProviderHealth
    async def list_models(self) -> list[AIModel]
    async def chat(self, model, messages, tools=None) -> AIResponse
```

**Concrete Implementations:**
| Class | File | Purpose |
|-------|------|---------|
| `OpenRouterProvider` | `openrouter.py` | OpenRouter API client |
| `OllamaProvider` | `ollama.py` | Ollama local API client |
| `KimiProvider` | `kimi.py` | Kimi/Moonshot API client |
| `FutureProvider` | `future.py` | Placeholder for unimplemented providers |

**Coding Subsystem:** Separate `CodingProvider(ABC)` abstraction in `coding_base.py`
| Class | File | Purpose |
|-------|------|---------|
| `OpenRouterCodingAdapter` | `coding_openrouter.py` | Wraps `OpenRouterProvider` for coding tasks |
| `OllamaCodingAdapter` | `coding_ollama.py` | Wraps `OllamaProvider` for coding tasks |

**Factory/Manager:** `ProviderManager` in `services/core/app/ai/services/provider_manager.py`
- `SUPPORTED_PROVIDERS = ("ollama", "openrouter", "kimi", "kilo", "omniroute")`
- `get_provider()` — returns `AIProvider` based on settings
- `get_coding_provider()` — returns `CodingProvider` based on settings
- No singleton pattern; fresh instances per call

**Configuration Source:** `SettingsService` + `CredentialService`
- Provider selection: `ai_provider` setting
- Model selection: `ai_selected_model` setting
- API keys: DPAPI-encrypted in SQLite (`ai_openrouter_api_key_encrypted`, `ai_kimi_api_key_encrypted`)
- URLs: `ai_ollama_url`, `ai_openrouter_base_url`, `ai_kimi_base_url`
- Kilo: `kilo_executable_path`, `kilo_auto_approve`

**Used by:**
- `AIService` (normal AI chat)
- `DevelopmentAgentFactory` (autonomous coding)
- `OllamaProcessManager` (Ollama lifecycle)
- `OllamaDownloadManager` (model downloads)

### 2.2 Dead Hierarchy: `app/ai_manager/providers/`

**Purpose:** AI Manager planning/governance provider abstraction.

**Base Interface:** `AIManagerProvider(ABC)` in `base.py`
```python
class AIManagerProvider(ABC):
    @abstractmethod
    async def decide(self, intent: UserIntent) -> RawManagerDecision:
        ...
```

**Concrete Implementations:**
| Class | File | Purpose |
|-------|------|---------|
| `OpenRouterAIManagerAdapter` | `openrouter.py` | Wraps `OpenRouterProvider` for AI Manager |
| `OllamaAIManagerAdapter` | `ollama.py` | Direct HTTP to Ollama (does NOT wrap `OllamaProvider`) |
| `FakeAIManagerProvider` | `fake.py` | Test double |

**CRITICAL FINDING:** `AIManager` is **never instantiated in production code**.

**Evidence:** Grep for `AIManager(` across `services/core/app` returns zero results. The class is only instantiated in tests:
- `tests/test_ai_manager.py`
- `tests/test_ai_manager_integration.py`

**Dead Code Includes:**
- `AIManager` class
- `ManagerPlanningService`
- `ExecutionBridge`
- `PlanManager`
- All provider adapters in `app/ai_manager/providers/`

### 2.3 Dead Hierarchy: `app/manager/providers/`

**Purpose:** Planning provider abstraction.

**Concrete Implementations:**
| Class | File | Purpose |
|-------|------|---------|
| `OpenRouterPlanningAdapter` | `openrouter.py` | Planning provider for OpenRouter |
| `PlanProvider` (ABC) | `base.py` | Base interface |

**CRITICAL FINDING:** `PlanManager` and planning providers are **never instantiated in production code**. Only used in tests.

---

## 3. Call Graph

### 3.1 Normal AI Chat

```
User Request
  → API Route (/api/ai/chat)
    → AIService.chat()
      → ProviderManager.get_config()  [reads SettingsService]
      → ProviderManager.get_provider()  [instantiates AIProvider]
        → OpenRouterProvider / OllamaProvider / KimiProvider / FutureProvider
      → provider.chat(model, messages, tools)
      → ToolExecutionService.execute_tool_calls()  [for tool calls]
```

**Provider Hierarchy Used:** `app/ai/providers/`

### 3.2 AI Manager (DEAD CODE)

```
User Intent
  → AIManager.decide()  [NEVER CALLED IN PRODUCTION]
    → AIManagerProvider.decide()
      → OpenRouterAIManagerAdapter / OllamaAIManagerAdapter
        → OpenRouterProvider (wrapped) OR direct HTTP (Ollama)
```

**Provider Hierarchy Used:** `app/ai_manager/providers/`

**Status:** Dead code. No production caller.

### 3.3 Autonomous Development

```
Task Request
  → DevelopmentAgentFactory.create_agent()
    → provider_type == "kilo":
      → KiloDevelopmentAgent  [bypasses ProviderManager entirely]
    → provider_type == "ollama"/"openrouter":
      → ProviderManager.get_coding_provider()
        → OllamaCodingAdapter / OpenRouterCodingAdapter
      → BaseDevelopmentAgent(coding_provider)
```

**Provider Hierarchy Used:** `app/ai/providers/` (coding subsystem)

**BYPASS:** Kilo branch bypasses `ProviderManager.get_coding_provider()` and instantiates `KiloDevelopmentAgent` directly.

### 3.4 Kilo

```
KiloDevelopmentAgent.execute_with_prompt()
  → start_kilo_process()  [subprocess wrapper]
  → Kilo CLI subprocess
  → parse JSON output
```

**Provider Hierarchy Used:** None. Kilo is a subprocess wrapper, not a provider.

### 3.5 Planning

```
Plan Request
  → API Route (/api/plans)
    → [BUG] imports from non-existent module
```

**Status:** Broken. `api/routes/plans.py:482` imports `DevelopmentAgent` from `app.ai.services.development_agent` which does not exist.

### 3.6 Verification/Repair

```
Verification Request
  → VerificationGate.check()
    → [uses GitRunner, not providers]
```

**Provider Hierarchy Used:** None.

---

## 4. Provider Hierarchy Comparison

| Aspect | `app/ai/providers/` | `app/ai_manager/providers/` |
|--------|---------------------|---------------------------|
| **Status** | Active production | Dead code |
| **Base Interface** | `AIProvider(ABC)` | `AIManagerProvider(ABC)` |
| **Methods** | `health_check()`, `list_models()`, `chat()` | `decide(intent) -> RawManagerDecision` |
| **Implementations** | OpenRouter, Ollama, Kimi, Future | OpenRouter, Ollama, Fake |
| **Wraps existing?** | N/A (primary) | OpenRouter wraps `OpenRouterProvider`; Ollama makes direct HTTP |
| **Configuration** | Via `ProviderManager` + `SettingsService` | Via constructor args only |
| **Factory** | `ProviderManager.get_provider()` | None — DI by caller |
| **Error types** | `OpenRouterProviderError`, `KimiProviderError` | `AIManagerProviderTimeoutError`, etc. |
| **Retry** | None in providers; service layer classifies | Max 3 attempts with exponential backoff |
| **Streaming** | Not supported | Not supported |
| **Health checks** | Yes (rich diagnostics for Ollama) | No |
| **Tool calling** | Yes (via `normalize_tool_definitions`) | Yes (forced single tool call) |
| **Structured output** | Via function calling | Via forced tool call `ai_manager.propose_decision` |
| **Tests** | Extensive (unit + integration) | Extensive (unit + integration) |
| **Production usage** | Yes — `AIService`, `DevelopmentAgentFactory` | No — only tests |

---

## 5. Autonomous Developer Analysis

### 5.1 Provider Architecture

The Autonomous Developer uses **two distinct paths**:

1. **Coding Path (via `CodingProvider`):**
   - Uses `app/ai/providers/` coding subsystem
   - `BaseDevelopmentAgent` wraps `CodingProvider`
   - `ProviderManager.get_coding_provider()` creates adapter
   - Uses `CodingPromptBuilder` for prompts
   - Uses `CodingOutputParser` for parsing
   - Enforces single tool call `coding.propose_step` per response

2. **Kilo Path (subprocess wrapper):**
   - `KiloDevelopmentAgent` does NOT implement `CodingProvider`
   - Bypasses `ProviderManager.get_coding_provider()` entirely
   - Direct subprocess execution of Kilo CLI
   - No provider abstraction — Kilo handles its own model interaction

### 5.2 Is This Separation Justified?

**YES — architecturally justified.**

**Reasons:**
1. **Kilo is an external coding agent**, not a model provider. It is a separate CLI tool with its own model selection, context management, and tool execution.
2. **DevelopmentAgent is an application-level abstraction** for autonomous coding tasks. It needs a controlled, deterministic interface (`CodingProvider`) that constrains the model to a specific protocol.
3. **CodingProvider is a different abstraction from general AI providers.** It enforces:
   - Single tool call per response (`coding.propose_step`)
   - Structured operations (READ, WRITE, EDIT, TOOL_REQUEST, FINISH)
   - Path validation (no absolute paths, no traversal)
   - Content size limits
4. **Kilo bypasses this abstraction intentionally** because it is a self-contained coding agent that does not need the `CodingProvider` protocol.

**Conclusion:** The separation between `CodingProvider` (for in-process model-driven coding) and `KiloDevelopmentAgent` (for external CLI-driven coding) is intentional and architecturally sound.

---

## 6. Configuration Sources of Truth

### 6.1 Authoritative Configuration Sources

| Configuration | Source | Location |
|--------------|--------|----------|
| Provider selection | `SettingsService` | `ai_provider` key |
| Model selection | `SettingsService` | `ai_selected_model` key |
| Ollama URL | `SettingsService` | `ai_ollama_url` key |
| OpenRouter URL | `SettingsService` | `ai_openrouter_base_url` key |
| Kimi URL | `SettingsService` | `ai_kimi_base_url` key |
| OpenRouter API key | `CredentialService` (DPAPI encrypted) | `ai_openrouter_api_key_encrypted` |
| Kimi API key | `CredentialService` (DPAPI encrypted) | `ai_kimi_api_key_encrypted` |
| Kilo executable path | `SettingsService` | `kilo_executable_path` key |
| Kilo auto-approve | `SettingsService` | `kilo_auto_approve` key |
| Autonomy mode | `SettingsService` | `autonomy_mode` key |

### 6.2 No Competing Configuration Sources

**FACT:** There is only one `SettingsService` and one `CredentialService`. All provider configuration flows through them. There are no competing configuration sources.

**OBSERVATION:** `ProviderManager` is the single composition root for all provider instantiation in production. No other code directly instantiates providers except:
- `DevelopmentAgentFactory` — for Kilo (bypasses ProviderManager intentionally)
- Tests — use mocks or direct instantiation

---

## 7. Security and Privacy Analysis

### 7.1 API Key Handling

**FACT:** API keys are encrypted via DPAPI (Windows) before storage in SQLite. Decryption happens only at read time in `ProviderManager`.

**FACT:** API keys are never exposed in:
- Audit logs (redacted)
- API responses (only hints returned)
- Provider health checks (only presence/absence checked)

**OBSERVATION:** Both hierarchies handle API keys consistently:
- `app/ai/providers/` — keys passed via constructor from `ProviderManager`
- `app/ai_manager/providers/` — OpenRouter adapter calls `self._provider._headers()` (reuses same key); Ollama adapter makes unauthenticated requests

**NO SECURITY ISSUE:** No inconsistent API key handling between hierarchies.

### 7.2 Provider Switching

**FACT:** Provider switching is controlled by `SettingsService` and requires no code changes. Both hierarchies respect the configured provider.

**OBSERVATION:** Since `app/ai_manager/providers/` is dead code, provider switching in production only affects `app/ai/providers/`.

### 7.3 Model Selection

**FACT:** Model selection is stored in `SettingsService` (`ai_selected_model`). Both hierarchies receive model as a constructor argument.

**OBSERVATION:** Model selection semantics are consistent:
- `app/ai/providers/` — model passed to `provider.chat(model, ...)`
- `app/ai_manager/providers/` — model passed to adapter constructor, then to API payload

**NO SECURITY ISSUE:** No inconsistent model selection semantics.

### 7.4 External vs Local Models

**FACT:** Ollama (local) and OpenRouter/Kimi (external) are treated consistently in `app/ai/providers/`.

**OBSERVATION:** `app/ai_manager/providers/` would treat them the same way if it were active.

**NO SECURITY ISSUE:** No privacy inconsistencies between hierarchies.

### 7.5 Authorization Boundaries

**FACT:** Both hierarchies sit below `ToolExecutionService` and `PermissionEngine`. They have no access to:
- `PermissionEngine`
- `ApprovalService`
- `ToolExecutionService`

**VERIFIED:** Static analysis of `app/ai_manager/providers/` shows no imports of `app.tools`, `app.permissions`, or `app.approvals`.

**NO SECURITY ISSUE:** No bypass of authorization boundaries.

---

## 8. Test Coverage

### 8.1 `app/ai/providers/` Tests

| Test File | Coverage |
|-----------|----------|
| `test_ai.py` | Health checks, model listing, chat, config persistence, API key leakage, retry |
| `test_openrouter_provider.py` | Chat with/without tools |
| `test_ollama_provider.py` | Chat with/without tools |
| `test_kimi_provider.py` | Chat with/without tools |
| `test_coding_protocol.py` | Prompt builder, output parser, provider neutrality |
| `test_coding_openrouter.py` | Coding adapter construction, validation, error propagation |
| `test_coding_ollama.py` | Coding adapter construction, validation, error propagation |
| `test_coding_agent_integration.py` | ProviderManager, DevelopmentAgentFactory, E2E |

### 8.2 `app/ai_manager/providers/` Tests

| Test File | Coverage |
|-----------|----------|
| `test_ai_manager_providers.py` | Adapter construction, retry, error mapping, security |
| `test_ai_manager_integration.py` | Full pipeline with adapters |
| `test_ai_manager.py` | AIManager with fake provider |
| `test_ai_manager_bridge.py` | ManagerPlanningService bridge |
| `test_ai_manager_gate.py` | PolicyCapabilityGate |

### 8.3 Coverage Gaps

| Gap | Severity | Notes |
|-----|----------|-------|
| No Kimi coding adapter tests | Low | Kimi is not a primary coding provider |
| No direct ProviderManager unit tests | Low | Tested indirectly via factory |
| No real provider integration tests | Low | All use `httpx.MockTransport` |
| No Kilo subprocess integration tests | Medium | Environment-blocked |

---

## 9. Future Model Router Compatibility

### 9.1 Current State

The current provider architecture can be wrapped by a future Model Router with minimal changes:

**Option A: Wrap `ProviderManager`**
- Model Router calls `ProviderManager.get_provider()` and `ProviderManager.get_coding_provider()`
- Adds routing logic on top of existing factory
- **Migration cost:** LOW
- **Impact:** Minimal — existing code continues to work

**Option B: Replace `ProviderManager`**
- Model Router becomes the new factory
- `ProviderManager` becomes a legacy wrapper
- **Migration cost:** MEDIUM
- **Impact:** Requires updating all callers

### 9.2 Dual Hierarchy Impact

**FACT:** The `app/ai_manager/providers/` hierarchy is dead code. It does not need to be reconciled for Model Router.

**RECOMMENDATION:** When Model Router is built, only wrap `app/ai/providers/`. The dead code in `app/ai_manager/providers/` can be:
- Deleted if AI Manager is never activated
- Migrated later if AI Manager is activated

### 9.3 Lock-in Analysis

**OBSERVATION:** The current `ProviderManager` is a simple factory with explicit if/elif chains. It does not use a registry, decorators, or plugin system. This makes it easy to wrap or replace.

**NO LOCK-IN:** The current architecture does not create future lock-in. A Model Router can be added as an adapter layer without changing existing providers.

---

## 10. Architectural Options

### Option A: KEEP BOTH

**Benefits:**
- No migration cost
- Dead code remains available for future AI Manager activation
- No disruption to Stage 18

**Risks:**
- Dead code creates maintenance confusion
- Two hierarchies may diverge over time
- New developers may not understand which to use

**Migration Cost:** None

**Impact on Stage 18:** None — file tools don't use providers directly

**Impact on Future Model Router:** Low — Model Router only needs to wrap `app/ai/providers/`

**Security Implications:** None

### Option B: UNIFY LATER

**Benefits:**
- Allows AI Manager to activate with minimal changes
- Preserves both hierarchies for different use cases

**Risks:**
- Dead code remains in repository
- Unification may never happen if AI Manager is never activated

**Migration Cost:** Medium — requires reconciling two different interfaces (`AIProvider` vs `AIManagerProvider`)

**Impact on Stage 18:** None

**Impact on Future Model Router:** Low — Model Router only needs `app/ai/providers/`

**Security Implications:** None

### Option C: UNIFY BEFORE STAGE 18

**Benefits:**
- Clean architecture before expanding capabilities
- Single provider hierarchy for all future tools

**Risks:**
- **Blocks Stage 18 unnecessarily** — file tools don't use providers directly
- Migration work has no immediate value
- May break existing tests for dead code

**Migration Cost:** High — requires reconciling `AIProvider` and `AIManagerProvider` interfaces, updating adapters, updating tests

**Impact on Stage 18:** **BLOCKS Stage 18** — unnecessary work before filesystem tools

**Impact on Future Model Router:** Low — same as Option A/B

**Security Implications:** None (no security improvement from unification)

### Option D: KEEP SEPARATE PERMANENTLY

**Benefits:**
- Clear separation of concerns: chat vs planning
- No migration work needed
- Each hierarchy optimized for its use case

**Risks:**
- Code duplication between adapters
- Configuration divergence risk
- Maintenance burden

**Migration Cost:** None

**Impact on Stage 18:** None

**Impact on Future Model Router:** Low — Model Router wraps `app/ai/providers/`; dead hierarchy ignored

**Security Implications:** None

### Recommendation: **OPTION A (KEEP BOTH) with DEFERRED cleanup**

**Rationale:**
1. **Stage 18 must not be blocked** by provider unification — file tools integrate with `ToolExecutionService`, not providers
2. **The dead code is not harmful** — it is isolated, tested, and does not affect production flows
3. **Unification can wait** until Model Router is built (Stage 21+)
4. **If AI Manager is never activated**, the dead code can be deleted later with no impact
5. **If AI Manager is activated**, the existing `app/ai_manager/providers/` hierarchy provides a starting point

---

## 11. Stage 18 Decision

**DECISION: PROCEED_WITH_STAGE_18**

### Why Provider Duplication Does Not Block Stage 18

1. **File tools do not use providers directly.** They integrate with `ToolExecutionService` → `PermissionEngine` → `ToolExecutor`. The provider layer is irrelevant to filesystem tool execution.

2. **The active provider hierarchy (`app/ai/providers/`) is healthy and production-ready.** It has:
   - Clear base interface (`AIProvider`)
   - Concrete implementations for OpenRouter, Ollama, Kimi
   - Coding subsystem (`CodingProvider`) for autonomous development
   - Central factory (`ProviderManager`)
   - Consistent configuration via `SettingsService` + `CredentialService`
   - Comprehensive test coverage

3. **The dead hierarchy (`app/ai_manager/providers/`) does not affect production.** It is never instantiated in any route, service, or orchestrator.

4. **No inconsistent behavior between hierarchies.** Both use the same underlying providers (OpenRouter, Ollama) with consistent error handling and retry logic.

5. **Model Router can be built later** by wrapping `ProviderManager` without touching file tools or the active provider hierarchy.

### Provider Cleanup Should Be Deferred

**DEFERRED WORK:**
- Document dead code status of `app/ai_manager/providers/` and `app/manager/providers/`
- Decide deletion vs. preservation when AI Manager activation is decided
- Reconcile hierarchies during Model Router implementation (Stage 21+)

---

## 12. Deferred Work

| Item | Priority | Trigger |
|------|----------|---------|
| Document dead code status | Low | Now |
| Decide AI Manager activation | Medium | When planning subsystem is needed |
| Delete dead code if AI Manager not activated | Low | After 6 months of inactivity |
| Reconcile provider hierarchies | Medium | During Model Router implementation (Stage 21+) |
| Add Kimi coding adapter tests | Low | When Kimi is used for coding |
| Add direct ProviderManager unit tests | Low | When config loading bugs appear |
| Fix `api/routes/plans.py` import bug | High | When planning subsystem is activated |

---

## 13. Risks and Limitations

### 13.1 Current Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Dead code confusion | Low | Document status; add deprecation warning if needed |
| `api/routes/plans.py` import bug | Medium | Fix when planning is activated |
| Kilo bypasses ProviderManager | Low | Intentional — Kilo is external CLI |
| No Kimi coding adapter | Low | Kimi not used for coding currently |

### 13.2 Limitations of This Analysis

| Limitation | Impact |
|------------|--------|
| Static analysis only — no runtime tracing | Cannot confirm zero production usage of dead code |
| Test coverage gaps for Kimi | Cannot verify Kimi behavior in all scenarios |
| No real provider integration tests | Cannot verify actual API behavior |

---

## 14. Evidence Inspected

### 14.1 Files Read — `app/ai/providers/`

| File | Lines | Purpose |
|------|-------|---------|
| `base.py` | 41 | `AIProvider` ABC, `AIModel`, `ProviderHealth` |
| `openrouter.py` | 125 | OpenRouter HTTP client |
| `ollama.py` | 282 | Ollama HTTP client + utilities |
| `kimi.py` | 114 | Kimi HTTP client |
| `future.py` | 17 | Placeholder provider |
| `coding_base.py` | 194 | `CodingProvider` ABC, operation models |
| `coding_openrouter.py` | 91 | OpenRouter coding adapter |
| `coding_ollama.py` | 91 | Ollama coding adapter |
| `coding_prompt.py` | 261 | Prompt builder |
| `coding_parser.py` | 119 | Output parser |

### 14.2 Files Read — `app/ai_manager/providers/`

| File | Lines | Purpose |
|------|-------|---------|
| `base.py` | 90 | `AIManagerProvider` ABC, error hierarchy |
| `openrouter.py` | 213 | OpenRouter adapter with retry |
| `ollama.py` | 215 | Ollama adapter with retry |
| `fake.py` | 44 | Test double |

### 14.3 Files Read — `app/manager/providers/`

| File | Lines | Purpose |
|------|-------|---------|
| `openrouter.py` | — | Planning adapter (dead code) |
| `base.py` | — | `PlanProvider` ABC (dead code) |

### 14.4 Files Read — Consumers

| File | Purpose |
|------|---------|
| `services/core/app/ai/services/provider_manager.py` | Central provider factory |
| `services/core/app/ai/services/ai_service.py` | Normal AI chat |
| `services/core/app/ai_manager/ai_manager.py` | AI Manager (dead code) |
| `services/core/app/orchestration/agent_factory.py` | DevelopmentAgent factory |
| `services/core/app/orchestration/agents/base_agent.py` | BaseDevelopmentAgent |
| `services/core/app/orchestration/agents/kilo_agent.py` | KiloDevelopmentAgent |
| `services/core/app/api/routes/plans.py` | Plans route (has import bug) |

### 14.5 Files Read — Tests

| File | Purpose |
|------|---------|
| `tests/test_ai.py` | Provider integration tests |
| `tests/test_openrouter_provider.py` | OpenRouter unit tests |
| `tests/test_ollama_provider.py` | Ollama unit tests |
| `tests/test_kimi_provider.py` | Kimi unit tests |
| `tests/test_coding_protocol.py` | Coding prompt/parser tests |
| `tests/test_coding_openrouter.py` | OpenRouter coding adapter tests |
| `tests/test_coding_ollama.py` | Ollama coding adapter tests |
| `tests/test_coding_agent_integration.py` | Factory + E2E tests |
| `tests/test_ai_manager_providers.py` | Manager provider tests |
| `tests/test_ai_manager_integration.py` | Manager integration tests |
| `tests/test_ai_manager.py` | AIManager tests |
| `tests/test_ai_manager_bridge.py` | Bridge tests |
| `tests/test_ai_manager_gate.py` | Gate tests |

### 14.6 Static Analysis

| Check | Result |
|-------|--------|
| `AIManager(` instantiation in production | **ZERO** — only in tests |
| `PlanManager(` instantiation in production | **ZERO** — only in tests |
| `app.ai_manager.providers` imports outside tests | Only `ai_manager/__init__.py` and test files |
| `app.manager.providers` imports outside tests | **ZERO** |
| Direct provider instantiation in production | Only via `ProviderManager` |
| `KiloDevelopmentAgent` instantiation | In `agent_factory.py` — intentional bypass |

---

## 15. Final Status

**STAGE_18_1A_CLARIFICATION_COMPLETE**

**Recommendation:** KEEP BOTH hierarchies; defer cleanup. PROCEED_WITH_STAGE_18.

**Files Created:**
- `STAGE_18_1A_PROVIDER_ARCHITECTURE_CLARIFICATION.md` (this document)

**Production Files Modified:**
- None — this is a discovery-only stage

**Tests Executed:**
- None — no tests needed for documentation

**Git Status:**
- Pre-existing modifications present (from earlier stages)
- New untracked file: `STAGE_18_1A_PROVIDER_ARCHITECTURE_CLARIFICATION.md`
- No production code changes
