# Stage 10.9 — Autonomous Development Reliability & Environment Hardening
## Part 1: Discovery | Part 2: Regression Classification

**Date:** 2026-09-17  
**Scope:** services/core/app orchestration, workspace, tools, verification, tasks, repositories  
**Test Run:** 1602 passed, 15 failed, 2 skipped, 1 warning in 364.21s

---

## 1. File Inventory & Key Interfaces

| File | Status | Key Interfaces / Invariants |
|------|--------|----------------------------|
| `orchestration/autonomous_plan_executor.py` | **MISSING** | Expected but not present in tree |
| `orchestration/plan_executor.py` | **MISSING** | Expected but not present in tree |
| `orchestration/kilo_session_manager.py` | ✅ | Session CRUD, recovery eligibility (10 checks), orphan detection with process identity verification (`_verify_process_identity`), JSON event parsing (diagnostics only). **Security invariant:** never kills arbitrary PIDs. |
| `workspace/workspace_manager.py` | ✅ | Worktree creation (with ownership marker + git config), checkpoint (explicit staging), rollback (hard reset scoped to autonomous wt only), removal (fail-closed ownership check). **7 critical safety invariants enforced.** |
| `tools/tool_execution_service.py` | ✅ | `execute_tool_call()` enforces: definition lookup → enabled check → arg validation → PermissionEngine → execution → result validation → audit. **PermissionEngine ALWAYS evaluated before execution.** |
| `ai_manager/approval_service.py` | ✅ | `CapabilityApprovalService` with PENDING → APPROVED/DENIED/CANCELLED → CONSUMED state machine. Atomic consumption; lazy expiration; fail-closed. |
| `tools/permission_engine.py` | ✅ | 11-rule fail-closed evaluation (tool existence, enabled, action, grants, confirmation, autonomy mode, default deny). Approval override can only upgrade ASK_USER → ALLOW; CRITICAL is absolute deny. |
| `verification/check_registry.py` | ✅ | Ordered, duplicate-rejecting, lockable container. Thread-safe via `threading.Lock`. Not a singleton. |
| `verification/check_runner.py` | ✅ | `VerificationCheck` Protocol (check_id, check_name, required, execute). `CheckExecutor` restricted subprocess runner with `_ALLOWED_EXECUTABLES` whitelist (15 executables). No shell=True, no arbitrary commands. |
| `orchestration/agents/base_agent.py` | ✅ | Coding loop: `plan_next_step` → validate → WorkspaceWriter / ToolExecutionService → repeat. Enforces `max_operations`, `max_total_writes`, `max_content_bytes`. Cancel/abort events checked each iteration. |
| `orchestration/repair_diagnostics.py` | ✅ | `format_verification_feedback()` produces length-bounded (4096 char) diagnostic text. Exposes no secrets or untrusted paths. |
| `tasks/state_machine.py` | ✅ | Authoritative transition matrix (PENDING→RUNNING→...). Atomic CAS via `transition_status_atomically()`. `claim_attempt()` is single SQL UPDATE asserting status + attempt_count < max. `transition_recovery()` with lease-guarded ownership. |
| `repositories/task_repository.py` | ✅ | Atomic CAS updates, `claim_task_attempt` (single SQL UPDATE), `claim_task_recovery` (lease-guarded), `create_tasks_idempotent` (fingerprint + ON CONFLICT DO NOTHING). `_retry_on_locked` helper defined. |

---

## 2. Confirmed Strengths

- **Workspace safety is best-in-class.** `WorkspaceManager` has 7 explicit safety invariants, never touches primary worktree destructively, uses explicit `intended_paths` staging (no `git add .`), and validates worktree registration via `git worktree list --porcelain`.
- **Permission engine is fail-closed by design.** 11 ordered rules, CRITICAL is absolute deny regardless of grants/approval, audit is fail-soft and cannot alter decisions.
- **State transitions are atomic and authoritative.** `TaskStateMachine` is the sole domain authority; all status mutations go through single-statement SQL UPDATEs with embedded assertions.
- **Process identity is verified before termination.** `KiloSessionManager` checks process start time (Windows: wmic; Linux: ps) before killing PIDs, with 5-second tolerance window.
- **Verification subprocess is locked down.** `CheckExecutor` whitelists 15 executables, never uses shell=True, enforces cwd as absolute non-primary path, caps timeout at 300s.

---

## 3. Concrete Reliability Risks Found

| Risk | Location | Severity |
|------|----------|----------|
| Direct access to repository private `_session` attribute | `kilo_session_manager.py:115-117, 168-170, 404-406` | Medium — breaks encapsulation; repository should expose commit/execute methods |
| Direct access to `writer._changed_files` (private) | `base_agent.py:150, 162` | Low — tight coupling to implementation detail |
| `.strip()` called on domain fields without null checks | `task_repository.py:67-68, 750-751` | Medium — `task.parent_task_id.strip()` will raise `AttributeError` if `parent_task_id` is `None` |
| `path.resolve()` docstring claims `strict=True` but default is `strict=False` | `workspace_manager.py:98` | Low — misleading; non-existent paths won't raise as docstring claims |
| `_resolve_safe` does not actually validate path existence | `workspace_manager.py:90-101` | Low — callers must check `.exists()` themselves; risk of confusing downstream errors |
| `subprocess.run` in async methods blocks event loop | `kilo_session_manager.py:423-484` | Medium — 5-10s blocking calls in async context; no `run_in_executor` |
| `_retry_on_locked` defined but not used by key methods | `task_repository.py:37-53` | Low — dead code or incomplete retry coverage |
| `execute_tool_call` returns raw result without catching `CancelledError` | `tool_execution_service.py:234` | Medium — asyncio task cancellation may leave audit trail incomplete |
| `parse_kilo_json_events` uses `__import__("json")` inline | `kilo_session_manager.py:510, 522` | Low — obscures dependency; standard `import json` is clearer |
| `CheckExecutor` allows `extra_env` injection | `check_runner.py:207, 259-260` | Low-Medium — environment variable injection could manipulate check behavior |
| `PermissionEngine.evaluate` re-fetches `tool_def` after `_compute_decision` returns | `permission_engine.py:87-93` | Low — minor inefficiency; tool_def already fetched in `_compute_decision` |
| `approval_service.py` uses `datetime.now(timezone.utc)` directly instead of injecting clock | `approval_service.py:89` | Low — hard to test time-dependent TTL logic |

---

## 4. Environmental / Test Fragility Found

- **15 of 1602 tests fail.** 2 are environment-specific (Kilo binary path), 1 is external-service-dependent (503 from AI provider), 12 are product defects in the AI provider config/chat endpoints.
- **Tests assume `httpx.AsyncClient` monkeypatch intercepts all HTTP calls.** Some tests still receive real 503/422 responses, indicating module-level singletons or alternative HTTP clients bypass the mock.
- **Tests share global config state via `client` fixture.** Config set in one test leaks to subsequent tests, causing cascade failures (e.g., `test_kimi_status_and_model_listing` sees `ollama` instead of `kimi` because the prior config PUT returned 422).
- **Kilo binary tests are Windows-path-specific** (`test_e2e_autonomous_coding.py:674-687`) and will fail on any machine without that exact VS Code extension version installed.
- **No test cleanup for `/api/ai/config` state.** Tests that PUT config do not reset it, causing cross-test contamination.

---

## 5. Real Architectural Defects Found

| Defect | Evidence |
|--------|----------|
| `autonomous_plan_executor.py` and `plan_executor.py` are missing | Files do not exist at documented paths; orchestration layer is incomplete |
| Config endpoint returns 422 for valid Kimi/OpenRouter configs | `test_ai.py:162, 264` — PUT `/api/ai/config` with valid provider config rejected |
| Config does not persist / provider does not switch | `test_ai.py:186, 312` — status endpoint returns stale `ollama` after setting `kimi`/`openrouter` |
| Chat response missing `conversation` key | `test_ai.py:346` — `KeyError: 'conversation'` indicates conversation creation is broken |
| Error code mapping is wrong (ai_not_ready instead of rate_limited/model_unavailable) | `test_ai.py:380, 402` — structured error responses use wrong error codes |
| Clearing API key does not work | `test_ai.py:282` — `openrouter_api_key_configured` remains `True` after submitting empty string |
| `/api/ai/models` returns 503 under mock | `test_ai.py:57` — endpoint returns 503 even when httpx is mocked |
| Retry after 429 does not create conversation record | `test_ai.py:435` — `len(convs) == 0` indicates failed chat calls are not persisted |
| OpenRouter regression tests in ollama-named test files | `test_ollama_diagnostics.py:211`, `test_ollama_model_management.py:242` — test files named for Ollama contain OpenRouter/Kimi regression checks, suggesting copy-paste organization |
| `task_repository.py` unsafe `.strip()` on potentially-None fields | `task_repository.py:67-68, 750-751` — will crash on `None` values |
| `task_repository.py` has three overlapping atomic-update methods | `transition_status_atomically`, `claim_task_attempt`, `transition_recovery_status_atomically` — duplicated WHERE + VALUES patterns |
| `kilo_session_manager.py` bypasses repository abstraction | Direct SQL execution via `self._session_repo._session.execute(stmt)` |

---

## 6. False-Positive Test Assumptions

| Assumption | Why It Fails |
|------------|--------------|
| Monkeypatching `httpx.AsyncClient` intercepts all outgoing HTTP | Module-level singletons or cached clients bypass the monkeypatch; some tests still receive real 503 responses |
| `client` fixture provides test isolation | Config state persists across tests; no teardown/reset between tests |
| 422 response means "test is wrong" | 422 is a valid product defect (endpoint rejecting valid config); test correctly identifies broken behavior |
| `test_kilo_binary_exists` is a unit test | It is an environment-dependent smoke test; should be skipped or marked `xfail` on non-Windows or when Kilo is absent |

---

## 7. Missing High-Value Coverage

- **No tests for `autonomous_plan_executor.py` / `plan_executor.py`** — these files are entirely absent from the tree.
- **No tests for `repair_diagnostics.py` prompt-injection defense** — the docstring warns about injection but there are no tests verifying that injected text in verification output is neutralized.
- **No concurrent-recovery tests for `KiloSessionManager`** — `check_recovery_eligibility` and `enforce_recovery_before_execution` have no concurrency test coverage.
- **No tests for `PermissionEngine` concurrent one-time grant consumption** — `consume_one_time_grant_atomically` race conditions are untested.
- **No tests for `WorkspaceManager` symlink attack resistance** — `_resolve_safe` and `_assert_not_inside` are security-critical but lack symlink-target tests.
- **No tests for `task_repository.create_tasks_idempotent` under concurrent insert** — the idempotency claim is untested under real concurrency.
- **No tests for `BaseDevelopmentAgent` cancel/abort mid-operation** — the loop checks events but there are no tests verifying prompt termination.

---

## 8. Unnecessary Complexity

- **`_resolve_safe` + `_resolve_safe_nonexistent` in `workspace_manager.py`** — two near-identical functions; could be unified with a `must_exist: bool` parameter.
- **Three overlapping atomic-update methods in `task_repository.py`** — `transition_status_atomically`, `claim_task_attempt`, `transition_recovery_status_atomically` share ~70% identical SQL patterns; could use a generic `cas_update` helper.
- **`BaseVerificationCheck.execute` wraps `_run` with timing + error handling** — adds indirection; `execute` could be the concrete implementation and `_run` removed.
- **`CheckCommandResult` is a frozen dataclass with 5 fields** — used only by `CheckExecutor`; could be a simpler namedtuple or inline return.

---

## 9. Opportunities for Simplification

1. **Merge `_resolve_safe` and `_resolve_safe_nonexistent`** into a single `_resolve(path: Path, must_exist: bool = True) -> Path`.
2. **Extract common CAS update helper** in `task_repository.py` to eliminate duplicated `update(...).where(...).values(...).execute()` boilerplate.
3. **Make `PermissionEngine` rule evaluation data-driven** — replace 11 early-return if/elif blocks with a list of `(condition, decision_factory)` tuples.
4. **Remove direct `_session` access** from `KiloSessionManager` — add `execute_raw(stmt)` or similar to `KiloSessionRepository` to preserve encapsulation.
5. **Replace `writer._changed_files` access** in `base_agent.py` with a public `get_changed_files_count()` method on `WorkspaceWriter`.
6. **Add null-safe `.strip()`** in `task_repository.py` — use `(task.parent_task_id or "").strip()` pattern consistently.
7. **Make `subprocess.run` non-blocking** in `kilo_session_manager.py` — wrap in `asyncio.to_thread()` or `loop.run_in_executor()`.

---

## 10. Regression Classification Table

| # | Test | Failure | Classification | Rationale |
|---|------|---------|----------------|-----------|
| 1 | `test_ai.py::test_ai_models_success` | `assert 503 == 200` at line 57 | **D. EXTERNAL SERVICE FAILURE** | `/api/ai/models` returns 503 from upstream AI provider (Ollama or equivalent); mock monkeypatch does not intercept the call, indicating a real HTTP response from an external service |
| 2 | `test_ai.py::test_kimi_config_does_not_leak_api_key` | `assert 422 == 200` at line 162 | **B. PRE-EXISTING PRODUCT DEFECT** | PUT `/api/ai/config` with valid Kimi config returns 422 Unprocessable Entity; endpoint validation rejects valid input |
| 3 | `test_ai.py::test_kimi_status_and_model_listing` | `assert 'ollama' == 'kimi'` at line 186 | **B. PRE-EXISTING PRODUCT DEFECT** | Provider status remains `ollama` after config PUT; config either not persisting or status endpoint returning stale data |
| 4 | `test_ai.py::test_openrouter_config_does_not_leak_api_key` | `assert 422 == 200` at line 264 | **B. PRE-EXISTING PRODUCT DEFECT** | Same root cause as #2; OpenRouter config endpoint rejects valid input |
| 5 | `test_ai.py::test_openrouter_clear_api_key` | `assert False is True` at line 282 | **B. PRE-EXISTING PRODUCT DEFECT** | Submitting empty string for API key does not clear it; `openrouter_api_key_configured` remains `True` |
| 6 | `test_ai.py::test_openrouter_chat_flow_and_free_router_selection` | `assert 'ollama' == 'openrouter'` at line 312 | **B. PRE-EXISTING PRODUCT DEFECT** | Provider status shows `ollama` instead of `openrouter`; config state not updating |
| 7 | `test_ai.py::test_provider_switching_preserves_conversation_history` | `KeyError: 'conversation'` at line 346 | **B. PRE-EXISTING PRODUCT DEFECT** | Chat response missing `conversation` key; conversation creation is broken |
| 8 | `test_ai.py::test_openrouter_chat_structured_error_on_429` | `assert 'ai_not_ready' == 'rate_limited'` at line 380 | **B. PRE-EXISTING PRODUCT DEFECT** | Error code mapping returns `ai_not_ready` instead of `rate_limited` for 429 responses |
| 9 | `test_ai.py::test_openrouter_chat_structured_error_on_503` | `assert 'ai_not_ready' == 'model_unavailable'` at line 402 | **B. PRE-EXISTING PRODUCT DEFECT** | Error code mapping returns `ai_not_ready` instead of `model_unavailable` for 503 responses |
| 10 | `test_ai.py::test_openrouter_chat_retry_does_not_duplicate_user_message` | `assert 0 == 1` at line 435 | **B. PRE-EXISTING PRODUCT DEFECT** | No conversations created; failed chat calls are not persisted or list endpoint is broken |
| 11 | `test_ai.py::test_two_identical_prompts_normally_sent_creates_two_messages` | Failure at line 471+ | **B. PRE-EXISTING PRODUCT DEFECT** | Likely cascades from broken config/chat infrastructure; exact assertion not shown but pattern matches other product defects |
| 12 | `test_e2e_autonomous_coding.py::TestKiloProviderReality::test_kilo_binary_exists` | `assert False` (Path missing) at line 677 | **C. ENVIRONMENTAL FAILURE** | Hard-coded Windows path `C:\Users\adamz\.vscode\extensions\kilocode.kilo-code-7.6.2-win32-x64\bin\kilo.exe` does not exist on this machine |
| 13 | `test_e2e_autonomous_coding.py::TestKiloProviderReality::test_kilo_version` | `FileNotFoundError` at line 681-687 | **C. ENVIRONMENTAL FAILURE** | Same root cause as #12; subprocess cannot find `kilo.exe` |
| 14 | `test_ollama_diagnostics.py::test_openrouter_regression_check` | `assert 'ollama' == 'openrouter'` at line 211 | **B. PRE-EXISTING PRODUCT DEFECT** | Provider status returns `ollama` instead of `openrouter`; test file named for Ollama contains OpenRouter regression, indicating organizational confusion |
| 15 | `test_ollama_model_management.py::test_kimi_regression_check` | `assert 409 == 200` at line 242 | **B. PRE-EXISTING PRODUCT DEFECT** | Chat endpoint returns 409 Conflict; likely because config endpoint failed earlier (same 422 pattern as #2/#4) |

---

## 11. Summary of Stage 10.9 Impact

**Readiness:** The core reliability infrastructure (workspace safety, permission engine, state machine, verification gate) is **production-grade**. The security invariants are well-documented and enforced.

**Blockers:**
1. **AI provider integration layer is broken.** 12 tests in `test_ai.py` plus 2 in Ollama-named files reveal systematic defects in config persistence, provider switching, error code mapping, and conversation creation. These are product defects, not test defects.
2. **Missing orchestration files.** `autonomous_plan_executor.py` and `plan_executor.py` do not exist, indicating an incomplete implementation.
3. **Test isolation is insufficient.** Shared config state and unreliable httpx monkeypatching create cascade failures and mask true product behavior.
4. **Repository encapsulation is violated.** `KiloSessionManager` reaches into private `_session` attributes, and `BaseDevelopmentAgent` reaches into private `_changed_files`.

**Recommendation for Stage 10.9:**
- Fix the 12 product defects in the AI provider layer (config endpoint, status endpoint, error mapping, conversation creation).
- Add test isolation (reset config state between tests, fix httpx monkeypatch to intercept singletons).
- Add the missing orchestration files or remove their references.
- Replace private-attribute access with proper repository/agent public APIs.
