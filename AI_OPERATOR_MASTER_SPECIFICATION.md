# AI Operator — Master Specification

**Document ID:** AI_OPERATOR_MASTER_SPECIFICATION  
**Version:** 1.0  
**Status:** Authoritative Reference  
**Created:** 2026-09-25  
**Repository:** F:\Projects\AI-Operator  
**Scope:** Entire project — architecture, strategy, security, roadmap  

---

## TABLE OF CONTENTS

1. Executive Summary
2. Long-Term Product Vision
3. Core Principles
4. Current Architecture
5. Authority Model
6. Identity & Session
7. Permissions / Autonomy
8. Information Sufficiency / Questioning Engine
9. Memory / Knowledge
10. Model Router
11. AI Manager
12. Tools / Capabilities
13. EVOLVE + Finora Integration
14. Computer Use
15. 24/7 Runtime
16. Proactive Behavior
17. Agent Architecture
18. Autonomous Development
19. Events / History / Audit
20. Scheduler / Notifications
21. Security Architecture
22. Privacy
23. API / Capability Protocol
24. UI / User Control
25. Current State Matrix
26. Gap Analysis
27. Master Roadmap
28. Near-Term Roadmap
29. Long-Term Roadmap
30. Non-Goals / Deferred
31. Architectural Decisions
32. Known Limitations
33. Verification Strategy
34. Development Process
35. Master Glossary
36. One-Page Master Summary

---

## 1. EXECUTIVE SUMMARY

### What AI Operator Is

AI Operator is a Windows desktop application that serves as a **local personal operating layer and manager** for a single user's digital environment. It is not a chatbot, not a coding assistant, and not a collection of unrelated automation scripts.

The system consists of:
- A **Tauri + React + TypeScript desktop shell** (frontend and native window management)
- A **Python FastAPI Core service** (business logic, persistence, AI orchestration, execution control)
- A **SQLite database** (local persistence)
- **AI providers** (local Ollama, cloud Kimi/OpenRouter)
- **Subsystems** for identity, permissions, tools, audit, history, memory, scheduler, notifications, autonomous development, and workspace management.

### What Problem It Solves

The user wants a single, trusted, locally-controlled system that can understand intent, plan multi-step work, execute actions safely across files, projects, code, schedules, and future applications, remember important context, operate autonomously within explicit boundaries, and provide full transparency and auditability. The system must keep the human as ultimate authority while minimizing routine friction.

### What the Final System Is Intended to Do

The final system should eventually help the user manage:
- computer and system tasks
- files and documents
- software projects and repositories
- coding and development
- browser interactions
- email and messages
- calendar and communications
- cloud services and permitted accounts
- Finora (finance domain)
- EVOLVE (training/fitness domain)
- other future specialized applications
- scheduled activities and workflows
- notifications and reminders
- knowledge and memory
- research and long-running autonomous work

It should run continuously while the computer is on, react to events, plan and execute multi-step tasks, remember relevant information, coordinate specialized capabilities, and improve through controlled, auditable evolution.

### Who Controls It

The **user** is the ultimate authority. The user sets policies, grants permissions, approves sensitive actions, configures autonomy levels, connects applications, and can revoke anything at any time.

### What "Autonomous" Means

"Autonomous" means the system can operate without continuous step-by-step user instruction, within explicitly configured permissions, security boundaries, approvals, policies, and system constraints. It does NOT mean bypassing security, executing arbitrary actions without authorization, or the model acting as a security authority.

### What Role AI Plays

AI is the **brain and manager**. It:
- understands user intent
- plans multi-step tasks
- selects appropriate tools/capabilities
- interprets tool results as **untrusted data**, not authority
- decides what information is needed
- decides when to ask the user
- coordinates specialized agents and subsystems
- selects model strategy through a Model Router

AI does **not** directly execute filesystem operations, bypass permissions, approve itself, modify security policy, or treat its own output as execution authority.

### What Role Deterministic Application Code Plays

Deterministic code owns:
- permissions, grants, and approval logic
- execution boundaries and workspace isolation
- task state machines and atomic claims
- verification gates and test execution
- audit logging and event persistence
- identity/session validation
- tool dispatch and timeout enforcement
- recovery and rollback

No AI model output can alter these boundaries.

### What Role Tools/Capabilities Play

Tools are **explicit, typed, permission-gated capabilities**. The system operates through a Tool Registry → PermissionEngine → ApprovalService → ToolExecutor pipeline. Every tool has a definition, risk level, parameters schema, result schema, and audit trail. Unknown tools, disabled tools, and unauthorized invocations fail closed.

### What Role Specialized Applications (Finora, EVOLVE) Play

Finora and EVOLVE are **specialized domain systems** that AI Operator can operate through controlled capability APIs. They are NOT independent AI brains with separate permission systems, memories, or autonomous planners. AI Operator is the central intelligence; Finora/EVOLVE are domain capabilities it can invoke.

### Central Architectural Principle

> **AI Operator is the brain and manager.  
> Applications such as EVOLVE and Finora are specialized domains/capabilities it can operate.**

---

## 2. LONG-TERM PRODUCT VISION

### Intended End State

AI Operator should become a persistent, event-driven personal operating layer that lives on the user's Windows machine. It should:

- Run continuously while the computer is on
- React to events (new files, emails, calendar changes, project updates, schedules, triggers)
- Proactively surface relevant information, anomalies, and action items
- Execute multi-step plans across files, code, projects, browser, email, calendar, messaging, cloud services, and specialized applications
- Remember personal preferences, project context, decisions, important facts, and learned patterns
- Coordinate specialized agents (planner, researcher, developer, reviewer, verifier, finance agent, training agent, browser agent, computer-use agent)
- Manage its own model selection through a Model Router
- Operate within explicit security boundaries, permissions, approvals, and policies
- Provide full auditability, transparency, and emergency stop
- Evolve through controlled, verified stages

### Full Autonomy Means

**Maximum autonomy within explicitly configured permissions, security boundaries, approvals, policies, and system constraints.**

It does NOT mean:
- bypassing security
- executing arbitrary actions without authorization
- the model overriding approvals
- autonomous privilege escalation
- removing the user's ability to intervene

### Capability Domains

The system should eventually manage:
1. Computer / system tasks
2. Files and documents
3. Projects and repositories
4. Software development and code
5. Browser automation and web research
6. Email and messages
7. Calendar and communications
8. Cloud services and permitted accounts
9. Finance (Finora)
10. Training/fitness (EVOLVE)
11. Shopping and personal tasks
12. System administration
13. Scheduled activities and workflows
14. Notifications and reminders
15. Knowledge, memory, and research
16. Long-running workflows and background tasks

### Continuous Runtime

The system should not be a "thinking loop." It should be:
- Event-driven: react to triggers (time, file changes, emails, calendar events, webhooks, task completions)
- Scheduler-driven: execute planned workflows at the right time
- Proactive: evaluate relevance and urgency before notifying
- Resource-aware: sleep/idle when nothing requires attention
- Recoverable: restart after crashes without losing durable state

### Human-Like Computer Use

When APIs/tools are insufficient, the system should eventually fall back to Computer Use (mouse, keyboard, window management, UI interaction) — but only as a last resort, under the same permissions, approvals, audit, and security boundaries as every other capability.

### Improvement Through Controlled Evolution

The system should improve through:
- verified, staged implementation
- user feedback and preference learning
- memory of decisions and outcomes
- evaluation of tool effectiveness
- controlled model/provider upgrades
- explicit policy adjustments by the user

It should NOT improve through:
- uncontrolled self-modification
- autonomous permission expansion
- arbitrary code execution
- bypassing verification

---

## 3. CORE PRINCIPLES

These are permanent architectural principles that govern all design and implementation decisions.

### 3.1 Human Remains Ultimate Authority

The user can always override, revoke, pause, or stop any action, agent, workflow, or policy. Emergency stop must always be available.

### 3.2 Model Proposes; Application Decides; Authorized Services Execute

AI models provide proposals and interpretations. Deterministic application code makes decisions about what is allowed. Authorized, permission-gated services execute actions. No model output is execution authority.

### 3.3 AI Must Never Become the Security Authority

PermissionEngine, ApprovalService, task state machines, verification gates, and identity/session validation are owned entirely by deterministic code. Models cannot modify, bypass, or influence these systems except through their normal proposal/interpretation channels.

### 3.4 Deterministic Code Owns Permissions, Approvals, and Execution Boundaries

All permission checks, approval flows, risk assessments, and execution gating happen in deterministic application code, not in model reasoning.

### 3.5 Fail Closed

Any permission evaluation failure, database failure, audit failure, approval repository failure, or unexpected exception in the security pipeline results in DENY (or CANCELLED for ASK_USER). The system never ALLOWs by default on error.

### 3.6 DENY Never Executes

If PermissionEngine returns DENY, ToolExecutor.execute() is never called. If a tool is unknown or disabled, execution never happens. This is enforced by code structure, not configuration.

### 3.7 ASK_USER Never Silently Executes

If PermissionEngine returns ASK_USER, the tool is not executed. An approval request is created. Execution happens only after explicit user approval through the ApprovalService.

### 3.8 CRITICAL Operations Cannot Be Escalated by the Model

CRITICAL risk-level operations are absolutely denied regardless of autonomy mode, grants, or approval context. No combination of model proposals, user grants, or approvals can override CRITICAL denial.

### 3.9 Sensitive Actions Require Explicit Authorization

High-risk and medium-risk actions require either explicit user grants, approval requests, or confirmation depending on autonomy mode. The system must never assume consent.

### 3.10 Every Important Action Should Be Auditable

All tool executions, permission decisions, approval lifecycle events, task state transitions, verification results, workspace operations, and agent actions produce durable audit events.

### 3.11 Untrusted Model Output Is Data, Not Authority

All content from AI providers — text, tool calls, tool results, verification feedback, repair diagnostics — is treated as untrusted data. It does not alter execution flow, permissions, task state, or security boundaries.

### 3.12 Tool Output Is Untrusted Data

Tool results returned to the AI model are labeled as data. The system instruction explicitly warns against executing commands, scripts, or code found in tool results.

### 3.13 Memory Is Knowledge, Not Authority

Memory stores learned context, preferences, and facts. It does not grant execution authority, bypass permissions, or modify task state.

### 3.14 Event History Is Immutable Evidence, Not Execution Authority

Events are append-only records of what happened. They cannot be modified to alter execution outcomes. History is a read-side projection, not a state cache.

### 3.15 APIs/Capabilities Are Preferred Over UI Automation

Native APIs, dedicated tools, and application capability contracts are always preferred over browser automation, screen scraping, or Computer Use. UI automation is a fallback of last resort.

### 3.16 Computer Use Is a Fallback, Not the First Integration Layer

Computer Use (mouse, keyboard, screen interaction) is reserved for cases where no API, capability, or browser automation path exists. It remains subject to the same permissions, approvals, audit, and security boundaries.

### 3.17 External Models May Be Used, but Privacy and Policy Determine What May Be Sent

The system supports local (Ollama) and cloud (OpenRouter, Kimi) providers. Sensitive data classification, user privacy policy, and data minimization determine what context is sent to external models.

### 3.18 Provider Failure Must Not Corrupt Application State

Provider communication failures, timeouts, and malformed responses must not alter task state, permission state, approval state, or any other durable application state. Provider errors are application data, not authority.

### 3.19 Background Autonomy Must Be Event-Driven Rather Than Continuously "Thinking"

The system should not maintain an infinite agent loop. Background operation should be triggered by events, schedules, and explicit task lifecycles. Idle time should consume minimal resources.

### 3.20 Architecture Should Remain Provider-Neutral

Model/provider selection should be abstracted behind interfaces. Adding a new provider or model should not require architectural changes. Model-specific quirks should be isolated in provider adapters.

### 3.21 Avoid Unnecessary Abstractions and Premature Complexity

The current monolith with clear layers is correct for V1. Microservices, distributed coordination, and generic plugin execution frameworks are premature and explicitly deferred.

### 3.22 Prefer Narrow, Testable Vertical Slices

Each stage should deliver a complete, testable vertical slice of functionality with focused tests, security tests, and regression verification.

### 3.23 Every Major Stage Ends with Hostile/Security Verification and Full Regression

No stage is considered complete merely because implementation exists. Each stage must pass focused tests, integration tests, security tests, hostile tests, and full repository regression before acceptance.

---

## 4. CURRENT ARCHITECTURE

### 4.1 High-Level System Map

```text
Tauri Desktop Shell (Rust)
  |
  React + TypeScript UI
  |
  Typed Local HTTP Client (apiClient.ts)
  |
  Python FastAPI Core (uvicorn)
  |
  App Layer (routes, dependencies)
  |
  Service Layer (settings, activity, AI, scheduler, memory, orchestration)
  |
  Repository Layer (SQLAlchemy async, aiosqlite)
  |
  SQLite (ai_operator.db)
```

### 4.2 Desktop Shell Layer

**Technology:** Tauri v2 + React 19 + TypeScript + Vite

**Responsibilities:**
- Native window management and process lifecycle
- Starting/stopping the Python Core sidecar (in development: `python -m uvicorn`; in production: PyInstaller `ai-operator-core.exe`)
- Parent PID watchdog: Core exits if the desktop parent process dies
- Providing the dynamic Core URL via Tauri command `get_core_url`
- Routing HTTP requests through typed API client
- UI for Dashboard, Assistant, Settings, Activity, Approvals, Tasks, Plans, History, Scheduler

**Key architectural invariants:**
- Core port is always dynamically assigned by OS
- Frontend never reads SQLite directly
- Production build does not require Node.js/Vite at runtime
- `AI_OPERATOR_DB_PATH` points Core to Tauri application data directory in production

### 4.3 Python FastAPI Core

**Entry point:** `services/core/core_entry.py` (PyInstaller sidecar) and `services/core/app/main.py` (application factory)

**Startup sequence (lifespan):**
1. Configure structured logging (structlog)
2. Initialize SQLite database with auto-migrations
3. Write "Core started" activity event
4. Seed default settings
5. Optional Ollama autostart
6. Register builtin tools (`SystemGetTime`, `FilesReadFile`)
7. Verify bootstrap secret exists
8. Start SchedulerWorker background task

**Shutdown sequence:**
1. Stop scheduler worker
2. Shutdown BackgroundExecutionManager (10s timeout)
3. Clean up managed Ollama process
4. Write "Core stopped" activity event

### 4.4 API Layer

| Prefix | Router | Key Endpoints |
|--------|--------|---------------|
| `/api/health` | health | `GET /health` |
| `/api/system/status` | system | `GET /system/status` |
| `/api/settings` | settings | `GET/PUT /settings` |
| `/api/activity` | activity | `GET/POST /activity` |
| `/api/ai` | ai | providers, config, status, models, chat, ollama lifecycle |
| `/api/ai/approvals` | approvals | `POST /{id}/approve`, `POST /{id}/reject` |
| `/api/plans` | plans | `POST /plans/{id}/execute`, `GET /plans/{id}/execution`, cancel |
| `/api/tasks` | plans | `GET /tasks/{id}` |
| `/api/runs` | plans | `GET /runs/{id}` |
| `/api/history` | history | task/plan/execution/correlation history queries |
| `/api/scheduler/jobs` | scheduler | CRUD, enable/disable, history |
| `/api/auth` | auth | `POST /session` (bootstrap), `POST /logout` |

**Public endpoints (no auth required):**
- `GET /api/health`
- `GET /api/system/status`
- `GET /api/ai/providers`
- `POST /api/auth/session`

All other endpoints require a valid session bearer token.

### 4.5 Identity / Session Layer

**Files:** `app/identity/auth.py`, `app/identity/session.py`, `app/identity/bootstrap.py`, `app/identity/provider.py`, `app/identity/models.py`

**Design (implemented and hostile-gated in Stage 16):**

1. **Bootstrap Secret:** 32-byte cryptographically random secret stored in `%APPDATA%/AI-Operator/.local_secret` (Windows) or `~/.local/share/AI-Operator/.local_secret` (Linux/macOS). Constant-time comparison. Required for initial session creation.

2. **Session Creation:** `POST /api/auth/session` accepts `X-Local-Secret` header. If valid, creates a server-issued session token (`ses_<64-hex>`).

3. **Session Store:** In-memory thread-safe dictionary. Sessions have:
   - Absolute timeout: 24 hours
   - Idle timeout: 24 hours
   - Server restart invalidates all sessions

4. **Auth Middleware:** Extracts `Authorization: Bearer ses_<64-hex>` token, validates against SessionStore, builds `RequestContext` with:
   - `user_id` (derived from Windows identity)
   - `actor_type` (USER)
   - `actor_id` (same as user_id)
   - `session_id`
   - `service_name` (null)

5. **Windows Identity Derivation:** `OSIdentityProvider` derives `user_id` from Windows machine SID + user SID hash. This is an identifier, not authentication evidence.

6. **RequestContext:** Attached to `request.state.context`. Used by downstream services for audit and actor identification. Never accepted from HTTP input.

**Limitations:**
- In-memory session store: sessions lost on restart
- No multi-user support (single local user model)
- No session revocation endpoint (logout removes from store but no broadcast)
- No persistent session across restarts

### 4.6 Authorization / Permission Layer

**Files:** `app/tools/permission_engine.py`, `app/tools/approval_service.py`, `app/tools/approval_models.py`

**PermissionEngine:** Central authorization component. Evaluates `PermissionEvaluationRequest` against a 13-rule cascade:

| Rule | Condition | Decision |
|------|-----------|----------|
| RULE_01 | Tool not in registry | DENY |
| RULE_02 | Tool disabled (static or runtime) | DENY |
| RULE_03 | Missing/empty action | DENY |
| RULE_04 | Matching grant found | ALLOW |
| RULE_05 | CRITICAL risk | ABSOLUTE DENY |
| RULE_06 | `requires_confirmation=True` | ASK_USER |
| RULE_07 | MANUAL mode + non-SAFE | ASK_USER |
| RULE_08 | SEMI_AUTONOMOUS + MEDIUM/HIGH | ASK_USER |
| RULE_09 | AUTONOMOUS + HIGH | ASK_USER |
| RULE_10 | Default fall-closed | DENY |
| RULE_11 | CRITICAL + approval | ABSOLUTE DENY |
| RULE_12 | Grant repo failure | DENY |
| RULE_13 | Grant consumption failure | DENY |

After rules 1–10, `_evaluate_approval_override()` can ONLY upgrade `ASK_USER → ALLOW` with a valid `ApprovalVerificationToken`. It cannot override DENY. CRITICAL risk is re-checked and always denied.

**ApprovalService:** Manages the secure user-confirmation flow:
- `ApprovalVerificationToken` is immutable (`__slots__` + `__setattr__` raising `AttributeError`)
- Approval is bound to: `tool_id`, `action`, `arguments_hash` (SHA256), `tool_call_id`, `request_id`, `tool_definition_fingerprint`
- Atomic state transitions: PENDING → APPROVED → EXECUTING → EXECUTED/FAILED
- Concurrent `approve()` calls: exactly one succeeds (atomic DB constraint)
- Expired approvals cannot be approved or executed

**Autonomy modes:** MANUAL, SEMI_AUTONOMOUS, AUTONOMOUS. Controlled via `settings` table key `autonomy_mode`.

### 4.7 Tool Framework

**Files:** `app/tools/base.py`, `app/tools/models.py`, `app/tools/registry.py`, `app/tools/implementation_registry.py`, `app/tools/executor.py`, `app/tools/tool_execution_service.py`

**Execution pipeline (enforced by code structure):**

```
ToolCall
  → ToolExecutionService.execute_tool_call()
    → ToolRegistry lookup + enabled check
    → Argument validation (JSON Schema)
    → PermissionEngine.evaluate()
      → [DENY] → return DENIED (ToolExecutor NEVER called)
      → [ASK_USER] → return CANCELLED (ToolExecutor NEVER called)
      → [ALLOW] → ToolExecutor.execute()
        → Result validation
        → Audit logging (fail-soft)
```

**Key invariants:**
- `ToolExecutionService` is the ONLY production component that calls both `PermissionEngine.evaluate()` and `ToolExecutor.execute()`
- `ToolExecutor` itself performs NO permission checks
- `ToolDefinition` is immutable (`frozen=True` in Pydantic)
- Module-level singleton `tool_registry`
- Tool execution limits: 30s timeout, 64KB args, 256KB result, 10 calls per request

**Built-in tools (implemented and registered):**
- `SystemGetTime` (`system.get_time`) — returns current UTC time, RiskLevel.SAFE
- `FilesReadFile` (`files.read_file`) — reads file within workspace, RiskLevel.LOW, workspace containment enforced
- `DevTest` (`dev.test`) — test utility, RiskLevel.LOW

**FilesReadFile path security:**
1. Rejects absolute paths and drive letters
2. Rejects literal `..` components
3. Resolves and enforces workspace containment after symlink resolution
4. Rejects directories
5. Rejects symlinks and reparse points

**Known limitations:**
- TOCTOU window between stat calls and read
- Windows junctions/reparse points not explicitly tested
- Mixed path separators not explicitly tested on Windows

### 4.8 Workspace / Git Layer

**Files:** `app/workspace/workspace_manager.py`, `app/workspace/git_runner.py`

**WorkspaceManager:**
- Creates isolated Git worktrees at `<repo_root>/../.ai_operator/worktrees/<task_id>_<token_suffix>`
- Validates primary worktree is never dirty, never targeted for destructive ops
- Explicit staging (`git add -- <path>`) — no blind `git add .`
- `create_checkpoint()` commits only intended paths, verifies commit SHA
- `rollback_to_checkpoint()` hard resets in autonomous worktree only
- `remove_workspace()` requires `expected_execution_token` for ownership verification

**GitRunner:** Safe subprocess wrapper with explicit args, timeouts, `shell=False`.

**Architectural invariant:** Zero raw Git, raw shell, or raw subprocess calls in orchestrator, run controller, or service layers.

### 4.9 Autonomous Development Core

**Files:** `app/orchestration/orchestrator.py`, `app/orchestration/run_controller.py`, `app/orchestration/agent_interface.py`, `app/orchestration/agent_factory.py`, `app/orchestration/prompt_compiler.py`, `app/orchestration/recovery_manager.py`, `app/orchestration/repair_diagnostics.py`, `app/orchestration/agents/`

**AutonomousTaskOrchestrator:** Central task lifecycle coordinator.

**Lifecycle:**
```
PENDING / REPAIR_REQUIRED
  → claim attempt (atomic SQL UPDATE)
  → create workspace (WorkspaceManager)
  → build DevelopmentAgentContext
  → compile prompt (DevelopmentManager)
  → execute agent (DevelopmentAgent)
  → RUNNING
  → VERIFYING
  → run VerificationGate
  → PASSED → create checkpoint → COMMITTED
  → FAIL → REPAIR_REQUIRED / FAILED
```

**Invariants:**
1. `TaskStateMachine` is the sole authority for task state transitions
2. `WorkspaceManager` is the sole authority for workspace and Git operations
3. `VerificationGate` is the sole authority for code verification
4. `DevelopmentAgent` is provider-neutral (no ToolExecutor, PermissionEngine, secrets)
5. Checkpoints/commits occur ONLY after explicit VerificationGate PASS
6. FAIL/ERROR/SKIPPED verification NEVER allows commits
7. Autonomous workspace must NOT be the primary worktree
8. Zero raw Git/shell/subprocess in orchestrator

**AutonomousRunController:** Wraps orchestrator with timeout (`asyncio.wait` with sleep task), cancellation, and abort propagation. Uses fresh DB sessions for state reconciliation after timeouts.

**DevelopmentManager:** Application-owned prompt compiler. Generates `AgentExecutionPrompt` from trusted application state. Does NOT execute tools, shell, Git, or model calls. All model-bound content is untrusted data placed in the user prompt only.

**RecoveryManager:** Sole recovery authority. Does not delete active workspaces. Uses `execution_terminated_at` for durable termination proof.

### 4.10 Verification Layer

**Files:** `app/verification/gate.py`, `app/verification/check_registry.py`, `app/verification/check_runner.py`, `app/verification/checks/`

**VerificationGate:** Fail-closed check orchestrator.
1. Validates `VerificationContext` (no primary worktree)
2. Validates workspace identity (Git common dir, HEAD SHA, ancestry)
3. Locks `CheckRegistry`, runs checks in insertion order
4. Exceptions → ERROR (never PASS)
5. Unreached checks → SKIPPED
6. `GateDecision.PASS` iff all required checks return PASS

**Built-in checks:**
- `base_workspace_check`
- `typescript_check`
- `pytest_check`
- `lint_check`
- `frontend_build_check`

### 4.11 Events / History / Audit

**Files:** `app/events/schemas.py`, `app/events/sanitizer.py`, `app/events/repository.py`, `app/events/history.py`, `app/events/emitter.py`

**Events:** Durable, immutable, append-only records persisted to SQLite `events` table. Key fields: `event_id` (PK), `event_type`, `occurred_at`, `recorded_at`, `source`, `task_id`, `plan_id`, `execution_id`, `correlation_id`, `causation_id`, `payload` (sanitized, max 4KB), `metadata` (max 1KB).

**EventType vocabulary (implemented):**
- `TASK_STARTED`, `TASK_FAILED`, `TASK_COMPLETED`
- `VERIFICATION_STARTED`, `VERIFICATION_PASSED`, `VERIFICATION_FAILED`
- `CHECKPOINT_CREATED`
- `PLAN_STARTED`, `PLAN_COMPLETED`, `PLAN_FAILED`, `PLAN_CANCELLED`, `PLAN_BLOCKED`
- `APPROVAL_REQUESTED`, `APPROVAL_APPROVED`, `APPROVAL_EXECUTING`, `APPROVAL_EXECUTED`, `APPROVAL_DENIED`, `APPROVAL_EXPIRED`
- `TOOL_CALL_REQUESTED`, `TOOL_EXECUTION_STARTED`, `TOOL_EXECUTION_SUCCEEDED`, `TOOL_EXECUTION_FAILED`, `TOOL_EXECUTION_TIMEOUT`
- `PERMISSION_GRANTED`, `PERMISSION_DENIED`, `PERMISSION_EVALUATED`
- `MEMORY_CREATED`, `MEMORY_UPDATED`, `MEMORY_SUPERSEDED`, `MEMORY_DELETED`

**EventEmitter:** Emits events to `EventRepository` and optionally triggers notifications via `NotificationPolicy`.

**HistoryService:** Read-side projection over Events. Methods: `get_task_history`, `get_plan_history`, `get_execution_history`, `get_correlation_history`, `get_history_between`, `get_recent_history`. Paginated (default 50, max 500).

### 4.12 Scheduler

**Files:** `app/scheduler/worker.py`, `app/scheduler/service.py`, `app/scheduler/repository.py`, `app/scheduler/handoff.py`, `app/scheduler/calculator.py`, `app/scheduler/lease.py`

**SchedulerWorker:** Background worker started in lifespan.

**ScheduledJob model:** `job_id`, `name`, `enabled`, `schedule_type` (ONCE, INTERVAL, CRON), `schedule_expr`, `timezone`, `target_type` (task/plan), `target_id`, `last_run_at`, `next_run_at`, `lease` fields (`occurrence_id`, `claim_token`, `claimed_at`, `lease_until`, `execution_id`).

**State machine:** PENDING → CLAIMED → TRIGGERED → COMPLETED/FAILED/SKIPPED.

**Misfire policy:** SKIP_ONCE — skip exactly one occurrence, advance by one interval/cron cycle.

**Concurrency policy:** SKIP — no overlap, no queuing.

**SchedulerHandoffService:** Bridges scheduled jobs to execution (`AutonomousRunController`, `BackgroundExecutionManager`).

### 4.13 Notifications

**Files:** `app/notifications/repository.py`, `app/notifications/replay.py`, `app/notifications/policy.py`, `app/notifications/models.py`

**NotificationPolicy:** Pure function `Event → NotificationDraft | None`. No side effects.

**Durable Notification:** Persisted with `UNIQUE(source_event_id, notification_type)` for idempotency.

**Replay:** Repair mechanism for forward-processing failures. Deterministic ordering, bounded limit, correct filtering, no event modification.

**Internal-only in V1:** No public REST surface.

### 4.14 Memory

**Files:** `app/memory/service.py`, `app/memory/repository.py`, `app/memory/models.py`

**MemoryService:** CRUD for long-lived memory entries with:
- Trust levels: EXPLICIT_USER > SYSTEM > DERIVED > EXTERNAL > AGENT
- Confidence scores
- Scope: global, project, task
- TTL support
- Soft delete
- Supersession semantics
- Event integration (MEMORY_CREATED, MEMORY_UPDATED, MEMORY_SUPERSEDED, MEMORY_DELETED)

**Authority:**
- USER can create/update/delete/supersede any memory
- SYSTEM/AGENT limited to own memories
- Scope and scope_id immutable after creation
- MemoryService does NOT import PermissionEngine, ApprovalService, or ToolExecutor

**Current limitations:**
- No retrieval/search API in V1
- Superseded memories remain queryable in V1 (retrieval filtering deferred)
- No embeddings/vector DB
- No public REST API in V1

### 4.15 AI Manager / Model/Provider Layer

**Files:** `app/ai/services/ai_service.py`, `app/ai/services/provider_manager.py`, `app/ai/services/conversation_service.py`, `app/ai/providers/`

**AIService:** High-level chat assistant with bounded agent loop.
- `_LoopLimits`: max iterations, max tool calls total, max tool calls per request
- Tool calls go through `ToolExecutionService` with autonomy mode enforcement
- If tool returns `CANCELLED` with `error_code="ask_user"`, creates `PendingApproval` and returns `approval_required`
- Supports Ollama, Kimi, OpenRouter providers
- System instruction explicitly warns that tool results are untrusted data

**ProviderManager:** Provider selection, config get/update.

**Providers:**
- `OpenRouterProvider` (cloud)
- `KimiProvider` (cloud)
- `OllamaProvider` (local)

**CredentialService:** Credential encryption/decryption.

**OllamaProcessManager:** Managed Ollama subprocess lifecycle (start/stop/restart).

**OllamaDownloadManager:** Async model download with disk-space checks.

**AI Manager Gate (app/ai_manager/gate.py):**
- Higher-level authorization layer
- Mantra: MODEL PROPOSES. APPLICATION DECIDES. AUTHORIZED APPLICATION SERVICES EXECUTE.
- `RawManagerDecision` and `RawActionProposal` are untrusted model output (`extra="forbid"`)
- `ManagerDecision` and `ActionProposal` are application-controlled validated forms
- Capability-based authorization via `CapabilityRegistry`
- `ApplicationPolicy` controls: `max_risk_level`, `allowed_capabilities`, `denied_capabilities`, `require_approval_for_capabilities`

### 4.16 Plan / Task / Execution Orchestration

**Files:** `app/services/plan_execution_service.py`, `app/services/autonomous_loop.py`, `app/services/background_execution_manager.py`, `app/services/plan_service.py`

**PlanExecutionService:** Validates task existence, startability, attempt limits, DAG dependencies; delegates to `AutonomousRunController`.

**AutonomousPlanExecutor:** Multi-task DAG loop. Schedules runnable tasks, delegates to `PlanExecutionService`, handles cancellation, yields when tasks are actively executing.

**BackgroundExecutionManager:** HTTP-triggered background plan execution with per-plan `asyncio.Lock`, duplicate protection, graceful shutdown.

### 4.17 Task State Machine

**Files:** `app/tasks/state_machine.py`, `app/tasks/models.py`, `app/tasks/exceptions.py`

**TaskStatus enum:** PENDING, RUNNING, VERIFYING, PASSED, REPAIR_REQUIRED, COMMITTED, PAUSED, FAILED, CANCELLED, ABORTED.

**DevelopmentTask:** `task_id` (`task_<64-hex>`), `task_key`, `plan_id`, `project_id`, `attempt_count`, `max_attempts`, `execution_token`, `workspace_path`, `execution_terminated_at`, `workspace_cleanup_pending`.

**TaskStateMachine:** Sole authority for state transitions. `claim_attempt()` performs atomic `UPDATE ... WHERE status = ? AND attempt_count < ?` in one statement.

### 4.18 Database

**Engine:** `sqlite+aiosqlite` via SQLAlchemy 2.0 async.

**Session factory:** `async_sessionmaker` with `expire_on_commit=False`.

**Migrations:** Inline SQLite `ALTER TABLE` / `CREATE TABLE IF NOT EXISTS` in `init_db()`.

**14 tables:**
1. `settings` — key-value application settings
2. `activity_events` — lightweight activity log
3. `conversations` — AI chat conversations
4. `messages` — chat messages
5. `permission_grants` — persistent/one-time/time-bound permission grants
6. `audit_events` — tool execution audit trail
7. `pending_approvals` — pending approval requests
8. `capability_approvals` — capability approval records
9. `development_tasks` — autonomous development tasks
10. `task_dependencies` — DAG dependencies
11. `plan_fingerprints` — plan deduplication
12. `kilo_sessions` — Kilo CLI execution tracking
13. `events` — durable event store
14. `scheduled_jobs` — scheduler jobs
15. `notifications` — durable notifications
16. `memories` — long-lived memory entries

### 4.19 Frontend / Tauri

**Technology:** Tauri v2, React 19, TypeScript, Vite, react-router-dom

**Key files:**
- `apps/desktop/src/services/apiClient.ts` — typed HTTP client with dynamic Core URL resolution, auto-retry, structured errors
- `apps/desktop/src/hooks/useCoreStatus.ts` — Core status polling via shared `subscribeCoreStatus` bus
- `apps/desktop/src-tauri/src/lib.rs` — Rust native commands, Core process management
- `apps/desktop/src-tauri/src/main.rs` — Tauri application entry

**Architectural invariants:**
- All API calls use `getCoreUrl()` single source of truth
- Core status changes broadcast via `subscribeCoreStatus`
- HTTP errors from Core do NOT set `CORE OFFLINE` (only real connection failures do)
- All API responses use typed interfaces with structured error payloads
- Frontend never reads SQLite directly

---

## 5. AUTHORITY MODEL

### 5.1 Hierarchy of Authority

```
User (ultimate authority)
  |
  ├── Authenticated Identity (RequestContext)
  │     ├── user_id (derived from Windows SID)
  │     ├── actor_type (USER, SYSTEM, AGENT, SERVICE)
  │     ├── actor_id (identity + session)
  │     └── session_id
  │
  ├── Session (server-issued, in-memory, time-limited)
  │
  ├── RequestContext (attached to request.state)
  │
  ├── AI Manager (proposes, plans, selects)
  │     ├── PlanManager (generates plans via provider)
  │     ├── Model Router (future: selects model)
  │     └── Information Sufficiency (future: decides what is needed)
  │
  ├── Application Services (decide and execute)
  │     ├── ToolExecutionService (orchestrates execution)
  │     ├── PermissionEngine (evaluates authorization)
  │     ├── ApprovalService (manages user confirmation)
  │     ├── TaskStateMachine (authority for state transitions)
  │     ├── VerificationGate (sole authority for verification)
  │     ├── WorkspaceManager (sole authority for workspace/Git)
  │     ├── EventEmitter (records events)
  │     ├── HistoryService (read projection)
  │     ├── SchedulerService (when to execute)
  │     ├── NotificationPolicy (pure function: event → notification)
  │     ├── MemoryService (CRUD for memory)
  │     └── BackgroundExecutionManager (HTTP-triggered bg tasks)
  │
  ├── Agents (workers subordinate to AI Manager)
  │     ├── DevelopmentAgent (coding tasks)
  │     ├── KiloDevelopmentAgent (Kilo CLI wrapper)
  │     ├── Future: Planner, Researcher, Browser, Finance, etc.
  │
  ├── Tools (explicit capabilities)
  │     ├── ToolRegistry (registry of capabilities)
  │     ├── ToolExecutor (dispatches to implementations)
  │     └── Tool Implementations (actual capability logic)
  │
  └── Data
        ├── Events (immutable evidence)
        ├── History (read projection)
        ├── Memory (knowledge, not authority)
        ├── Audit (decision trail)
        └── Settings (user-configured policy)
```

### 5.2 Who Can Do What

| Action | Who Decides | Who Executes |
|--------|-------------|--------------|
| Propose a plan | AI Manager / PlanManager | — |
| Select a model | AI Manager / Model Router (future) | — |
| Select a tool | AI Manager | — |
| Evaluate permission | PermissionEngine | — |
| Grant permission | User (via persistent/one-time grants) | PermissionEngine reads grants |
| Ask for confirmation | PermissionEngine (ASK_USER) | ApprovalService creates request |
| Approve an action | User (via ApprovalService) | ApprovalService transitions state |
| Execute a tool | PermissionEngine (ALLOW) | ToolExecutor |
| Cancel a task | User or system | TaskStateMachine + Orchestrator |
| Verify code | VerificationGate | Verification checks |
| Create a Git commit | VerificationGate PASS → WorkspaceManager | GitRunner |
| Read memory | MemoryService | — |
| Write memory | USER or authorized agent | MemoryService |
| Emit an event | Any service with event_emitter | EventEmitter → EventRepository |
| Query history | Any authenticated user | HistoryService |
| Schedule a job | User or system | SchedulerService → SchedulerWorker |
| Start/stop Ollama | User or system | AIService → OllamaProcessManager |

### 5.3 Authority Boundaries

**What the model CANNOT do:**
- Modify PermissionEngine rules
- Bypass ApprovalService
- Modify task state directly
- Modify verification results
- Claim Git authority
- Execute shell commands
- Access secrets directly
- Modify audit logs or events
- Create or modify grants
- Override CRITICAL denial

**What tools CANNOT do:**
- Call PermissionEngine (ToolExecutor does not)
- Bypass ToolExecutionService (no direct execution path in production)
- Modify registry definitions (ToolDefinition is frozen)
- Access files outside workspace (for file tools)
- Execute subprocesses (no subprocess import in BaseTool)

**What agents CANNOT do:**
- Modify task state directly (orchestrator owns this)
- Bypass VerificationGate
- Bypass PermissionEngine
- Access raw Git or shell
- Modify security policy

---

## 6. IDENTITY & SESSION

### 6.1 Local-User Model

AI Operator is designed for a **single local user** on a Windows machine. The user is identified by a hash of the Windows machine SID + user SID. This is an identifier, not an authentication credential.

### 6.2 Windows Identity Derivation

`OSIdentityProvider` reads the Windows machine and user SIDs via Windows API. It hashes them to produce a stable `user_id`. This remains consistent across reboots and desktop reopens but changes if the Windows user account changes.

### 6.3 Bootstrap Secret

- 32-byte cryptographically random secret
- Stored in `%APPDATA%/AI-Operator/.local_secret`
- Protected by Windows ACLs (user-only access)
- Constant-time comparison (`hmac.compare_digest`)
- Used exactly once: at `POST /api/auth/session` to obtain a server-issued session token
- If the file is missing or corrupted, a new secret can be generated (but this invalidates existing sessions — and there are none at bootstrap)

### 6.4 Server-Issued Sessions

- Session token format: `ses_<64-hex-characters>`
- Generated by `SessionStore.create()` using `secrets.token_hex(32)`
- Stored in-memory only
- Validated by `AuthMiddleware` on every request
- Attached to `request.state.context` as `RequestContext`

### 6.5 Server-Side Session Store

- Thread-safe `dict` subclass with `threading.RLock`
- O(1) lookup by token
- Tracks: `user_id`, `created_at`, `last_activity_at`, `session_id`
- Enforces:
  - Absolute timeout (default 24h)
  - Idle timeout (default 24h)
  - Validation returns `None` for expired or missing sessions

### 6.6 Expiration and Idle Timeout

- `session_absolute_timeout_seconds` (configurable via settings, default 86400)
- `session_idle_timeout_seconds` (configurable via settings, default 86400)
- Validation checks both; returns `None` if either is exceeded

### 6.7 Revocation and Restart Behavior

- `POST /api/auth/logout` removes session from store
- Core restart invalidates all sessions (in-memory store is lost)
- No persistent session recovery across restarts in V1

### 6.8 Localhost Boundary

- Core binds to `127.0.0.1` by default
- CORS limited to localhost/tauri origins only
- Session tokens are bearer tokens with no additional transport security beyond localhost
- Tokens should not be exposed to external networks

### 6.9 RequestContext

```python
class RequestContext:
    user_id: str           # Derived from Windows identity
    actor_type: ActorType  # USER, SYSTEM, AGENT, SERVICE, ANONYMOUS
    actor_id: str          # Same as user_id for USER
    session_id: str        # Session identifier
    service_name: str | None  # For internal service calls
```

Built server-side from session. Never accepted from HTTP input. Used for:
- Audit logging (who performed an action)
- Memory source tracking
- Permission evaluation (actor_id for audit)

### 6.10 Limitations

- In-memory session store: sessions lost on restart
- No multi-user support
- No session revocation broadcast
- No persistent session across restarts
- No MFA or additional authentication factors
- Bootstrap secret is the only initial authentication mechanism

### 6.11 Future Evolution Path

Potential future enhancements:
- Persistent session recovery (encrypted session store on disk)
- Session revocation API
- Multi-user support (requires architectural changes)
- Biometric or PIN-based local authentication
- Session activity logging

---

## 7. PERMISSIONS / AUTONOMY

### 7.1 Interaction Modes

**QUICK:**
- Higher tolerance for missing information
- Reasonable assumptions allowed (explicitly marked)
- Minimal questioning
- Fast execution where safe
- Suitable for simple, low-risk tasks

**DEEP / EXACT:**
- Gathers materially relevant missing information
- Asks clarifying questions
- Lower tolerance for assumptions
- Verifies important details before execution
- Suitable for complex, high-impact tasks

**Both modes must ask questions whenever missing information materially affects correctness or safety.**

### 7.2 Autonomy Profiles (Policy Profiles, Not Security Bypasses)

| Profile | Behavior |
|---------|----------|
| ASSISTED | AI analyzes, plans, proposes. No automatic execution. |
| SEMI_AUTONOMOUS | AI can execute SAFE and LOW risk automatically. MEDIUM/HIGH/CRITICAL require approval. |
| AUTONOMOUS | AI can execute SAFE/LOW/MEDIUM automatically. HIGH requires approval. CRITICAL always denied. |
| FULL_AUTONOMY | Same as AUTONOMOUS in current architecture (FULL_AUTONOMY is not yet a distinct mode with additional capabilities). |

**Important:** These are policy profiles that control how PermissionEngine evaluates risk levels. They do NOT bypass any security boundary. CRITICAL is always denied. DENY never executes. ASK_USER never silently executes.

### 7.3 Capability-Specific Permission Profiles (Future)

The preferred future model is **capability-specific permission profiles** rather than a single global autonomy level.

**Potential capability groups:**
- filesystem
- browser
- email
- calendar
- messaging
- purchases
- finance (Finora)
- training (EVOLVE)
- computer use
- software development
- cloud services
- system administration
- external services

**Interaction:**
- Global autonomy provides a baseline
- Per-capability authorization overrides or restricts the baseline
- A capability can be completely disabled even in AUTONOMOUS mode
- A capability can require approval even in MANUAL mode (if configured)
- User-configurable per-capability policies

### 7.4 Current Implementation

The current `autonomy_mode` setting is a single global value: `manual`, `semi_autonomous`, or `autonomous`. It is stored in the `settings` table and read by `AIService._get_autonomy_mode()`.

PermissionEngine evaluates each tool call against:
1. Tool registry (existence, enabled state)
2. Risk level (SAFE, LOW, MEDIUM, HIGH, CRITICAL)
3. Autonomy mode
4. Grants (persistent, one-time, time-bound)
5. Approval context

---

## 8. INFORMATION SUFFICIENCY / QUESTIONING ENGINE

### 8.1 Concept

This is a **core future capability** that is not yet implemented.

The system must decide, for each task, whether it has enough information to proceed safely and correctly.

**Decision model:**

```
Task → required information → available information → uncertainty → risk → readiness
```

### 8.2 Information States

| State | Description | Action |
|-------|-------------|--------|
| Missing | Required information is completely absent | Ask user or research |
| Uncertain | Information exists but confidence is low | Verify or ask |
| Conflicting | Multiple sources disagree | Ask user or resolve |
| Stale | Information may be outdated | Refresh or flag |
| Optional | Missing information does not affect correctness | Proceed with assumption |
| Critical | Missing information materially affects safety or correctness | Must obtain before proceeding |

### 8.3 Decision Rules

1. If critical information is missing → refuse to proceed until obtained
2. If uncertainty materially affects correctness → ask or verify
3. If information is optional and safe to assume → proceed, marking assumption
4. If conflicting information cannot be resolved automatically → escalate to user
5. If stale information could cause incorrect action → refresh or ask

### 8.4 Mode Interaction

**QUICK mode:**
- Higher threshold for "materially affects"
- More permissive assumptions
- Fewer clarifying questions

**DEEP mode:**
- Lower threshold for "materially affects"
- Stricter verification requirements
- More clarifying questions

### 8.5 Future Implementation

This will be implemented as a subsystem of the AI Manager that:
- Maps task types to required information sets
- Compares available context against required sets
- Calculates uncertainty scores
- Generates targeted questions
- Manages research tasks (web search, file search, memory search)
- Returns structured readiness assessments

---

## 9. MEMORY / KNOWLEDGE

### 9.1 Current Implementation

**Files:** `app/memory/service.py`, `app/memory/repository.py`, `app/memory/models.py`

**MemoryService:** CRUD for `Memory` entries with:
- `memory_id` (primary key)
- `content` (text)
- `metadata` (JSON)
- `confidence` (float, 0.0–1.0)
- `scope` (GLOBAL, PROJECT, TASK)
- `scope_id` (optional project/task identifier)
- `trust_level` (EXPLICIT_USER, SYSTEM, DERIVED, EXTERNAL, AGENT)
- `source_id` (who created it)
- `ttl` (optional time-to-live)
- `created_at`, `updated_at`, `deleted_at`
- `superseded_by` (for supersession chain)

**Trust hierarchy:** EXPLICIT_USER > SYSTEM > DERIVED > EXTERNAL > AGENT

**Authority rules:**
- USER can create/update/delete/supersede any memory
- SYSTEM can create/update/delete own memories
- AGENT can create/update own memories (limited scope)
- Scope and scope_id are immutable after creation
- Source_id defaults from caller identity

**Event integration:** Memory mutations emit events (`memory.created`, `memory.updated`, `memory.superseded`, `memory.deleted`) within the same DB transaction as the mutation. Events persist atomically.

**Current limitations:**
- No retrieval/search API in V1
- No embeddings or vector search
- Superseded memories remain queryable
- No public REST API in V1
- No batch operations
- No memory categories or tags beyond scope

### 9.2 Future Memory Architecture

Memory should support:

| Category | Examples | Ownership |
|----------|----------|-----------|
| Personal memory | User preferences, habits, important facts | User |
| Project memory | Architecture decisions, tech stack, goals | Project |
| Application memory | Domain knowledge for Finora, EVOLVE | Application |
| Preferences | Response style, autonomy defaults, UI preferences | User |
| Decisions | Past decisions with rationale | User or system |
| Learned context | Patterns observed from user behavior | System |
| Important facts | Key information the user explicitly saved | User |
| Temporary context | Short-lived context for active tasks | Agent/system |
| Historical knowledge | Lessons learned from past tasks | System |

### 9.3 Planned User-Facing Memory UI

- Inspect memories (filter by scope, trust level, date)
- Search memories (full-text in V1, semantic in future)
- Edit memories (USER can edit own, SYSTEM/AGENT can edit own)
- Delete memories (soft delete)
- View source (who created, when, trust level)
- View creation date
- View trust/confidence
- View scope and provenance

### 9.4 Explicit Distinctions

| Concept | Purpose | Authority |
|---------|---------|-----------|
| Memory | Knowledge storage and retrieval | MemoryService |
| Event History | Immutable record of what happened | EventEmitter |
| Task State | Current execution state of tasks | TaskStateMachine |
| Permission | Whether an action is allowed | PermissionEngine |
| Approval | User confirmation of an action | ApprovalService |

Memory ≠ Event History ≠ Task State ≠ Permission ≠ Authority

---

## 10. MODEL ROUTER

### 10.1 Concept

The Model Router is a **future central component** that selects the most appropriate model/provider for each request based on multiple factors.

### 10.2 Selection Factors

| Factor | Examples |
|--------|----------|
| Task type | Chat, coding, vision, reasoning, embedding |
| Reasoning difficulty | Simple classification vs complex multi-step planning |
| Coding requirement | Code generation, analysis, debugging |
| Vision requirement | Screenshot analysis, OCR, document understanding |
| Latency | Real-time interaction vs background processing |
| Cost | Free local models vs paid cloud APIs |
| Privacy | Sensitive data → local/private model |
| Context size | Long conversations need larger context windows |
| Availability | Provider health, model availability |
| Provider reliability | Uptime history, rate limits |
| Model capabilities | Tool calling, structured output, function calling |
| User policy | User preferences for providers/models |

### 10.3 Routing Examples

| Scenario | Route To |
|----------|----------|
| Simple chat, low latency | Fast local model (Ollama small model) |
| Complex reasoning | Strong reasoning model (cloud or large local) |
| Coding tasks | Code-optimized model |
| Vision tasks | Vision-capable model (Qwen3-VL, etc.) |
| Sensitive data | Local/private model |
| Provider unavailable | Fallback provider |
| Background task | Cheapest adequate model |

### 10.4 Architecture

```
Request
  → Model Router
    → Provider Abstraction Layer
      → Ollama (local)
      → OpenRouter (cloud)
      → Kimi (cloud)
      → Future providers
```

### 10.5 Provider Abstraction

- `PlanProvider` interface (already exists for planning)
- `AIProvider` interface (already exists for chat)
- Model capability metadata registry
- Health/reliability tracking per provider
- Fallback chain configuration
- Cost tracking per model/provider
- Privacy policy enforcement (data minimization before sending to cloud)

### 10.6 Future Evaluation System

- A/B testing of models for specific task types
- User feedback collection
- Automated quality metrics
- Model performance regression detection

### 10.7 Current State

The current `ProviderManager` selects a single provider and model from user configuration. There is no automatic routing, fallback, or capability-based selection.

---

## 11. AI MANAGER

### 11.1 Current Role

The AI Manager (currently implemented as `AIService` + `PlanManager` + `DevelopmentManager`) is responsible for:

- Understanding user intent (chat + tool loop)
- Planning multi-step tasks (PlanManager via PlanProvider)
- Deciding what information is needed (future: Information Sufficiency)
- Selecting tools/capabilities (via tool loop)
- Coordinating agents (DevelopmentAgent, KiloDevelopmentAgent)
- Creating tasks (AutonomousPlanExecutor)
- Monitoring progress (future: proactive monitoring)
- Recovering from failures (RepairDiagnostics, RecoveryManager)
- Deciding when to ask the user (PermissionEngine ASK_USER + ApprovalService)
- Selecting model strategy (future: Model Router)

### 11.2 What AI Manager Must NOT Do

- Bypass permissions
- Execute arbitrary filesystem operations
- Approve itself
- Modify security policy
- Bypass ApprovalService
- Treat model output as authority
- Modify task state directly
- Claim Git authority
- Access secrets directly
- Modify audit logs or events

### 11.3 Current Implementation

**AIService** (`app/ai/services/ai_service.py`):
- High-level chat assistant
- Bounded agent loop (`_LoopLimits`)
- Tool execution through `ToolExecutionService`
- Approval creation when `ask_user` returned
- Ollama lifecycle management (start/stop/restart, model pull/delete)
- Provider health checks and error mapping

**PlanManager** (`app/manager/plan_manager.py`):
- Provider-neutral plan generation
- Calls `PlanProvider.generate_plan()`
- Normalizes plan output
- Does NOT persist plans, execute tasks, select providers, or implement retry loops

**DevelopmentManager** (`app/orchestration/prompt_compiler.py`):
- Application-owned prompt compiler
- Generates `AgentExecutionPrompt` from trusted application state
- Does NOT execute tools, shell, Git, or model calls
- All model-bound content is untrusted data

**AI Manager Gate** (`app/ai_manager/gate.py`):
- Higher-level authorization layer
- Validates raw model decisions into application-controlled forms
- Capability-based authorization via `CapabilityRegistry`
- Does NOT execute capabilities, plans, or call external services

---

## 12. TOOLS / CAPABILITIES

### 12.1 Current Tool Framework

**Files:** `app/tools/base.py`, `app/tools/models.py`, `app/tools/registry.py`, `app/tools/implementation_registry.py`, `app/tools/executor.py`, `app/tools/tool_execution_service.py`, `app/tools/permission_engine.py`, `app/tools/approval_service.py`, `app/tools/audit_service.py`

**ToolDefinition:**
```python
class ToolDefinition(BaseModel):
    tool_id: str              # Unique identifier (e.g., "files.read_file")
    name: str                 # Human-readable name
    description: str          # What the tool does
    action: str               # Canonical action (e.g., "read_file")
    category: ToolCategory    # FILES, SYSTEM, DEV, etc.
    risk_level: RiskLevel     # SAFE, LOW, MEDIUM, HIGH, CRITICAL
    enabled: bool             # Static enabled state
    requires_confirmation: bool  # Always require approval
    parameters_schema: dict   # JSON Schema for arguments
    result_schema: dict       # JSON Schema for results
```

**ToolCall:**
```python
class ToolCall(BaseModel):
    tool_call_id: str         # Unique call identifier
    tool_id: str              # Tool identifier
    arguments: dict           # Call arguments
```

**ToolExecutionContext:**
```python
class ToolExecutionContext:
    request_id: str | None
    actor_id: str | None
    tool_call_id: str | None
    approval_context: Any | None
    workspace: WorkspaceContext | None
```

**WorkspaceContext:**
```python
class WorkspaceContext:
    workspace_path: str       # Contained root for file operations
```

**ToolRegistry:** Thread-safe, immutable definitions, runtime enable/disable, singleton.

**ToolImplementationRegistry:** Maps `tool_id` → implementation instance.

**ToolExecutor:** Dispatches `ToolCall` to implementation. Does NOT perform permission checks.

**ToolExecutionService:** Orchestrates execution. Enforces:
- Tool existence + enabled check
- Argument validation (JSON Schema)
- PermissionEngine.evaluate()
- ONLY if ALLOW → ToolExecutor.execute()
- Result validation
- Audit logging (fail-soft)

### 12.2 Built-in Tools

| Tool ID | Action | Category | Risk | Status |
|---------|--------|----------|------|--------|
| `system.get_time` | get_time | SYSTEM | SAFE | Implemented |
| `files.read_file` | read_file | FILES | LOW | Implemented (Stage 17.3A) |
| `dev.test` | test | DEV | LOW | Implemented |

### 12.3 Tool Execution Limits

| Limit | Default | Purpose |
|-------|---------|---------|
| `max_execution_time_ms` | 30,000 | Timeout via `asyncio.wait_for` |
| `max_arguments_size_bytes` | 65,536 (64KB) | Prevent oversized inputs |
| `max_result_size_bytes` | 262,144 (256KB) | Prevent oversized outputs |
| `max_tool_calls_per_request` | 10 | Prevent batch abuse |

### 12.4 Audit Integration

All tool execution decisions and results produce audit events:
- `TOOL_CALL_REQUESTED`
- `TOOL_EXECUTION_STARTED`
- `TOOL_EXECUTION_SUCCEEDED`
- `TOOL_EXECUTION_FAILED`
- `TOOL_EXECUTION_TIMEOUT`
- `APPROVAL_REQUESTED`
- `APPROVAL_APPROVED`
- `APPROVAL_EXECUTING`
- `APPROVAL_EXECUTED`
- `APPROVAL_DENIED`
- `APPROVAL_EXPIRED`
- `PERMISSION_GRANTED`
- `PERMISSION_DENIED`
- `PERMISSION_EVALUATED`

Audit payloads are sanitized (`sanitize_payload` redacts sensitive keys, truncates to 4096 bytes).

### 12.5 Future Capability Architecture

AI Operator should operate through **explicit capabilities**, not arbitrary application internals. Each capability has:
- A `ToolDefinition` (or equivalent capability contract)
- Typed inputs and outputs
- Risk level
- Permission requirements
- Audit events
- Idempotency guarantees
- Version

The framework should support:
- Future file tools (write, copy, move, delete-to-recycle-bin, search, list)
- Future system tools (process management, system status)
- Future browser tools (via Playwright or equivalent)
- Future email tools (read, classify, draft)
- Future calendar tools
- Future Finora/EVOLVE capabilities
- Future computer-use tools (as fallback)

---

## 13. EVOLVE + FINORA INTEGRATION

### 13.1 Recommended Architecture

```
AI Operator (central intelligence)
  ├── Capability API (generic protocol)
  │     ├── Finora (finance domain)
  │     ├── EVOLVE (training/fitness domain)
  │     └── Future applications
  │
  ├── PermissionEngine (authorizes all capability calls)
  ├── ApprovalService (user confirmation)
  ├── AuditLogService (records all capability invocations)
  └── EventEmitter (emits domain events)
```

### 13.2 What NOT To Do

Do NOT create independent AI brains in every app with separate:
- model routers
- permission systems
- memories
- autonomous planners

Instead, applications should **expose controlled capabilities** that AI Operator invokes through its own security pipeline.

### 13.3 Finora Capabilities (Future)

**Example capabilities:**
- `finora.get_accounts` — list financial accounts
- `finora.get_transactions` — retrieve transactions (with date range, filters)
- `finora.get_balance` — account balance
- `finora.get_budget` — budget status
- `finora.get_categories` — transaction categories
- `finora.create_transaction` — record a transaction
- `finora.update_transaction` — modify a transaction
- `finora.get_reports` — financial reports

**Architecture:**
- Finora exposes a capability API (REST, MCP, or direct integration)
- AI Operator calls through `ToolExecutionService` with `finora.*` tool IDs
- Each capability has risk level, parameters schema, and approval requirements
- Finora data stays in Finora; AI Operator receives structured results
- Audit events record all capability invocations

### 13.4 EVOLVE Capabilities (Future)

**Example capabilities:**
- `evolve.get_profile` — user training profile
- `evolve.get_training_plan` — current plan
- `evolve.get_today` — today's scheduled training
- `evolve.get_progress` — progress metrics
- `evolve.log_workout` — record workout
- `evolve.update_availability` — update schedule
- `evolve.generate_plan` — create/adapt plan

**Architecture:** Same as Finora — EVOLVE exposes capabilities, AI Operator invokes through its security pipeline.

### 13.5 Capability API Requirements

The integration protocol should support:
- Capability discovery (list available capabilities with metadata)
- Typed inputs (JSON Schema)
- Typed outputs (JSON Schema)
- Permission/authorization (via PermissionEngine)
- Approval (via ApprovalService)
- Audit (via AuditLogService)
- Idempotency (idempotency keys)
- Errors (structured error codes)
- Versioning (capability version in contract)
- Event emission (domain events to EventEmitter)
- Domain ownership (Finora owns finance data, EVOLVE owns training data)

### 13.6 Future Beyond Finora and EVOLVE

The architecture should support arbitrary future applications:
- Email/calendar applications
- Browser automation services
- Cloud service connectors
- Custom personal tools
- External APIs

---

## 14. COMPUTER USE

### 14.1 Definition

Computer Use is a **future fallback layer** for controlling the Windows desktop when APIs, capabilities, and browser automation are insufficient.

### 14.2 Preferred Hierarchy

1. **Native API** — most reliable, most auditable
2. **Application capability** — controlled API exposed by the application
3. **Dedicated tool** — purpose-built tool with known behavior
4. **Browser/API automation** — Playwright, structured web automation
5. **Computer Use** — mouse, keyboard, screen interaction (LAST RESORT)
6. **Human intervention** — when automation cannot safely proceed

### 14.3 What Computer Use May Eventually Control

- Mouse movement and clicks
- Keyboard input
- Window management (focus, resize, move)
- Desktop applications (UI interaction)
- Browser (when structured APIs insufficient)
- Dialogs and system prompts
- UI-only systems with no API

### 14.4 Security Boundaries

Computer Use must remain subject to:
- Identity (RequestContext with authenticated user)
- Permissions (PermissionEngine evaluation)
- Approvals (ApprovalService for sensitive actions)
- Session (valid session required)
- Audit (all actions logged)
- Workspace/security policy (bounded to allowed applications)
- Bounded execution (timeout, action limits)
- Emergency stop (user can halt at any time)
- Action limits (max actions per session, per application)

### 14.5 Current State

**NOT IMPLEMENTED.** No Computer Use capability exists in the current codebase.

---

## 15. 24/7 RUNTIME

### 15.1 Design Principle

The system should NOT run as an "infinite agent loop continuously thinking." Instead:

```
Event / Scheduler
  → detect relevant condition
  → evaluate policy
  → determine whether action is needed
  → gather context
  → plan
  → authorize
  → execute
  → record
  → notify if appropriate
```

### 15.2 Components

| Component | Role |
|-----------|------|
| Scheduler | Triggers time-based and interval-based workflows |
| Events | React to file changes, emails, calendar events, task completions |
| BackgroundExecutionManager | HTTP-triggered background plan execution |
| SchedulerWorker | Background polling for due jobs |
| SchedulerHandoffService | Bridges jobs to execution |

### 15.3 Idempotency and Recovery

- Events have unique `event_id` primary keys (idempotent emission)
- Scheduler uses atomic claim (`UPDATE ... WHERE next_run_at <= :now`)
- Task execution uses `TaskStateMachine.claim_attempt()` (atomic claim)
- Durable state in SQLite survives restarts
- `execution_terminated_at` provides durable termination proof for recovery

### 15.4 Resource Usage

- Idle: minimal CPU/memory (scheduler polls at intervals)
- Active: bounded by task execution limits
- Ollama process: managed lifecycle, can be stopped when not needed
- Background tasks: time-limited, cancellable

### 15.5 Sleep/Idle Behavior

- No continuous polling for non-scheduler events
- SchedulerWorker polls at reasonable intervals (configurable)
- When no tasks are pending and no events are pending, system is idle
- Notifications only for relevant, non-duplicate events

---

## 16. PROACTIVE BEHAVIOR

### 16.1 Examples

- Reminders for upcoming deadlines
- Monitoring for project status changes
- Scheduled actions (daily reports, file organization)
- Detected anomalies (unusual activity, errors)
- Recurring task notifications
- Important change alerts (new email from key contact, calendar conflict)

### 16.2 Anti-Noise Principles

The system must avoid becoming noisy. Proactive actions require:

| Factor | Rule |
|--------|------|
| Relevance | Action must be relevant to user's current context |
| Urgency | Time-sensitive actions have higher priority |
| Confidence | Low-confidence predictions should not trigger notifications |
| User preferences | Respect quiet hours, notification settings |
| Notification policy | Centralized policy for what generates notifications |
| Cooldown | Minimum interval between similar notifications |
| Deduplication | Same event → same notification (idempotent) |
| Escalation | Unactioned critical items escalate appropriately |

### 16.3 Current Implementation

**NotificationPolicy** is a pure function: `Event → NotificationDraft | None`.  
**Notification** is durable with idempotency via `UNIQUE(source_event_id, notification_type)`.  
**Replay** is a repair mechanism for forward-processing failures.

**Current limitation:** No public REST API for notifications. No user-facing notification preferences UI. No batch processing.

---

## 17. AGENT ARCHITECTURE

### 17.1 Current Agents

| Agent | Role | Status |
|-------|------|--------|
| `DevelopmentAgent` | Coding tasks (provider-neutral interface) | Implemented |
| `KiloDevelopmentAgent` | Kilo CLI wrapper for coding | Implemented |
| `WorkspaceWriter` | Safe file writes in workspace | Implemented |

### 17.2 Future Specialized Agents

| Agent | Domain | Status |
|-------|--------|--------|
| Planner | Task decomposition and planning | Future |
| Researcher | Web search, document analysis | Future |
| Developer | Code implementation | Partial (DevelopmentAgent) |
| Browser Agent | Browser automation | Future |
| Computer Use Agent | UI fallback | Future |
| Finance Agent | Finora operations | Future |
| Training/EVOLVE Agent | Fitness/training operations | Future |
| Memory Agent | Memory management | Future |
| Monitor | System/project monitoring | Future |
| Reviewer | Code review, quality checks | Future |
| Verification Agent | Test execution and verification | Partial (VerificationGate) |

### 17.3 Agent Contracts

All agents must:
- Receive bounded context (not unlimited memory)
- Operate within explicit permissions
- Report results as structured data (not raw authority)
- Support cancellation and timeout
- Support retries with bounded attempts
- Produce verifiable results
- Not bypass security boundaries

### 17.4 Agent Subordination

Agents are **workers subordinate to the central AI Manager**. They do NOT become independent authorities. The AI Manager:
- Creates tasks for agents
- Monitors progress
- Recovers from failures
- Decides when to ask the user
- Coordinates multiple agents

---

## 18. AUTONOMOUS DEVELOPMENT

### 18.1 Current Subsystem

Autonomous Development is a **specialized subsystem** for safe, autonomous coding tasks. It is NOT the entire AI Operator.

### 18.2 Components

| Component | File | Role |
|-----------|------|------|
| PlanManager | `app/manager/plan_manager.py` | Provider-neutral plan generation |
| DevelopmentManager | `app/orchestration/prompt_compiler.py` | Application-owned prompt compiler |
| DevelopmentAgent | `app/orchestration/agent_interface.py` | Provider-neutral agent interface |
| KiloDevelopmentAgent | `app/orchestration/agents/kilo_agent.py` | Kilo CLI wrapper |
| WorkspaceManager | `app/workspace/workspace_manager.py` | Git worktree isolation |
| VerificationGate | `app/verification/gate.py` | Fail-closed verification |
| RecoveryManager | `app/orchestration/recovery_manager.py` | Stale run recovery |
| TaskStateMachine | `app/tasks/state_machine.py` | Atomic state transitions |
| RepairDiagnostics | `app/orchestration/repair_diagnostics.py` | Bounded repair feedback |

### 18.3 Execution Flow

```
User Request
  → AutonomousPlanExecutor
    → PlanExecutionService (validates DAG)
      → AutonomousRunController (timeout wrapper)
        → AutonomousTaskOrchestrator
          → TaskStateMachine.claim_attempt() (atomic)
          → WorkspaceManager.create_workspace() (isolated worktree)
          → DevelopmentManager.compile() (prompt)
          → DevelopmentAgent.execute() (AI coding)
          → TaskStateMachine.transition(RUNNING → VERIFYING)
          → VerificationGate.run() (tests, lint, build)
          → If PASS → WorkspaceManager.create_checkpoint() → COMMITTED
          → If FAIL → REPAIR_REQUIRED or FAILED
```

### 18.4 Security Properties

- Model output never constitutes verification success
- Application determines verification outcome (VerificationGate)
- Workspace isolation prevents primary worktree mutation
- Execution token binds workspace to task
- Repair diagnostics bounded at 4096 chars and labeled "untrusted DATA only"
- Kilo runs in `--pure` mode; Git authority enforced by WorkspaceManager
- No prompt → shell execution path
- No prompt → workspace control path

### 18.5 Current Limitations

- Only 3 tool implementations registered (system.get_time, files.read_file, dev.test)
- Real E2E coding blocked by external provider quota/credit limits
- Kilo session tracking gap (pre-existing)
- VerificationGate result persistence gap (pre-existing)
- Audit event coverage gap for autonomous runs (pre-existing)

---

## 19. EVENTS / HISTORY / AUDIT

### 19.1 Distinctions

| Concept | Definition | Authority | Mutability |
|---------|-----------|-----------|------------|
| Event | Durable, immutable, append-only record of what happened | EventEmitter | Append-only |
| History | Read-side projection over Events | HistoryService | Derived (no mutation) |
| Audit | Decision trail for tool executions and permission evaluations | AuditLogService | Append-only |
| Task State | Current execution state of a task | TaskStateMachine | Controlled transitions |
| Memory | Knowledge and learned context | MemoryService | CRUD within authority rules |

### 19.2 Event System

**Event:** `event_id` (PK), `event_type`, `occurred_at`, `recorded_at`, `source`, `task_id`, `plan_id`, `execution_id`, `correlation_id`, `causation_id`, `payload` (sanitized, max 4KB), `metadata` (max 1KB).

**EventEmitter:** Emits to `EventRepository` and optionally triggers notifications.

**EventRepository:** Persists events with synchronous emission within transaction for Tier 1 events.

**Sanitization:** `sanitize_payload` redacts sensitive keys and truncates to `AUDIT_PAYLOAD_MAX_BYTES = 4096`.

### 19.3 History

**HistoryService:** Read projection over Events. No new tables required. Paginated (default 50, max 500, offset max 100,000).

### 19.4 Audit

**AuditLogService:** Records tool execution decisions and results. Fail-soft — audit failures never alter permission decisions.

### 19.5 Future Observability Goals

- Correlation across events (task → plan → execution → tool → approval → memory)
- Time-travel debugging (replay events to reconstruct state)
- Performance metrics (tool execution times, model latency)
- Error pattern analysis
- Automated anomaly detection

---

## 20. SCHEDULER / NOTIFICATIONS

### 20.1 Current Scheduler

**SchedulerWorker:** Background worker started in lifespan.

**ScheduledJob model:** `job_id`, `name`, `enabled`, `schedule_type` (ONCE, INTERVAL, CRON), `schedule_expr`, `timezone`, `target_type` (task/plan), `target_id`, `last_run_at`, `next_run_at`, `occurrence_id`, `claim_token`, `claimed_at`, `lease_until`, `execution_id`.

**State machine:** PENDING → CLAIMED → TRIGGERED → COMPLETED/FAILED/SKIPPED.

**Misfire policy:** SKIP_ONCE.

**Concurrency policy:** SKIP (no overlap).

**REST control plane:** 8 endpoints (CRUD, enable/disable, history).

### 20.2 Future Extensions

- Recurring workflows with complex branching
- Event-driven triggers (file changes, emails, calendar events)
- Proactive background tasks
- User-configurable notification policies
- Escalation chains
- Batch processing
- Quiet periods
- Dependency chains between scheduled jobs

---

## 21. SECURITY ARCHITECTURE

### 21.1 Threat Model Summary

| Threat | Attack Surface | Current Mitigation | Residual Risk | Future Mitigation |
|--------|---------------|-------------------|---------------|-------------------|
| Prompt injection | Model input/output | System instruction warning; all model output is data | Medium (model may still be influenced) | Input/output filtering, prompt hardening |
| Malicious model output | Tool calls, text | PermissionEngine, ToolExecutionService, fail-closed | Low-Medium | Enhanced output validation |
| Malicious tool output | Tool results | Treated as untrusted data; no execution | Low | Output sanitization, content scanning |
| Forged identity | HTTP headers | Server-side RequestContext; never from input | Low | Multi-factor, session binding |
| Stolen session | Token theft | In-memory store, localhost-only, short timeout | Medium (token can be reused within window) | Persistent encrypted store, token rotation |
| Local same-user process | Localhost HTTP | CORS limited, but no additional auth | Medium | Mutual TLS, process binding |
| Other-user local process | Windows ACL, localhost | `127.0.0.1` binding only | Low-Medium | Named pipe IPC, Windows user isolation |
| Path traversal | File tool arguments | Workspace containment, absolute path rejection, `..` rejection | Low (TOCTOU window) | OS-level sandboxing |
| Symlink/junction escape | Filesystem | Symlink rejection, re-resolve after resolution | Low-Medium (junctions not fully tested) | Windows reparse point detection |
| Command injection | Tool arguments | No shell execution; JSON Schema validation | Very Low | Input sanitization |
| Argument injection | Tool arguments | JSON Schema validation, size limits | Low | Strict type checking |
| Environment leakage | Logs, context | Secret masking in logs | Low | Environment variable scrubbing |
| Secret leakage | Logs, audit | `sanitize_payload` redacts sensitive keys | Low | Encryption at rest for secrets |
| Approval replay | Approval token | Immutable token, state binding, expiration | Low | Token rotation, shorter TTL |
| Permission escalation | Grant manipulation | Atomic grant consumption, DB constraints | Low | Audit logging, anomaly detection |
| Stale authorization | Timeouts | Session timeout, grant expiration, approval expiration | Low | Active session validation |
| Race conditions | Concurrent requests | Atomic SQL operations, locks | Low | Formal verification of critical paths |
| Malicious files | File tool results | Untrusted data; no execution | Low | File type validation, content scanning |
| Malicious web content | Browser tool (future) | Future: sandboxed browser | Medium | Playwright isolation, content security |
| Malicious application responses | External APIs | Untrusted data; no authority | Low-Medium | Response validation, schema enforcement |
| Provider compromise | External API keys | User-provided keys; minimal data sent | Medium | Data minimization, local fallback |
| External API leakage | Cloud provider calls | Privacy policy, data minimization | Medium | Local model routing for sensitive data |
| Excessive autonomy | Background tasks | Time limits, cancellation, verification | Low | Resource quotas, user-configurable limits |
| Runaway background actions | Scheduler/tasks | Timeouts, cancellation, max attempts | Low | Hard resource limits |
| Resource exhaustion | Untrusted inputs | Size limits, timeouts, bounded reads | Low | Resource quotas |
| Infinite loops | Agent loops | `_LoopLimits` (max iterations, max tool calls) | Low | Watchdog timers |
| Agent-to-agent privilege escalation | Agent communication | Agents are workers, not authorities | Low | Strict capability boundaries |

### 21.2 Security Invariants

1. **Fail-closed:** Any failure in the security pipeline → DENY
2. **DENY never executes:** `ToolExecutor.execute()` never called for DENY
3. **ASK_USER never silently executes:** Approval required before execution
4. **CRITICAL always denied:** No override possible
5. **Model output is data, not authority:** Never alters execution flow
6. **Tool output is untrusted data:** Not executed, only returned
7. **Memory is knowledge, not authority:** Does not grant permissions
8. **Events are immutable evidence:** Cannot alter execution outcomes
9. **Tool definitions are immutable:** Frozen Pydantic models
10. **Approval tokens are immutable:** `__slots__` + `__setattr__` raising `AttributeError`

### 21.3 Hostile Testing

The project maintains extensive hostile security tests:
- `test_security_e2e.py` (1,951 lines, 26 sections A–Z)
- `test_controlled_execution.py` (677 lines, 20 tests)
- `test_tool_execution_service.py` (886 lines, 17 tests)
- `test_permission_engine.py` (690 lines, 14 tests)
- `test_approval_flow.py` (1,098 lines, 24 tests)
- `test_files_read_file.py` (394 lines, 14 tests)
- `test_stage_16_2_hostile_gate.py` (25 tests)
- `test_stage_816_hardening.py` (39 tests)

All tests use real production components where possible. No test uses a mock that bypasses permission checks.

---

## 22. PRIVACY

### 22.1 Privacy Philosophy

The user wants privacy to matter strongly, but is willing to use external models/APIs when they provide substantial value.

### 22.2 Local-First Possibilities

- Ollama provides fully local AI inference
- SQLite is fully local
- All data stays on the user's machine by default
- External APIs are opt-in via provider configuration

### 22.3 Sensitive-Data Classification

**Categories:**
- **Public:** General knowledge, non-sensitive context
- **Internal:** Personal but not secret (preferences, schedule)
- **Confidential:** Financial data, health data, private communications
- **Secret:** API keys, passwords, tokens (never sent to models)

### 22.4 Model/Provider Policy

| Data Sensitivity | Allowed Providers | Routing |
|-----------------|------------------|---------|
| Public | Any | Cost/latency optimized |
| Internal | Local or trusted cloud | User preference |
| Confidential | Local only (default) | Explicit user override required |
| Secret | Never sent to models | CredentialService handles separately |

### 22.5 Data Minimization

- Only necessary context sent to models
- Secrets never included in prompts
- File contents summarized, not sent in full (unless explicitly requested)
- Conversation history bounded

### 22.6 Redaction

- `sanitize_payload` redacts sensitive keys in audit logs
- Log masking for API keys, tokens, Authorization headers
- Result sanitization before audit persistence

### 22.7 User-Configurable Privacy Policy

Future: User should be able to configure:
- Default provider (local vs cloud)
- Data classification rules
- What can be sent to external models
- Retention periods for logs, events, history
- Automatic redaction rules

### 22.8 Logs/Audit Safety

- Audit events contain sanitized arguments and results
- Raw file content not persisted in audit logs
- Secret keys redacted in diagnostic logs
- Structured logging with configurable levels

### 22.9 Secrets Handling

- `CredentialService` handles encryption/decryption
- API keys stored separately from model context
- Model receives only ability to use tools, not raw credentials

---

## 23. API / CAPABILITY PROTOCOL

### 23.1 Concept

The Capability Protocol is a **future stable contract** through which external applications integrate with AI Operator.

### 23.2 Requirements

| Requirement | Description |
|-------------|-------------|
| Capability discovery | List available capabilities with metadata |
| Capability metadata | Risk level, parameters, outputs, version |
| Typed inputs | JSON Schema validation |
| Typed outputs | JSON Schema validation |
| Permissions | Via PermissionEngine |
| Authorization | Via PermissionEngine + ApprovalService |
| Audit | Via AuditLogService |
| Idempotency | Idempotency keys for retry safety |
| Errors | Structured error codes |
| Versioning | Capability version in contract |
| Event emission | Domain events to EventEmitter |
| Domain ownership | Application owns its data |

### 23.3 Generic Contract

```json
{
  "capability_id": "finora.get_transactions",
  "version": "1.0",
  "name": "Get Transactions",
  "description": "Retrieve financial transactions",
  "risk_level": "medium",
  "requires_confirmation": false,
  "input_schema": { ... },
  "output_schema": { ... },
  "idempotent": true,
  "domain": "finora"
}
```

### 23.4 Integration Flow

```
External App
  → Capability API (HTTP/gRPC/MCP)
    → ToolRegistry (if exposed as tool)
      → ToolExecutionService
        → PermissionEngine
        → ApprovalService
        → ToolExecutor (app-specific adapter)
          → External App API
        → AuditLogService
        → EventEmitter
```

### 23.5 Scope

Should support:
- Finora (finance)
- EVOLVE (training)
- Future applications
- External services
- Plugins/capabilities

---

## 24. UI / USER CONTROL

### 24.1 Future Operator UI

The user should always be able to understand what the system is doing.

### 24.2 UI Components

| Component | Purpose |
|-----------|---------|
| Chat / Assistant | Main interaction interface |
| Quick / Deep mode selector | Interaction depth control |
| Active tasks | Current task progress |
| Approvals | Pending approval requests |
| Autonomy settings | Per-capability and global autonomy |
| Permission profiles | Granular capability permissions |
| Memory browser | Inspect, search, edit memory |
| History / Events | Complete activity log |
| Notifications | Proactive alerts |
| Connected applications | Finora, EVOLVE, future apps |
| Model/provider settings | Provider configuration |
| Privacy settings | Data classification, routing policy |
| Emergency stop | Halt all autonomous activity |
| Active agents | What agents are working on |

### 24.3 Transparency Requirements

- Every tool execution visible in history
- Every permission decision auditable
- Every approval request shown to user
- Every autonomous task tracked with status
- Every model interaction logged
- Every event recorded with timestamp and source

---

## 25. CURRENT STATE MATRIX

| Component | Current Status | Evidence | Limitations | Future Target |
|-----------|--------------|----------|-------------|---------------|
| Desktop Shell (Tauri) | IMPLEMENTED + HARDENED | Stage reports, production EXE verified | React UI basic | Rich, full-featured UI |
| FastAPI Core | IMPLEMENTED + HARDENED | 2365 tests passing | Monolith (acceptable for V1) | Same (V1 architecture correct) |
| SQLite Persistence | IMPLEMENTED + HARDENED | 14 tables, auto-migrations | No PostgreSQL sync | SQLite remains primary for V1 |
| Identity/Session | IMPLEMENTED + HARDENED | Stage 16.2 hostile gate passed | In-memory sessions, no multi-user | Persistent sessions, multi-user (future) |
| Bootstrap Secret | IMPLEMENTED + HARDENED | Stage 16.2 hostile gate passed | Single bootstrap per install | Key rotation (future) |
| Auth Middleware | IMPLEMENTED + HARDENED | Stage 16.2 hostile gate passed | Session-only, no MFA | MFA, biometrics (future) |
| RequestContext | IMPLEMENTED + HARDENED | Stage 16.2 hostile gate passed | Single actor per request | Multi-actor (future) |
| PermissionEngine | IMPLEMENTED + HARDENED | 13-rule cascade, hostile tests | Global autonomy mode only | Per-capability profiles |
| ApprovalService | IMPLEMENTED + HARDENED | Immutable tokens, atomic transitions | No approval history UI | Approval center UI |
| Tool Registry | IMPLEMENTED + HARDENED | Thread-safe, duplicate rejection | 3 tools registered | Expand tool set |
| Tool Execution Service | IMPLEMENTED + HARDENED | Enforces pipeline order, hostile tests | Limited tool set | More tools |
| Audit Log | IMPLEMENTED + HARDENED | Fail-soft, sanitized payloads | No public audit API | Audit UI |
| Events | IMPLEMENTED + HARDENED | Immutable, append-only, 20+ event types | No deterministic event IDs in V1 | Deterministic IDs |
| History | IMPLEMENTED + HARDENED | Paginated queries, correlation | Read-only projection | Enhanced search |
| Memory Service | IMPLEMENTED + HARDENED | Trust levels, scope, supersession, events | No retrieval/search API | Search, embeddings, UI |
| Memory Event Integration | IMPLEMENTED + HARDENED | 4 event types, atomic transactions | No causation_id in V1 | Full correlation |
| Scheduler | IMPLEMENTED + HARDENED | CRUD, enable/disable, misfire policy, handoff | 5-minute lease, no plan duplicate prevention | Enhanced scheduling |
| Scheduler REST API | IMPLEMENTED + HARDENED | 8 endpoints verified | No scheduler-specific auth | Authorization |
| Notifications | IMPLEMENTED + HARDENED | Idempotent, replay, internal-only | No public API, no user preferences | Notification center UI |
| Notification Policy | IMPLEMENTED + HARDENED | Pure function, hostile-tested | Basic policy only | User-configurable policies |
| AI Chat | IMPLEMENTED + HARDENED | E2E with OpenRouter | Blocked by provider quota | Local model support |
| AI Provider Abstraction | IMPLEMENTED + HARDENED | Ollama, Kimi, OpenRouter | No automatic fallback | Model Router |
| PlanManager | IMPLEMENTED + HARDENED | Provider-neutral plan generation | No persistence | Plan persistence |
| AI Manager Gate | IMPLEMENTED + HARDENED | Capability-based authorization | Basic policy | Enhanced policies |
| DevelopmentAgent | IMPLEMENTED + HARDENED | Provider-neutral interface | Limited tool set | Full tool access |
| KiloDevelopmentAgent | IMPLEMENTED + HARDENED | Kilo CLI wrapper | Session tracking gap | Improved tracking |
| WorkspaceManager | IMPLEMENTED + HARDENED | Git worktree isolation, checkpoint, rollback | No concurrent workspace sharing | Enhanced isolation |
| VerificationGate | IMPLEMENTED + HARDENED | Fail-closed, 5 check types | Result persistence gap | More check types |
| RecoveryManager | IMPLEMENTED + HARDENED | Stale run recovery | Basic recovery | Advanced recovery |
| Task State Machine | IMPLEMENTED + HARDENED | Atomic claims, 10+ states | Basic states only | Enhanced states |
| BackgroundExecutionManager | IMPLEMENTED + HARDENED | HTTP-triggered, per-plan lock | Basic | Enhanced |
| AutonomousRunController | IMPLEMENTED + HARDENED | Timeout, cancel, abort | Basic | Enhanced |
| AutonomousPlanExecutor | IMPLEMENTED + HARDENED | DAG loop, yield on active tasks | Basic | Enhanced |
| Model Router | DESIGNED | — | Not implemented | Central routing |
| Information Sufficiency | DESIGNED | — | Not implemented | Core future capability |
| Computer Use | NOT STARTED | — | Not implemented | Future fallback |
| Finora Integration | DESIGNED | — | Not implemented | Future application |
| EVOLVE Integration | DESIGNED | — | Not implemented | Future application |
| Capability Protocol | DESIGNED | — | Not implemented | Future integration layer |
| Browser Agent | NOT STARTED | — | Not implemented | Future |
| Email Integration | NOT STARTED | — | Not implemented | Future |
| Calendar Integration | NOT STARTED | — | Not implemented | Future |
| Proactive Behavior | PARTIAL | NotificationPolicy exists | Basic only | Enhanced proactive system |
| 24/7 Runtime | PARTIAL | SchedulerWorker, BackgroundExecutionManager | Event-driven but limited | Full event system |

---

## 26. GAP ANALYSIS

### 26.1 Critical Gaps

| Gap | Impact | Dependency | Risk | Complexity |
|------|--------|-----------|------|-----------|
| No actual file write/delete tools | Cannot organize files | Tool framework exists | Medium | Low |
| No tool implementations beyond read_time + read_file | Limited AI capabilities | Tool framework exists | High | Medium |
| No memory retrieval/search | Memory useless for AI | Memory write exists | Medium | Low |
| No actual autonomous coding E2E (blocked by provider quota) | Cannot verify full pipeline | Provider access | High | Low |
| No per-capability permission profiles | Coarse autonomy control | PermissionEngine exists | Medium | Medium |

### 26.2 High Priority Gaps

| Gap | Impact | Dependency | Risk | Complexity |
|------|--------|-----------|------|-----------|
| No browser automation | Limited web interaction | Playwright integration | Medium | High |
| No email integration | No email management | OAuth, email APIs | Medium | High |
| No Computer Use fallback | Cannot interact with UI-only systems | OS-level automation | High | High |
| No Model Router | Manual provider/model selection | Provider abstraction exists | Medium | Medium |
| No Information Sufficiency | AI cannot determine readiness | AI Manager exists | Medium | High |
| No public notification API | No UI for notifications | Notification system exists | Low | Low |

### 26.3 Medium Priority Gaps

| Gap | Impact | Dependency | Risk | Complexity |
|------|--------|-----------|------|-----------|
| No persistent session storage | Sessions lost on restart | Identity layer exists | Low | Low |
| No session revocation broadcast | Stolen tokens valid until expiry | Session store exists | Low | Low |
| No Finora/EVOLVE integration | Domain applications isolated | Capability protocol needed | Medium | Medium |
| No vector DB / semantic search | Limited memory search | Memory exists | Low | Medium |
| No evaluation system | Cannot measure AI quality | E2E tests exist | Medium | Medium |

### 26.4 Low / Future Gaps

| Gap | Impact | Dependency | Risk | Complexity |
|------|--------|-----------|------|-----------|
| No multi-user support | Single user only | Architecture | Low | High |
| No remote access | Local only | Desktop shell | Low | High |
| No mobile UI | Desktop only | Tauri mobile | Low | High |
| No cloud sync | Local only | Infrastructure | Low | High |
| No plugin system | Fixed capability set | Architecture | Low | High |

---

## 27. MASTER ROADMAP

### 27.1 Guiding Principles for Sequencing

1. Security foundations before capabilities
2. Verified vertical slices before horizontal expansion
3. Local before cloud
4. API before UI automation
5. Deterministic boundaries before AI expansion
6. Each stage ends with hostile audit and full regression

### 27.2 Recommended Stage Sequence

| Stage | Name | Goal | Dependencies | Deliverables | Security | Verification | Exit Criteria |
|-------|------|------|-------------|-------------|----------|-------------|---------------|
| **Stage 18** | File Tool Expansion | Write, copy, move, delete-to-recycle-bin, search, list | files.read_file, ToolExecutionService, PermissionEngine | 5+ file tools, workspace containment, recycle bin | Path security, workspace containment | 50+ tests, hostile audit, full regression | All file tools pass security tests |
| **Stage 19** | Memory Retrieval | Search, semantic search, retrieval API | MemoryService write, Event system | Memory search API, filters, basic retrieval | No authority leakage | 30+ tests, full regression | Memory searchable and retrievable |
| **Stage 20** | Per-Capability Permissions | Granular autonomy profiles | PermissionEngine, settings | Per-capability policy, UI for settings | Policy enforcement | 40+ tests, hostile audit | Capability policies enforced |
| **Stage 21** | Model Router | Automatic model/provider selection | Provider abstraction, provider health | Model registry, capability metadata, routing logic | Provider isolation | 30+ tests, full regression | Automatic routing works |
| **Stage 22** | Information Sufficiency | Determine task readiness | AI Manager, question engine | Information requirements, readiness assessment | No authority leakage | 30+ tests, full regression | Questions generated appropriately |
| **Stage 23** | Browser Automation | Playwright integration | Tool framework, browser | Browser tools, session management, approval | Sandboxed browser | 40+ tests, hostile audit | Browser automation works |
| **Stage 24** | Email Integration | Read/classify/summarize/draft email | OAuth, email APIs, tool framework | Email tools, classification, read-only first | Credential safety | 30+ tests, full regression | Read-only email works |
| **Stage 25** | Calendar Integration | Calendar read/management | Calendar APIs, tool framework | Calendar tools | Credential safety | 30+ tests | Calendar read works |
| **Stage 26** | Finora Capability Integration | Finance domain operations | Capability protocol, Finora API | Finora tools, auth, audit | Domain isolation | 40+ tests, full regression | Finora operations work |
| **Stage 27** | EVOLVE Capability Integration | Training domain operations | Capability protocol, EVOLVE API | EVOLVE tools, auth, audit | Domain isolation | 40+ tests, full regression | EVOLVE operations work |
| **Stage 28** | Computer Use Fallback | UI automation as last resort | OS automation, screen capture | Computer Use tools, bounded execution | Strict containment | 50+ tests, hostile audit | UI automation works within bounds |
| **Stage 29** | Proactive Behavior Enhancement | Intelligent notifications | Scheduler, events, memory | Relevance scoring, cooldown, deduplication | No notification spam | 30+ tests | Proactive notifications relevant |
| **Stage 30** | Persistent Sessions | Session recovery across restarts | Identity layer, encrypted storage | Persistent session store, recovery | Encryption, secure storage | 20+ tests | Sessions survive restart |
| **Stage 31** | Evaluation System | Measure AI quality | E2E tests, metrics | Evaluation framework, A/B testing | No metric manipulation | 20+ tests | Evaluation metrics reliable |

### 27.3 What Should NOT Be Built Yet

- Multi-user SaaS architecture
- OAuth unless actually required for specific integrations
- Generic arbitrary process execution
- Unrestricted shell
- Unrestricted Git API
- Premature distributed architecture
- Independent AI brains in every app
- Uncontrolled autonomous agents
- Unnecessary microservices
- Premature vector database
- Arbitrary plugin execution
- Remote access / mobile UI (before local stabilization)

---

## 28. NEAR-TERM ROADMAP (10–20 Concrete Stages)

Based on current repository state, the next approximately 10–20 stages should be:

| # | Stage | Objective | Dependency | Importance | Major Risks | Acceptance Criteria |
|---|-------|-----------|-----------|------------|-------------|-------------------|
| 1 | Stage 18 | File Tool Expansion | Tool framework | HIGH | Path security regressions | 5+ file tools pass hostile audit |
| 2 | Stage 19 | Memory Retrieval | MemoryService | HIGH | Authority leakage | Memory searchable via API |
| 3 | Stage 20 | Per-Capability Permissions | PermissionEngine | HIGH | Policy complexity | Capability policies enforced |
| 4 | Stage 21 | Model Router | Providers | MEDIUM | Provider failures | Automatic routing with fallback |
| 5 | Stage 22 | Information Sufficiency | AI Manager | MEDIUM | Question quality | Readiness assessment works |
| 6 | Stage 23 | Browser Automation | Playwright | HIGH | Sandbox escape | Browser automation works |
| 7 | Stage 24 | Email Integration | OAuth | MEDIUM | Credential safety | Read-only email works |
| 8 | Stage 25 | Calendar Integration | Calendar APIs | MEDIUM | Credential safety | Calendar read works |
| 9 | Stage 26 | Finora Integration | Capability protocol | HIGH | Domain isolation | Finora tools work |
| 10 | Stage 27 | EVOLVE Integration | Capability protocol | HIGH | Domain isolation | EVOLVE tools work |
| 11 | Stage 28 | Computer Use | OS automation | HIGH | Security containment | UI automation bounded |
| 12 | Stage 29 | Proactive Behavior | Scheduler, events | MEDIUM | Notification spam | Relevance scoring works |
| 13 | Stage 30 | Persistent Sessions | Identity | MEDIUM | Encryption safety | Sessions survive restart |
| 14 | Stage 31 | Evaluation System | Metrics | LOW | Metric manipulation | Evaluation reliable |
| 15 | Stage 32 | Vector Search | Memory | LOW | Complexity | Semantic memory search |
| 16 | Stage 33 | Remote Access | Desktop shell | LOW | Security | Secure remote panel |
| 17 | Stage 34 | Mobile UI | Tauri mobile | LOW | Complexity | Mobile companion app |

---

## 29. LONG-TERM ROADMAP

### 29.1 Realistic Architecture Evolution

1. **Foundation Completion** (Stages 18–20): Complete file tools, memory retrieval, per-capability permissions
2. **Expansion** (Stages 21–25): Model Router, Information Sufficiency, Browser, Email, Calendar
3. **Application Integration** (Stages 26–27): Finora, EVOLVE, capability protocol
4. **Fallback & Proactivity** (Stages 28–29): Computer Use, enhanced proactive behavior
5. **Resilience** (Stages 30–31): Persistent sessions, evaluation system
6. **Optional Enhancements** (Stages 32–34): Vector search, remote access, mobile UI

### 29.2 Speculative Future Ideas

- Multi-device synchronization
- Cloud backup with end-to-end encryption
- Multi-user family profiles
- Voice interaction
- Wearable integration
- Smart home integration
- Custom model fine-tuning on user data
- Autonomous software installation (with explicit permission)
- Self-hosted remote access (no cloud dependency)

---

## 30. NON-GOALS / DEFERRED

### 30.1 Explicitly Deferred (Do Not Build Yet)

| Item | Reason |
|------|--------|
| Multi-user SaaS architecture | Single-user local-first design; requires massive architectural changes |
| OAuth 2.0 | Not needed until external integrations require it |
| Generic arbitrary process execution | Security risk; use dedicated tools instead |
| Unrestricted shell | Security risk; use dedicated tools with permission gating |
| Unrestricted Git API | Use WorkspaceManager; direct Git access bypasses security |
| Premature distributed architecture | Monolith is correct for V1 |
| Independent AI brains in every app | Violates central intelligence principle |
| Uncontrolled autonomous agents | Must remain subordinate to AI Manager |
| Unnecessary microservices | Monolith with clear layers is correct for V1 |
| Premature vector database | Simple retrieval first; vector DB when justified |
| Arbitrary plugin execution | Security risk; use controlled capability protocol |
| Remote access / mobile UI | Before local stabilization |
| Cloud synchronization | Before local stability |
| Multi-device support | Before single-device completeness |

### 30.2 Intentionally Excluded from Scope

- Generic chatbot (AI Operator is not a chatbot)
- Coding assistant (AI Operator is a development manager, not just a code completer)
- Automation script collection (AI Operator is an integrated system, not scripts)
- Unrelated agents (agents are coordinated, not independent)

---

## 31. ARCHITECTURAL DECISIONS

### 31.1 ADR-001: Tauri + React + Python FastAPI

**Decision:** Desktop shell is Tauri + React + TypeScript; Core is Python FastAPI.

**Alternatives considered:** Electron, pure Python GUI, pure Rust, .NET

**Why rejected:** Electron is heavy; pure Python GUI lacks modern UI capabilities; pure Rust too complex for rapid iteration; .NET breaks local-first privacy model.

**Current status:** Implemented and verified.

**Revisit conditions:** If performance becomes unacceptable, if Tauri v2 breaks compatibility, if user demands cross-platform.

### 31.2 ADR-002: SQLite as Primary Database

**Decision:** SQLite with aiosqlite/SQLAlchemy async.

**Alternatives considered:** PostgreSQL, MySQL, embedded key-value store

**Why rejected:** Single-user local-first; SQLite is simpler, requires no server, sufficient performance.

**Current status:** Implemented with 14+ tables.

**Revisit conditions:** Multi-user support, cloud sync, performance at scale.

### 31.3 ADR-003: Monolith with Clear Layers

**Decision:** Single Python process with layered architecture (API → Services → Repositories).

**Alternatives considered:** Microservices, modular monolith, serverless

**Why rejected:** Single local user; microservices add complexity without benefit; modular monolith is premature.

**Current status:** Implemented.

**Revisit conditions:** Team size growth, deployment complexity, scaling needs.

### 31.4 ADR-004: In-Memory Session Store

**Decision:** SessionStore is in-memory dictionary.

**Alternatives considered:** Redis, encrypted SQLite sessions, JWT

**Why rejected:** Single local user; Redis requires external dependency; JWT cannot be revoked; encrypted SQLite adds complexity.

**Current status:** Implemented.

**Revisit conditions:** Persistent session requirement, multi-device sync, security audit findings.

### 31.5 ADR-005: Bootstrap Secret for Initial Authentication

**Decision:** Local file-based bootstrap secret for session creation.

**Alternatives considered:** Password, Windows Hello, no auth, certificate

**Why rejected:** Password adds friction; Windows Hello is complex; no auth is insecure; certificate is overkill for local single-user.

**Current status:** Implemented.

**Revisit conditions:** Multi-user support, security audit, user feedback.

### 31.6 ADR-006: Tool Execution Pipeline (PermissionEngine → ToolExecutor)

**Decision:** Central ToolExecutionService enforces permission evaluation before execution.

**Alternatives considered:** Permission checks in each tool, decorator-based, middleware

**Why rejected:** Per-tool checks are error-prone; decorators can be bypassed; middleware doesn't fit async tool execution.

**Current status:** Implemented and hostile-gated.

**Revisit conditions:** Performance optimization, new tool types, architectural changes.

### 31.7 ADR-007: Fail-Closed Security Model

**Decision:** Any failure in the security pipeline results in DENY.

**Alternatives considered:** Fail-open, fail-neutral, retry-on-failure

**Why rejected:** Fail-open is catastrophic for security; fail-neutral is ambiguous; retry-on-failure adds complexity.

**Current status:** Implemented across all security boundaries.

**Revisit conditions:** None; this is a permanent architectural principle.

### 31.8 ADR-008: CRITICAL Risk Is Absolutely Denied

**Decision:** CRITICAL risk-level operations cannot be authorized by any grant, approval, autonomy mode, or confirmation.

**Alternatives considered:** Allow with explicit override, allow with multi-factor approval, allow with time delay

**Why rejected:** Too complex; absolute denial is safer and simpler for V1.

**Current status:** Implemented.

**Revisit conditions:** User demand for specific critical operations, operational experience.

### 31.9 ADR-009: Immutable Tool Definitions and Approval Tokens

**Decision:** ToolDefinition is Pydantic frozen; ApprovalVerificationToken is immutable via `__slots__`.

**Alternatives considered:** Mutable definitions, versioned definitions, copy-on-write

**Why rejected:** Mutable definitions enable tampering; versioned adds complexity; copy-on-write unnecessary.

**Current status:** Implemented.

**Revisit conditions:** Tool versioning requirements, definition migration needs.

### 31.10 ADR-010: Workspace Isolation via Git Worktrees

**Decision:** Autonomous tasks execute in isolated Git worktrees, never the primary worktree.

**Alternatives considered:** In-place editing, Docker containers, filesystem sandboxing

**Why rejected:** In-place editing risks primary worktree; Docker is heavy; filesystem sandboxing is OS-specific and complex.

**Current status:** Implemented.

**Revisit conditions:** Performance issues, non-Git projects, alternative isolation mechanisms.

### 31.11 ADR-011: VerificationGate as Sole Verification Authority

**Decision:** VerificationGate is the only authority that can determine verification success.

**Alternatives considered:** Model self-reporting, hybrid model+test verification, developer self-certification

**Why rejected:** Model self-reporting is untrustworthy; hybrid is complex; self-certification is meaningless.

**Current status:** Implemented.

**Revisit conditions:** None; this is a permanent security principle.

### 31.12 ADR-012: Event Store as Immutable Append-Only Log

**Decision:** Events are append-only, never modified or deleted.

**Alternatives considered:** Mutable events, event versioning, event compaction

**Why rejected:** Mutable events undermine auditability; versioning adds complexity; compaction loses evidence.

**Current status:** Implemented.

**Revisit conditions:** Storage constraints, retention policy requirements.

### 31.13 ADR-013: No Dynamic Execution in Tool Layer

**Decision:** Tool implementations cannot import subprocess, eval, exec, or shell execution modules.

**Alternatives considered:** Allow with permission, sandboxed execution, restricted shell

**Why rejected:** Too risky; dedicated tools are safer; shell execution should be a separate, heavily restricted capability if ever needed.

**Current status:** Enforced by code review and hostile tests.

**Revisit conditions:** User demand for shell tools, with extensive security review.

---

## 32. KNOWN LIMITATIONS

### 32.1 Technical Limitations

| Limitation | Impact | Planned Resolution |
|------------|--------|-------------------|
| In-memory session store | Sessions lost on restart | Stage 30: Persistent sessions |
| No memory retrieval/search | Memory useless for AI context | Stage 19: Memory retrieval |
| Only 3 tool implementations | Limited AI capabilities | Stage 18: File tool expansion |
| No vector DB / semantic search | Limited memory and document search | Stage 32: Vector search |
| No deterministic event IDs | Event idempotency relies on PK | Future enhancement |
| No causation_id for memory events | Limited event correlation | Future enhancement |
| No public notification API | No UI for notifications | Stage 29: Notification center |
| SQLite only | No cloud sync, no multi-device | Future (deferred) |
| Windows-only | No cross-platform | Future (deferred) |
| Single user | No multi-user support | Future (deferred) |

### 32.2 Security Limitations

| Limitation | Impact | Planned Resolution |
|------------|--------|-------------------|
| TOCTOU in file tools | Theoretical race condition | OS-level sandboxing (future) |
| Windows junctions not fully tested | Potential escape vector | Explicit testing (Stage 18) |
| Mixed path separators not tested | Potential path confusion | Explicit testing (Stage 18) |
| Localhost-only HTTP | Vulnerable to local same-user process | Mutual TLS (future) |
| No MFA | Single-factor authentication | Future enhancement |
| Session tokens reusable until expiry | Token theft risk | Shorter timeout, rotation (future) |
| Provider compromise can leak context | Sensitive data exposure | Data minimization, local routing |

### 32.3 Provider Limitations

| Limitation | Impact | Planned Resolution |
|------------|--------|-------------------|
| OpenRouter quota/credit limits | E2E coding blocked | Local model support (Stage 21) |
| Ollama model availability | Limited model selection | Model download management (existing) |
| No automatic provider fallback | Manual provider switching | Stage 21: Model Router |
| No provider health tracking | Unclear failure modes | Stage 21: Model Router |

### 32.4 Windows Limitations

| Limitation | Impact | Planned Resolution |
|------------|--------|-------------------|
| Windows junctions/reparse points | Path escape potential | Explicit testing, detection |
| Windows ACL complexity | Local secret file protection | Current mitigation sufficient |
| No WSL support | Linux tools unavailable | Future (if needed) |

### 32.5 External Model Dependency

| Limitation | Impact | Planned Resolution |
|------------|--------|-------------------|
| Cloud models require API keys | User configuration burden | Settings UI (existing) |
| Cloud models have privacy implications | Data sent externally | Data minimization, local routing |
| Cloud models have rate limits | Service interruptions | Model Router fallback |

### 32.6 Current E2E Limitations

| Limitation | Impact | Planned Resolution |
|------------|--------|-------------------|
| Real E2E coding blocked by provider quota | Cannot verify full autonomous pipeline | Local model support |
| Kilo session tracking gap | Incomplete Kilo integration audit | Future fix |
| VerificationGate result persistence gap | Verification results not fully persisted | Future fix |
| Audit event coverage gap for autonomous runs | Incomplete audit trail | Future fix |

### 32.7 Deferred Capabilities

- Computer Use
- Browser automation
- Email integration
- Calendar integration
- Finora integration
- EVOLVE integration
- Vector search
- Remote access
- Mobile UI
- Multi-user support
- Cloud sync
- Plugin system

---

## 33. VERIFICATION STRATEGY

### 33.1 Stage Completion Criteria

No stage is considered complete merely because implementation exists. Every stage must pass:

1. **Focused tests:** Unit and integration tests for new functionality
2. **Integration tests:** Tests for interaction with existing components
3. **Security tests:** Permission boundary tests, tampering tests, fail-closed tests
4. **Hostile tests:** Adversarial test cases (path traversal, injection, replay, tampering)
5. **Full regression:** All existing tests must pass
6. **Static audit:** Code review for security boundaries
7. **False-positive test inspection:** Verify tests actually exercise security boundaries (no mocks that bypass)
8. **Git scope verification:** Only intended files modified
9. **Final acceptance:** All criteria documented in final report

### 33.2 Test Counts

| Category | Count (current) | Target per stage |
|----------|----------------|-----------------|
| Total tests | 2365 | +50–200 per stage |
| Security/E2E tests | ~500 | +20–50 per stage |
| Hostile tests | ~300 | +10–30 per stage |

### 33.3 Regression Standard

- All existing tests must pass
- No unexplained failures
- No test modifications to make failures pass (unless fixing legitimate test bugs)
- Real production components used where possible (no permission-bypassing mocks)

---

## 34. DEVELOPMENT PROCESS

### 34.1 Recommended Stage Pattern

For each major feature or capability:

1. **DISCOVERY:** Inspect repository, understand current state, identify gaps, propose approach
2. **CONTRACT:** Define interfaces, schemas, security boundaries, test requirements
3. **IMPLEMENTATION:** Write code, add tests
4. **FOCUSED TESTS:** Unit and integration tests for new functionality
5. **HARDENING:** Security tests, hostile tests, edge cases
6. **HOSTILE AUDIT:** Adversarial testing of security boundaries
7. **FULL REGRESSION:** Run all tests; ensure no regressions
8. **GIT VERIFICATION:** Review changes, verify only intended files modified
9. **FINAL ACCEPTANCE:** Document results, verify all criteria met

### 34.2 Coding AI Rules

1. Inspect actual repository first; do not assume structure
2. Avoid assumptions; verify from code
3. Avoid broad rewrites; make targeted changes
4. Preserve existing security boundaries
5. Report exact test counts
6. Distinguish real failures from environment/provider failures
7. Never declare PASS without evidence
8. Use real production components in tests (no permission-bypassing mocks)
9. Do not add unnecessary comments
10. Do not modify production code during documentation tasks

### 34.3 Why This Process Exists

- Security is non-negotiable and easy to break subtly
- AI coding agents can introduce regressions through well-intentioned changes
- Hostile testing catches adversarial edge cases that normal testing misses
- Full regression ensures no existing functionality is broken
- Documentation-first approach prevents architectural drift

---

## 35. MASTER GLOSSARY

| Term | Definition |
|------|-----------|
| AI Operator | The complete system: desktop shell + Python Core + AI + subsystems |
| AI Manager | The intelligence layer: plans, selects, coordinates, asks |
| Model Router | Future component: selects optimal model/provider per request |
| Agent | Worker subordinate to AI Manager (DevelopmentAgent, etc.) |
| Tool | Explicit, typed, permission-gated capability (files.read_file, etc.) |
| Capability | Generic term for external application integration (Finora, EVOLVE) |
| Permission | Whether a specific tool action is allowed (PermissionEngine) |
| Approval | User confirmation of a tool action (ApprovalService) |
| Autonomy | Policy profile controlling automatic execution (MANUAL, SEMI_AUTONOMOUS, AUTONOMOUS) |
| Quick | Interaction mode: higher tolerance for assumptions, faster execution |
| Deep | Interaction mode: lower tolerance for assumptions, more questions |
| Information Sufficiency | Future capability: determining if enough information is available |
| Memory | Knowledge store: learned context, preferences, facts |
| Event | Immutable append-only record of what happened |
| History | Read-side projection over Events |
| Audit | Decision trail for tool executions and permission evaluations |
| Task | Unit of work with state machine lifecycle |
| Plan | Group of related tasks |
| Run | Single execution attempt of a task |
| Execution | The act of running a tool or agent |
| Session | Server-issued authentication token (ses_<64-hex>) |
| Identity | User identifier derived from Windows SID |
| Actor | Entity performing an action (USER, SYSTEM, AGENT, SERVICE) |
| RequestContext | Server-side context attached to each request |
| Workspace | Isolated Git worktree for autonomous execution |
| VerificationGate | Fail-closed authority for code verification |
| Recovery | Resuming or cleaning up after failed/terminated runs |
| Computer Use | Future fallback: mouse/keyboard/screen UI automation |
| Capability API | Future protocol for external application integration |

---

## 36. ONE-PAGE MASTER SUMMARY

### CURRENT STATE

AI Operator is a **Windows desktop application** (Tauri + React + Python FastAPI + SQLite) implementing a **secure local personal agent foundation**. The repository is at a solid, security-conscious checkpoint with 2365+ passing tests. Recent work (Stages 16–17.3A) hardened identity/session and the tool execution security pipeline.

### CURRENT ARCHITECTURE

```
Tauri Desktop Shell
  → React UI (typed HTTP client)
    → Python FastAPI Core
      → API Routes (auth, health, AI, plans, scheduler, history)
        → Service Layer (AI, orchestration, tools, memory, scheduler)
          → Repository Layer (SQLAlchemy async → SQLite)
```

Key subsystems:
- **Identity/Session:** Bootstrap secret → server-issued session → RequestContext
- **Permissions:** PermissionEngine (13-rule cascade, fail-closed)
- **Tools:** ToolRegistry → ToolExecutionService → PermissionEngine → ToolExecutor
- **Approval:** ApprovalService (immutable tokens, atomic transitions)
- **Audit:** Fail-soft audit logging with sanitized payloads
- **Events:** Immutable append-only event store
- **History:** Read projection over events
- **Memory:** CRUD with trust levels, scope, supersession, events
- **Scheduler:** Background worker with CRUD, misfire policy, handoff
- **Notifications:** Idempotent, internal-only, pure policy function
- **Autonomous Development:** Task lifecycle with workspace isolation, verification gate, repair loop

### CORE PRINCIPLES

1. Human remains ultimate authority
2. Model proposes; application decides; authorized services execute
3. AI must never become the security authority
4. Deterministic code owns permissions, approvals, execution boundaries
5. Fail closed
6. DENY never executes
7. ASK_USER never silently executes
8. CRITICAL cannot be overridden
9. Every important action is auditable
10. Untrusted model output is data, not authority
11. APIs/capabilities preferred over UI automation
12. Computer Use is a fallback, not first layer
13. Background autonomy is event-driven, not a thinking loop
14. Provider-neutral architecture
15. Narrow, testable vertical slices

### NEXT MAJOR STAGES

1. **Stage 18 — File Tool Expansion:** Write, copy, move, delete-to-recycle-bin, search, list
2. **Stage 19 — Memory Retrieval:** Search, retrieval API
3. **Stage 20 — Per-Capability Permissions:** Granular autonomy profiles
4. **Stage 21 — Model Router:** Automatic model/provider selection
5. **Stage 22 — Information Sufficiency:** Determine task readiness
6. **Stage 23 — Browser Automation:** Playwright integration
7. **Stage 24–25 — Email/Calendar:** External integrations
8. **Stage 26–27 — Finora/EVOLVE:** Domain capability integration
9. **Stage 28 — Computer Use:** UI fallback layer
10. **Stage 29–31 — Proactive/Sessions/Evaluation:** Enhanced operation

### LONG-TERM TARGET

A **persistent, event-driven personal operating layer** that manages the user's complete digital environment — files, projects, code, browser, email, calendar, finance, fitness, and future applications — through explicit capabilities, within strict security boundaries, with full transparency, auditability, and user control.

### MAIN SECURITY BOUNDARIES

- ToolExecutionService is the sole execution authority
- PermissionEngine is the sole authorization authority
- TaskStateMachine is the sole state transition authority
- VerificationGate is the sole verification authority
- WorkspaceManager is the sole workspace/Git authority
- CRITICAL risk is absolutely denied
- DENY never executes; ASK_USER never silently executes
- Model output is data, not authority
- Events are immutable; history is read-only

### MAIN CURRENT LIMITATIONS

- Limited tool implementations (3 tools)
- No memory retrieval/search
- No per-capability permissions
- No Model Router
- No Computer Use
- No external application integration (Finora, EVOLVE)
- In-memory sessions (lost on restart)
- External E2E coding blocked by provider quota
- Windows-only, single-user only

---

**END OF MASTER SPECIFICATION**

This document is the authoritative reference for AI Operator architecture, strategy, security, and roadmap. All future development should reference this document and update it when architectural decisions change.
