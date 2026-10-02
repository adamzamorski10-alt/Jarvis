# Stage 14 Strategic Discovery

**Baseline:** `366ddad` — checkpoint: stages 10-13 accepted working tree
**Date:** 2026-09-24
**Status:** STRATEGIC DISCOVERY ONLY — NO IMPLEMENTATION, NO CODE CHANGES

---

## 1. Current Architecture

### 1.1 Application Layers

```
Tauri Desktop Shell
    |
React + TypeScript UI
    |
Typed local HTTP client
    |
Python FastAPI Core (services/core)
    |
    ├── app/api/          — HTTP routes + Pydantic schemas
    ├── app/services/     — Application services (autonomous loop, plan execution, settings, activity)
    ├── app/repositories/ — SQLAlchemy async repositories
    ├── app/db/           — Models + async engine/session
    ├── app/core/         — Config, logging, lifespan
    |
SQLite (aiosqlite) — single file `data/ai_operator.db`
```

### 1.2 Core Subsystems

**AI Manager** (`app/ai_manager/`)
- `AIManager` — thin provider-neutral decision engine. Calls `AIManagerProvider.decide(intent)`, normalizes `RawManagerDecision` → `ManagerDecision`, returns `ManagerResult`.
- `PolicyCapabilityGate` — application-owned authorization boundary. Evaluates `ManagerDecision` against `CapabilityRegistry` + `ApplicationPolicy`. Produces `AuthorizationDecision` (ALLOWED / NEEDS_APPROVAL / DENIED / DISABLED).
- `CapabilityRegistry` — immutable application-owned metadata registry. Maps `CapabilityIdentifier` → `CapabilityDefinition` (risk, enabled, approval_required). Currently 10 hardcoded definitions.
- `AIManagerBridge` / `ExecutionBridge` — bridges AI Manager decisions to planning and execution.
- **Key invariant:** Model proposes; application decides; authorized application services execute.

**Orchestration** (`app/orchestration/`)
- `AutonomousTaskOrchestrator` — coordinates single task lifecycle: PENDING → RUNNING → VERIFYING → PASSED → COMMITTED (or REPAIR_REQUIRED / FAILED / CANCELLED / ABORTED). Enforces: TaskStateMachine is sole state authority, WorkspaceManager is sole workspace authority, VerificationGate is sole verification authority.
- `AutonomousRunController` — wraps orchestrator with timeout, cancellation, abort, and stale-detection. Uses `asyncio.Event` for cancel/abort signals. Creates fresh DB sessions for state recovery after timeout.
- `RecoveryManager` — detects orphaned RUNNING tasks via heartbeat staleness, claims recovery via atomic DB update, transitions to REPAIR_REQUIRED or FAILED. Lease-guarded.
- `DevelopmentAgent` interface — provider-neutral agent contract. `KiloAgent` / `KiloProcess` are current implementations.
- `DevelopmentManager` (prompt_compiler) — compiles `AgentExecutionPrompt` from `DevelopmentAgentContext`.
- `KiloSessionManager` / `KiloSessionModels` — tracks Kilo subprocess sessions in DB.

**Tools / Security** (`app/tools/`)
- `ToolRegistry` — thread-safe in-memory registry of `ToolDefinition` objects. Module-level singleton.
- `ToolImplementationRegistry` — maps tool_id → `BaseTool` implementation.
- `PermissionEngine` — fail-closed authorization. Evaluates `PermissionEvaluationRequest` against registry, grants, autonomy mode, and approval context. CRITICAL risk is always denied. 13 rule-based decisions.
- `ApprovalService` — ASK_USER approval flow. Creates `PendingApproval` records. Emits audit events.
- `ToolExecutionService` — the ONLY production component that calls both PermissionEngine and ToolExecutor. Enforces order: definition lookup → enabled check → argument validation → permission evaluation → (if ALLOW) execution → result validation → audit.
- `ToolExecutor` — executes only pre-registered implementations. No permission checks, no audit.
- **Current implementations:** `dev_test` (SAFE), `system_time` (SAFE). Only 2 tools.

**Event / History / Notification** (`app/events/`, `app/notifications/`)
- `EventEmitter` — application-facing write boundary. Validates, bounds (payload 4KB, metadata 1KB), sanitizes, timestamps, persists via `EventRepository`. Derives notifications via `NotificationPolicy` → `NotificationRepository`.
- `EventRepository` — append-only event store. `event_id` primary key. `EventIntegrityConflict` on duplicate.
- `HistoryService` — read-side query layer over events.
- `NotificationPolicy` — derives `NotificationDraft` from `Event`. Hardcoded per `EventType`. Cannot be manipulated by event payload.
- `NotificationRepository` — persists notifications. `UNIQUE(source_event_id, notification_type)`.

**Scheduler** (`app/scheduler/`)
- `SchedulerService` — control-plane CRUD. Idempotent create. Status-dependent PATCH restrictions. Safe delete.
- `SchedulerWorker` — background polling loop. Claims occurrences via lease. Emits events.
- `SchedulerHandoffService` — durable handoff boundary from scheduler to execution layer.
- `SchedulerRepository` — emits scheduler lifecycle events with deterministic event_ids.

**Execution Chain**
```
REST API
  → BackgroundExecutionManager (in-memory tracking)
    → AutonomousPlanExecutor (multi-task DAG loop)
      → PlanExecutionService (eligibility checks)
        → AutonomousRunController (timeout/cancel/abort)
          → AutonomousTaskOrchestrator (task lifecycle)
            → TaskStateMachine (sole state authority)
              → TaskRepository (persistence)
```

**Verification** (`app/verification/`)
- `VerificationGate` — fail-closed. Executes registered checks in deterministic order. PASS requires all required checks PASS. Cannot authorize execution.
- Checks: lint, pytest, typescript, frontend build, base workspace check.

**Workspace** (`app/workspace/`)
- `WorkspaceManager` — sole authority for Git worktree operations. Creates autonomous worktrees, creates checkpoints (commits), removes worktrees. Never touches primary worktree.

**Persistence**
- Single SQLite file via `aiosqlite`.
- `init_db()` uses raw SQL with `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`.
- Repositories follow pattern: constructor takes `AsyncSession`, methods are `async`, caller owns transaction.
- 15+ DB models: development_tasks, task_dependencies, plan_fingerprints, audit_events, activity_events, app_settings, kilo_sessions, capability_approvals, pending_approvals, permission_grants, scheduled_jobs, notifications, settings, etc.

### 1.3 Control Plane

FastAPI routes: `/api/health`, `/api/system`, `/api/settings`, `/api/activity`, `/api/ai`, `/api/approvals`, `/api/plans`, `/api/history`, `/api/scheduler`.

No notification REST surface (internal-only per V1 contract).

### 1.4 Recovery Mechanisms

- **Task recovery:** `RecoveryManager` detects stale RUNNING tasks via heartbeat, claims recovery atomically, transitions to REPAIR_REQUIRED or FAILED.
- **Run timeout:** `AutonomousRunController` cancels orchestrator after timeout, creates fresh session to verify/transition state.
- **Crash recovery:** On process restart, stale tasks can be recovered. `BackgroundExecutionManager` state is lost (in-memory only).
- **Event replay:** `NotificationReplay` can reconstruct notifications from durable events.

---

## 2. Current Strengths

1. **Exceptional security posture.** PermissionEngine, ApprovalService, and VerificationGate are fail-closed. The model never executes directly; it proposes and the application decides. This is the strongest architectural foundation.

2. **Clear separation of concerns.** AI Manager (decides) is completely isolated from execution, security, and persistence. No circular dependencies. Each layer has a single authority.

3. **Robust event system.** Events are durable, immutable, sanitized, bounded, and idempotent. Notification derivation is policy-driven and cannot be manipulated by event payload.

4. **Production-grade task lifecycle.** TaskStateMachine is sole state authority. Atomic claim patterns prevent double-execution. Workspace isolation (autonomous worktree ≠ primary worktree) is enforced. VerificationGate is sole verification authority. Commit only after PASS.

5. **Recovery is built in, not bolted on.** Heartbeat-based staleness detection, lease-guarded recovery claims, timeout handling with fresh DB sessions, graceful cancellation/abort.

6. **Comprehensive test coverage.** 2196 passed, 4 skipped, 0 failed. Tests use real SQLite databases and assert on persisted state. Hostile audit gates passed for stages 11-13.

7. **Incremental construction.** Each stage has added a focused, tested capability without breaking prior stages. Checkpoints preserve accepted working trees.

---

## 3. Current Bottlenecks

### 3.1 Statelessness Across Restarts (CRITICAL)

The system is fundamentally ephemeral:
- `BackgroundExecutionManager` tracks active executions in memory only. Process restart loses all in-flight plan executions.
- `PlanExecutionState` and `RunStatus` are in-memory only. No persisted run history.
- `KiloSession` records exist in DB but are not used for durable agent context.
- `AutonomousPlanExecutor` loop state is not resumable across restarts.

**Impact:** The system cannot maintain any continuity across restarts. A user who closes the desktop app loses all context of what was running, what was planned, and what the agent was doing.

### 3.2 No Persistent Memory or Knowledge

There is no mechanism for:
- Storing user preferences, habits, or context
- Maintaining project-level knowledge across sessions
- Learning from past execution outcomes
- Building a user/environment model
- Recording agent observations or conclusions

The Event system records *what happened* but not *what was learned*.

**Impact:** The system cannot personalize, cannot improve from experience, and cannot act as a "personal" operating system.

### 3.3 Minimal Tool Surface

Only 2 tools are registered (`dev_test`, `system_time`). The `ToolRegistry` infrastructure is mature (permission, approval, audit, validation, timeout) but the tool catalog is empty for any real work.

**Impact:** The AI Operator cannot interact with the file system, execute code, browse the web, send email, or control the desktop. The execution chain exists but has nothing meaningful to execute.

### 3.4 Thin AI Manager

`AIManager` is a thin wrapper: call provider → normalize → return. It has:
- No planning capabilities (planning is done by external `ManagerPlanningService` / `PlanManager`)
- No model routing or fallback
- No multi-step reasoning
- No context window management
- No decision history or learning

**Impact:** The quality of decisions depends entirely on the prompt and the model. The application has no way to improve decision quality over time or route to specialized models.

### 3.5 No Durable Execution Records

`AutonomousRunResult`, `OrchestrationResult`, `VerificationReport`, and `PlanExecutionResult` are all returned to callers but not persisted. Execution history exists only in scattered `failure_reason` fields on `DevelopmentTask` and `audit_events`.

**Impact:** Cannot analyze execution patterns, cannot debug past failures systematically, cannot compute reliability metrics.

### 3.6 No Monitoring / Observability

- No system health beyond `/api/health` (which just checks DB connectivity)
- No execution telemetry
- No worker health diagnostics
- No alerting on degraded subsystems

**Impact:** Failures are silent until the user notices missing results. No proactive health management.

### 3.7 No Evaluation Framework

No mechanism to:
- Run standardized evaluation tasks
- Measure agent reliability across runs
- Track regression in decision quality or execution success
- A/B test model configurations

**Impact:** Cannot validate improvements or detect regressions in autonomous behavior.

### 3.8 No Workflow Composition

Event → Notification, Scheduler → Execution, and Execution → Event are separate pipelines. There is no durable, composable workflow that spans:
- Trigger on event → schedule action → execute → notify on outcome → react to result

**Impact:** Complex autonomous behaviors require ad-hoc orchestration rather than declarative workflows.

---

## 4. Candidate Stage 14 Directions

### A. Memory / Knowledge Foundation

Persistent user/project/system knowledge, retrieval, provenance, lifecycle, privacy boundaries.

**What it adds:**
- Durable memory entities (user context, project context, agent observations, learned preferences)
- Memory lifecycle (create, update, expire, archive)
- Privacy boundaries (what is remembered vs. forgotten)
- Provenance tracking (source of each memory entry)

**Dependencies:** SQLite persistence (exists), Event system (exists). No new security boundaries.

### B. AI Manager Expansion

Planning/decision capabilities, model routing, richer decision context, better orchestration.

**What it adds:**
- Multi-step planning within AI Manager
- Model/provider routing and fallback
- Decision context enrichment from memory
- Cost/latency-aware model selection

**Dependencies:** AI Manager (exists). Memory would enrich context but is not required for basic expansion.

### C. Computer Use / Desktop Control

Controlled computer interaction, capability boundaries, screenshots/input/actions, safety.

**What it adds:**
- Screenshot capture
- Controlled input (mouse, keyboard)
- Desktop state observation
- Safety boundaries (no uncontrolled desktop modification)

**Dependencies:** Tool registry (exists), PermissionEngine (exists). Requires NEW safety boundary for desktop interaction. High security complexity.

### D. Agent Teams

Multiple specialized agents, delegation, coordination, isolation, authority boundaries.

**What it adds:**
- Agent identity and roles
- Delegation protocols
- Coordination patterns (supervisor, peer, pipeline)
- Isolation boundaries between agents

**Dependencies:** Kilo session management (partial), Memory (needed for coordination context). Significant architectural changes for isolation.

### E. Capability / Tool Registry Expansion

Central registry for tools/capabilities, schemas, permissions, discovery and lifecycle.

**What it adds:**
- New tool implementations (filesystem, shell, browser, etc.)
- Tool lifecycle management
- Dynamic tool discovery

**Dependencies:** Tool registry (exists), PermissionEngine (exists). Incrementally implementable. Low risk.

### F. Autonomous Workflows

Combining Event + Scheduler + Execution + Notification into durable autonomous workflows.

**What it adds:**
- Declarative workflow definitions
- Durable workflow state
- Event-driven workflow triggers
- Multi-step workflow composition

**Dependencies:** Event (exists), Scheduler (exists), Execution (exists), Notification (exists). Needs new workflow composition layer.

### G. Monitoring / Health / Observability

System health, execution telemetry, worker health, diagnostics, recovery visibility.

**What it adds:**
- System health indicators
- Execution telemetry (latency, success rate, retry count)
- Worker health diagnostics
- Recovery visibility

**Dependencies:** Event system (exists). Can be built on existing event stream. Low risk.

### H. Evaluation / Agent Verification

Evaluation framework for autonomous agents, task outcomes, reliability measurements, regression scenarios.

**What it adds:**
- Standardized evaluation tasks
- Reliability metrics
- Regression test scenarios
- A/B testing infrastructure

**Dependencies:** Execution history (partially exists, but not persisted). Needs new evaluation framework.

---

## 5. Dependency Analysis

### 5.1 Dependency Graph

```
Memory/Knowledge Foundation
    ↓ enables: Agent Teams, Workflow Composition, Personalization
    ↓ enriches: AI Manager context, Monitoring context
    ↓ depends on: SQLite, Event system (both exist)

AI Manager Expansion
    ↓ enriches: Decision quality, Planning
    ↓ depends on: AI Manager (exists)
    ↓ optionally enriched by: Memory

Capability/Tool Registry Expansion
    ↓ enables: Computer Use, real work execution
    ↓ depends on: Tool registry, PermissionEngine (both exist)
    ↓ independent of: Memory, AI Manager expansion

Computer Use / Desktop Control
    ↓ depends on: Tool registry (exists), NEW desktop safety boundary
    ↓ blocked by: High security complexity
    ↓ independent of: Memory (can be added later)

Agent Teams
    ↓ depends on: Memory (for coordination context), Kilo sessions (partial)
    ↓ blocked by: Significant architectural changes for isolation

Autonomous Workflows
    ↓ depends on: Event + Scheduler + Execution + Notification (all exist)
    ↓ needs: New workflow composition layer

Monitoring/Observability
    ↓ depends on: Event system (exists)
    ↓ independent of: Memory

Evaluation Framework
    ↓ depends on: Persisted execution records (NOT durable today)
    ↓ blocked by: Need for durable execution history first
```

### 5.2 Incremental Implementability

| Candidate | Can be implemented incrementally? |
|-----------|----------------------------------|
| Memory/Knowledge | YES — start with single memory entity, add types over time |
| AI Manager Expansion | YES — add routing, then planning, then context enrichment |
| Tool Registry Expansion | YES — add one tool at a time |
| Monitoring | YES — start with basic health, add telemetry over time |
| Autonomous Workflows | PARTIAL — composition layer is a new abstraction |
| Computer Use | NO — requires new safety boundary, significant design |
| Agent Teams | NO — requires architectural changes for isolation |
| Evaluation Framework | PARTIAL — requires durable execution records first |

### 5.3 Blocking Analysis

| Capability | Blocks future work? |
|------------|---------------------|
| Memory/Knowledge | Blocks: Agent Teams, long-running workflows, personalization |
| Tool Registry Expansion | Blocks: Any meaningful autonomous work |
| Durable Execution Records | Blocks: Evaluation, Monitoring, Workflow composition |
| AI Manager Expansion | Blocks: Better decision quality (but not architectural) |

### 5.4 Duplication Analysis

- **Memory does NOT duplicate Event system.** Events record *what happened*; memory stores *what is known/learned/preferred*.
- **Tool Registry Expansion does NOT duplicate AI Manager capabilities.** Tools are execution capabilities; AI Manager is decision-making.
- **Monitoring does NOT duplicate Notification.** Notifications are user-facing messages; monitoring is system health telemetry.

---

## 6. Security Implications

### 6.1 Memory/Knowledge Foundation

- **New attack surface:** Memory injection (poisoning knowledge base)
- **Mitigation:** Memory entries are application-written, not model-written. Same trust boundary as Task records.
- **Privacy boundary:** Memory must support selective forgetting. User must be able to delete memory entries.
- **No new execution authority:** Memory is read-only data. It does not grant capabilities or bypass PermissionEngine.

### 6.2 Tool Registry Expansion

- **New attack surface:** Tool implementations can execute arbitrary code
- **Mitigation:** Existing PermissionEngine + ApprovalService + VerificationGate already protect tool execution. New tools follow same pipeline.
- **CRITICAL risk tools** are already hardcoded to absolute deny in PermissionEngine.

### 6.3 Computer Use / Desktop Control

- **New attack surface:** Desktop control is the highest-risk capability
- **Mitigation required:** New safety boundary, screenshot-only mode, input simulation limits, primary worktree protection extended to desktop.
- **Risk:** Without careful design, desktop control could bypass all existing safety boundaries.

### 6.4 Agent Teams

- **New attack surface:** Agent isolation failures could allow privilege escalation
- **Mitigation required:** Process isolation, capability scoping per agent, audit per agent.
- **Risk:** Complex isolation requirements could introduce security regressions.

### 6.5 No-Security-Impact Candidates

- **Monitoring/Observability:** Read-only telemetry. No new execution paths.
- **Evaluation Framework:** Sandboxed evaluation. No production authority.

---

## 7. Complexity / Rework Analysis

### 7.1 Memory/Knowledge Foundation

- **Implementation complexity:** LOW-MEDIUM. New DB tables, repositories, service. Follows existing patterns.
- **Testing complexity:** MEDIUM. Privacy boundaries, lifecycle, integration with events.
- **Maintenance burden:** LOW. Memory is passive data. Read/write patterns are simple.
- **Rework risk:** LOW. Does not modify existing subsystems.
- **Prerequisites:** None (SQLite, Event system exist).

### 7.2 AI Manager Expansion

- **Implementation complexity:** MEDIUM. Model routing requires provider abstraction. Planning requires prompt engineering.
- **Testing complexity:** HIGH. Model behavior is non-deterministic. Requires golden-set testing.
- **Maintenance burden:** MEDIUM. Prompt maintenance, model migration.
- **Rework risk:** MEDIUM. Changes AI Manager contract; all callers must adapt.
- **Prerequisites:** None.

### 7.3 Tool Registry Expansion

- **Implementation complexity:** LOW per tool. Each tool is a new `BaseTool` subclass + registration.
- **Testing complexity:** LOW-MEDIUM per tool. Permission + approval + audit pipeline already tested.
- **Maintenance burden:** LOW-MEDIUM. Tool implementations need maintenance as system changes.
- **Rework risk:** LOW. Additive only.
- **Prerequisites:** None.

### 7.4 Computer Use / Desktop Control

- **Implementation complexity:** HIGH. OS-level integration, screenshot capture, input simulation.
- **Testing complexity:** HIGH. Requires OS-specific tests, safety boundary validation.
- **Maintenance burden:** HIGH. OS updates can break desktop control.
- **Rework risk:** HIGH. Safety boundary design is hard to get right; changes retroactively are expensive.
- **Prerequisites:** Tool registry expansion (for screenshot/input tools).

### 7.5 Agent Teams

- **Implementation complexity:** HIGH. Agent identity, delegation, coordination, isolation.
- **Testing complexity:** HIGH. Multi-agent interaction testing is complex.
- **Maintenance burden:** HIGH. Coordination logic is fragile.
- **Rework risk:** HIGH. Architectural changes to Kilo sessions, execution context, capability scoping.
- **Prerequisites:** Memory (for context sharing), durable execution records.

### 7.6 Autonomous Workflows

- **Implementation complexity:** MEDIUM-HIGH. New workflow definition language or DSL, state machine, composition engine.
- **Testing complexity:** HIGH. Workflow composition has many edge cases.
- **Maintenance burden:** MEDIUM. Workflow definitions need versioning and migration.
- **Rework risk:** MEDIUM. New abstraction layer touches all execution paths.
- **Prerequisites:** Durable execution records, event system.

### 7.7 Monitoring / Observability

- **Implementation complexity:** LOW-MEDIUM. Health indicators, telemetry collection, dashboard.
- **Testing complexity:** LOW. Health checks are simple assertions.
- **Maintenance burden:** LOW. Telemetry is additive.
- **Rework risk:** LOW.
- **Prerequisites:** None.

### 7.8 Evaluation Framework

- **Implementation complexity:** MEDIUM. Evaluation runner, metrics computation, scenario definitions.
- **Testing complexity:** MEDIUM. Evaluation scenarios need maintenance.
- **Maintenance burden:** MEDIUM. Scenarios need updates as system evolves.
- **Rework risk:** LOW-MEDIUM. Additive, but depends on execution records format.
- **Prerequisites:** Durable execution records.

---

## 8. Recommended Stage 14

### Stage 14 Goal

Establish a persistent memory and knowledge foundation that enables the AI Operator to maintain context, learn, and coordinate across sessions and restarts.

### Why Now

The system has completed its foundational safety and execution layers (stages 10-13). The next capability that unblocks the most future work with the least complexity is persistent memory. Without it:
- The system is stateless between desktop app sessions
- Agent Teams cannot share context
- Long-running workflows cannot survive restarts
- The system cannot personalize or learn
- Execution history is lost when in-memory structures are destroyed

Memory is the prerequisite for the "private personal autonomous operating system" vision. It transforms the system from a task runner into an operating system that knows its user and environment.

### Scope

- Design memory entity model (memory entries, contexts, provenance, lifecycle)
- Add DB tables and repositories for memory
- Add application service for memory CRUD with privacy boundaries
- Integrate memory with Event system (events can create memories)
- Add memory retrieval API (internal + REST)
- Define memory lifecycle: active → archived → expired → deleted
- Add privacy boundary: user-controlled memory deletion
- Ensure memory does NOT grant execution authority, bypass PermissionEngine, or modify task/plan state
- 2196+ tests must remain passing
- No modifications to existing event, scheduler, execution, or notification contracts

### Out of Scope

- AI Manager expansion (model routing, multi-step planning)
- Tool registry expansion (new tool implementations)
- Computer use / desktop control
- Agent teams / coordination
- Autonomous workflow composition
- Monitoring / observability dashboards
- Evaluation framework
- Vector databases, embeddings, or semantic search (use simple retrieval first)
- Multi-user architecture
- Cloud sync

### Sub-stages

**14.1 Discovery / Contract**
- Define memory entity model
- Define memory lifecycle and privacy boundaries
- Define memory API contracts (internal service + REST)
- Document security invariants
- Write Stage 14.1 discovery report

**14.2 Foundation**
- Add DB tables and migrations
- Add memory repositories
- Add memory service with lifecycle management
- Add memory retrieval service
- Write unit tests for memory service and repositories

**14.3 Integration**
- Integrate memory with Event system (events can create memory entries)
- Integrate memory with Kilo session manager (session context persistence)
- Add memory to AI Manager context (read-only enrichment)
- Write integration tests

**14.4 API / Control Plane**
- Add REST endpoints for memory management
- Add memory to desktop UI data flow (if applicable)
- Document API contracts
- Write API tests

**14.5 Hostile Audit**
- Audit memory for injection vectors
- Audit memory for privacy leaks
- Audit memory for authority bypass
- Verify no impact on existing subsystems
- Full regression test

**14.6 Final Acceptance**
- Verify all 14.x tests pass
- Verify full project regression (2196+ passed)
- Verify no scope creep
- Write Stage 14 final acceptance report
- Create checkpoint

---

## 9. Future 5–8 Stage Roadmap

### Stage 15 — Tool Registry Expansion + Computer-Use Safety Boundary

**Purpose:** Populate the tool registry with real, useful tools (filesystem, shell, browser) and establish the safety boundary for computer use.

**Major dependency:** Stage 14 (memory for tool context and learning).

**Why after 14:** Tools need persistent context (file history, user preferences) to be useful. Memory provides this.

**Deferred risks:** Desktop control safety boundary design is complex; may require iteration.

---

### Stage 16 — Durable Execution Records

**Purpose:** Persist `AutonomousRunResult`, `OrchestrationResult`, `VerificationReport`, and `PlanExecutionResult` to database. Make execution history queryable and durable across restarts.

**Major dependency:** Stage 14 (memory for execution context), Stage 15 (real tools to execute).

**Why after 14/15:** Execution records need memory context to be meaningful. Real tools make execution records non-trivial.

**Deferred risks:** Schema design for execution records must accommodate future workflow composition.

---

### Stage 17 — AI Manager Expansion: Model Routing + Planning

**Purpose:** Add model/provider routing, multi-step planning, and decision context enrichment to AI Manager.

**Major dependency:** Stage 14 (memory for context), Stage 16 (execution history for learning).

**Why after 14/16:** Better decisions require memory (user context) and execution history (past outcomes).

**Deferred risks:** Model routing adds provider abstraction complexity. Planning quality depends on tool availability.

---

### Stage 18 — Monitoring / Health / Observability

**Purpose:** System health indicators, execution telemetry, worker diagnostics, recovery visibility.

**Major dependency:** Stage 16 (durable execution records), Stage 14 (memory for system context).

**Why after 14/16:** Monitoring needs execution data and system context to be meaningful.

**Deferred risks:** Dashboard/UI work may require desktop app changes.

---

### Stage 19 — Autonomous Workflow Composition

**Purpose:** Declarative workflows that combine Event + Scheduler + Execution + Notification into durable, resumable, composable automation.

**Major dependency:** Stage 14 (memory), Stage 16 (durable execution), Stage 17 (AI Manager planning), Stage 18 (monitoring).

**Why after 14-18:** Workflows need memory (context), durable execution (state recovery), planning (AI-driven composition), and monitoring (health visibility).

**Deferred risks:** Workflow DSL design is open-ended; must avoid over-engineering.

---

### Stage 20 — Agent Teams / Coordination

**Purpose:** Multiple specialized agents with delegation, coordination, isolation, and authority boundaries.

**Major dependency:** Stage 14 (memory for shared context), Stage 19 (workflows for coordination patterns).

**Why after 14/19:** Agent coordination needs shared memory and workflow patterns.

**Deferred risks:** Agent isolation is architecturally significant. May require process-level sandboxing.

---

### Stage 21 — Evaluation / Agent Verification Framework

**Purpose:** Standardized evaluation tasks, reliability metrics, regression scenarios, A/B testing.

**Major dependency:** Stage 16 (durable execution records), Stage 20 (agent teams to evaluate).

**Why after 16/20:** Evaluation needs execution history and multiple agent types to measure.

**Deferred risks:** Evaluation scenarios must be maintained alongside system evolution.

---

### Stage 22+ — Computer Use / Desktop Control (if needed)

**Purpose:** Controlled desktop interaction for email, calendar, messengers, file management outside development workspaces.

**Major dependency:** Stage 15 (tool registry + safety boundary), Stage 14 (memory for desktop context).

**Why deferred:** Highest security complexity. Should only be added when the system has stable memory, tools, and execution foundation. Desktop control is not needed for software development autonomy.

---

## 10. Explicitly Deferred Work

- **Computer Use / Desktop Control** — deferred to Stage 22+ due to high security complexity. Not needed for software development autonomy.
- **Vector databases / embeddings / semantic search** — deferred indefinitely. Use simple key-value retrieval for memory first. Add semantic search only when memory volume justifies it.
- **Multi-user architecture** — not applicable. This is a private personal system.
- **Cloud infrastructure / sync** — not applicable. Private desktop application.
- **Microservices / message brokers** — not applicable. Single-process desktop application.
- **Distributed coordination** — not applicable. Single-user, single-machine.
- **Browser automation** — deferred until tool registry expansion (Stage 15).
- **Email / calendar / messengers** — deferred until computer use or dedicated tool implementations.
- **AI-generated abstractions** — avoid until concrete features require them.

---

## 11. Git / Checkpoint Strategy

**Authoritative checkpoint:** `366ddad` — DO NOT MODIFY.

**Stage 14 checkpoints:**
- `14.1-discovery` — after discovery/contract definition
- `14.2-foundation` — after DB tables, repositories, service
- `14.3-integration` — after event/session integration
- `14.4-api` — after REST API
- `14.5-audit` — after hostile audit
- `14.6-final` — after final acceptance (new authoritative checkpoint)

**Branching:** Each Stage 14 sub-stage should be developed on a feature branch and merged only after acceptance gate. The final Stage 14 checkpoint becomes the new baseline for Stage 15.

**Regression requirement:** All Stage 14 checkpoints must maintain `2196 passed, 4 skipped, 0 failed` or better.

---

`STAGE_14_ROADMAP_DECISION_READY`
