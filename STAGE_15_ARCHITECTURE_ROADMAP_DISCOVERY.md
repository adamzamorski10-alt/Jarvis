# Stage 15 — Architecture Roadmap Discovery

**Date:** 2026-09-24
**Scope:** Post-Stage 14 architecture discovery for AI Operator
**Status:** DISCOVERY COMPLETE

---

## 1. Executive Summary

Stage 15 is a discovery-only stage. No production code was modified. This document maps the actual current architecture, evaluates the capability matrix, identifies architectural gaps, and proposes a dependency-safe roadmap for the next 5–8 stages.

**Key finding:** The system has a solid foundation for autonomous development tasks, event sourcing, permissions, approvals, and memory storage. The most critical missing foundation is **user identity/session management** — without it, advanced memory retrieval, personalization, and multi-user scenarios cannot be built safely. The second critical gap is **actual tool implementations** — the tool registry exists but only `system.get_time` is registered. The third is **memory retrieval** — memory is currently write-only.

**Recommended next stage:** Stage 16 — Identity & Session Foundation. This unblocks user-scoped memory, personalized context, and safe multi-instance operation.

---

## 2. Current Architecture

### 2.1 Core Services/Modules

| Subsystem | Location | What It Does Today | Authority | Limitations |
|-----------|----------|-------------------|-----------|-------------|
| **FastAPI App** | `app/main.py` | HTTP control plane, CORS, global exception handler | Thin router | No auth middleware |
| **Task Execution** | `app/orchestration/` | Autonomous task lifecycle (PENDING→COMMITTED/FAILED) | TaskStateMachine, RunController | Single-task focus, no parallel DAG execution |
| **AI Manager** | `app/ai_manager/` | Decision engine: user intent → ManagerDecision → Plan | AIManager + normalizer + policy gate | Does not execute, only proposes |
| **Planning** | `app/manager/` | ManagerPlanResult → DevelopmentTask DAG | PlanManager | Simple DAG, no hierarchical plans |
| **Verification** | `app/verification/` | Fail-closed gate running checks in order | VerificationGate | Sequential only, no parallel checks |
| **Workspace/Git** | `app/workspace/` | Isolated Git worktrees per task, checkpoints | WorkspaceManager | No remote push, no merge/PR |
| **Scheduler** | `app/scheduler/` | Background polling worker, lease-based claims | SchedulerWorker + SchedulerRepository | Poll-based, not event-driven trigger |
| **Events** | `app/events/` | Immutable append-only event store | EventRepository | Idempotent by event_id |
| **History** | `app/events/history.py` | Read-side query over events | HistoryService | No materialized views |
| **Notifications** | `app/notifications/` | Event→NotificationDraft→Notification | NotificationPolicy + Repository | V1 types only |
| **Memory** | `app/memory/` | MemoryEntry CRUD with authority enforcement | MemoryService + MemoryRepository | No retrieval/search, no embeddings |
| **Permissions** | `app/tools/` | PermissionEngine evaluates grants + autonomy mode | PermissionEngine | Fail-closed, grant-based |
| **Approvals** | `app/tools/` | ASK_USER approval flow with tokens | ApprovalService | 1-hour TTL, no escalation |
| **Tool Execution** | `app/tools/` | ToolCall → validate → PermissionEngine → ToolExecutor → audit | ToolExecutionService | Only `system.get_time` registered |
| **Recovery** | `app/orchestration/` | Crash/timeout recovery with heartbeats | RecoveryManager | 3-attempt limit, no cross-process |
| **Settings** | `app/services/` | Key-value settings with defaults | SettingsService | Plain text values, no schema |
| **Credentials** | `app/ai/services/` | Windows DPAPI encrypt/decrypt | CredentialService | Windows-only |
| **Conversations** | `app/ai/services/` | Conversation + message persistence | ConversationService | Text-only, no attachments |
| **Activity** | `app/services/` | Activity event log | ActivityService | Separate from Event system |
| **AI Providers** | `app/ai/providers/` | Ollama, OpenRouter, Kimi, Kilo, Future | ProviderManager | Coding adapters only for Ollama/OpenRouter |
| **Tool Registry** | `app/tools/` | In-memory registry of ToolDefinitions | ToolRegistry (singleton) | Thread-safe, but no persistence |
| **Audit** | `app/tools/` | Audit log for permission/tool events | AuditLogService | Separate from Event system |
| **Background Execution** | `app/services/` | Background plan execution manager | BackgroundExecutionManager | In-memory handles, not durable |
| **Desktop Client** | `apps/desktop/` | Tauri + TypeScript frontend | Thin HTTP client | No offline mode |

### 2.2 Database Models

| Model | Table | Purpose |
|-------|-------|---------|
| DevelopmentTask | `development_tasks` | Task state, heartbeats, recovery |
| TaskDependency | `task_dependencies` | DAG edges |
| PlanFingerprint | `plan_fingerprints` | Idempotent plan creation |
| ScheduledJob | `scheduled_jobs` | Scheduler definitions |
| Event | `events` | Immutable event store |
| Notification | `notifications` | User-facing notifications |
| MemoryEntry | `memories` | Memory storage with authority |
| Conversation/Message | `conversations`, `messages` | AI chat persistence |
| AppSetting | `app_settings` | Key-value configuration |
| PermissionGrant | `permission_grants` | Persistent permission grants |
| PendingApproval | `pending_approvals` | Approval workflow state |
| CapabilityApproval | `capability_approvals` | AI Manager capability approvals |
| AuditEvent | `audit_events` | Permission/tool audit log |
| ActivityEvent | `activity_events` | General activity log |
| KiloSession | `kilo_sessions` | Kilo agent session tracking |
| Settings | `settings` | Legacy settings table |

### 2.3 REST/API Surface

| Route Prefix | Purpose | Auth |
|-------------|---------|------|
| `/api/health` | Health check | None |
| `/api/system` | System status, diagnostics | None |
| `/api/settings` | Read/write settings | None |
| `/api/activity` | Activity events | None |
| `/api/ai` | Providers, config, chat, models, conversations | None |
| `/api/ai/approvals` | Approve/reject pending approvals | None |
| `/api/plans` | Execute/cancel plans, task/run status | None |
| `/api/history` | Event history queries | None |
| `/api/scheduler` | Scheduler job management | None |

**Critical observation:** The entire API surface has **no authentication or authorization**. All endpoints are accessible without identity verification.

### 2.4 Background Workers

| Worker | Location | Purpose |
|--------|----------|---------|
| SchedulerWorker | `app/scheduler/worker.py` | Polls due jobs, claims leases, hands off to execution |
| BackgroundExecutionManager | `app/services/background_execution_manager.py` | Manages in-memory background plan execution handles |

### 2.5 Scheduler

- Poll-based worker with configurable interval (default 60s)
- Lease-based claim with recovery
- Handoff to Task execution (SchedulerHandoffService → RunController) or Plan execution (BackgroundExecutionManager)
- Deterministic event IDs via `make_scheduler_event_id()`

### 2.6 Event System

- Immutable append-only event store
- Idempotent by `event_id` primary key
- Sanitized payloads/metadata with size caps
- Correlation/causation/task/plan/run/execution/actor fields
- Notification policy maps events to notification drafts

### 2.7 History System

- Read-side query layer over EventRepository
- Task-scoped, plan-scoped, execution-scoped, correlation-scoped, time-range queries
- Deterministic ordering: `occurred_at ASC, event_id ASC`
- Pagination with hard caps

### 2.8 Notification System

- Policy-driven event → NotificationDraft mapping
- Idempotent by `(source_event_id, notification_type)`
- V1 types: task.completed, task.failed, plan.completed, plan.failed, verification.failed, scheduler.occurrence.failed/recovered, approval.requested, recovery.completed, system.error

### 2.9 Memory System

- MemoryEntry with category, scope, trust, provenance
- Authority enforcement per caller type (USER/SYSTEM/AGENT)
- Soft delete, supersession, scope isolation
- Secret redaction before persistence
- Event emission for lifecycle (memory.created/updated/superseded/deleted)
- No retrieval/search/embeddings

### 2.10 AI Manager

- Provider-neutral decision engine
- Model proposes; application decides
- CapabilityRegistry for known capabilities
- PolicyCapabilityGate for authorization
- ManagerPlanningService bridges decisions to PlanManager
- Does NOT execute tasks or tools

### 2.11 Autonomous Development

- Task state machine with atomic CAS claims
- Workspace isolation per task (Git worktrees)
- VerificationGate (fail-closed)
- Repair loop (max attempts)
- Recovery manager (heartbeats, crash recovery)
- AutonomousPlanExecutor (DAG execution loop)

### 2.12 Permission/Authorization System

- PermissionEngine: evaluates tool requests against rules + grants + autonomy mode
- Fail-closed: CRITICAL risk always denied
- Grant types: one_time, time_bound, persistent
- Audit logging is fail-soft

### 2.13 Approval System

- PendingApproval with TTL (1 hour)
- Server-side reconstruction of ToolCall (never trusts frontend)
- ApprovalVerificationToken for execution context
- Atomic state transitions: PENDING → APPROVED → EXECUTING

### 2.14 Tool Execution System

- ToolExecutionService is the ONLY component that calls both PermissionEngine and ToolExecutor
- Argument validation, result validation
- Size caps, timeouts
- Currently only `system.get_time` is registered

### 2.15 Recovery/Error Handling

- RecoveryManager detects orphaned RUNNING tasks
- Heartbeat-based staleness detection
- Atomic recovery claims
- Limit on recovery attempts
- Workspace cleanup integration

### 2.16 Settings/Configuration

- Key-value settings in SQLite
- Pydantic Settings with env prefix `AI_OPERATOR_`
- Default settings seeded on startup
- Windows DPAPI for credential encryption

### 2.17 Observability/Logging

- structlog with JSON or pretty format
- Activity events in separate table
- Event store for lifecycle events
- No distributed tracing
- No metrics/monitoring endpoints

### 2.18 Testing Infrastructure

- pytest + pytest-asyncio
- In-memory SQLite for tests
- ~222 tests for memory/event/history subsystems
- Hostile tests for permission, approval, memory authority
- No integration test harness for desktop ↔ core

---

## 3. Capability Matrix

| Capability | Status | Notes |
|------------|--------|-------|
| Durable task execution | ✅ Exists | Task state machine, heartbeats, recovery |
| Autonomous coding | ✅ Partial | Basic task execution, verification, repair |
| Planning | ✅ Exists | AI Manager → PlanManager → DAG |
| Verification | ✅ Exists | VerificationGate with check registry |
| Repair | ✅ Exists | Repair loop with max attempts |
| Permissions | ✅ Exists | PermissionEngine with grants |
| Approvals | ✅ Exists | ApprovalService with TTL and tokens |
| Tool execution | ⚠️ Partial | Pipeline exists, but only `system.get_time` implemented |
| Audit | ✅ Partial | AuditLogService for permissions/tools; ActivityService for general |
| Event history | ✅ Exists | Immutable event store with correlation |
| Scheduling | ✅ Exists | Background worker with lease claims |
| Notifications | ✅ Exists | Policy-driven from events |
| Memory storage | ✅ Exists | MemoryEntry with authority enforcement |
| Memory retrieval | ❌ Missing | No search, no context assembly |
| Memory search | ❌ Missing | No embeddings, no RAG |
| Knowledge management | ❌ Missing | No knowledge graph, no relationships |
| Identity | ❌ Missing | No user identity, no sessions |
| Session/context management | ❌ Missing | No application sessions |
| User interaction | ❌ Missing | Only approval UI via desktop |
| Conversation/session persistence | ✅ Partial | Conversation + messages, no attachments |
| Model/provider abstraction | ✅ Exists | ProviderManager with adapters |
| Model routing | ⚠️ Partial | Provider selection, no intelligent routing |
| Secrets management | ⚠️ Partial | Windows DPAPI only |
| External tool integrations | ❌ Missing | No filesystem, browser, email, etc. |
| Filesystem control | ❌ Missing | No file tools registered |
| Git control | ⚠️ Partial | Internal WorkspaceManager only |
| Process control | ❌ Missing | No subprocess execution |
| Browser/computer use | ❌ Missing | No browser automation |
| Email | ❌ Missing | No email integration |
| Calendar | ❌ Missing | No calendar integration |
| Cloud storage | ❌ Missing | No cloud storage |
| Messaging | ❌ Missing | No messaging integration |
| Device control | ❌ Missing | No device control |
| Financial data access | ❌ Missing | No financial integration |
| Monitoring/health | ⚠️ Partial | Health + system status endpoints |
| Recovery | ✅ Exists | Crash/timeout recovery with heartbeats |
| Observability | ⚠️ Partial | Structured logs + activity events |
| Policy management | ⚠️ Partial | Settings + autonomy mode |
| Autonomous decision loops | ⚠️ Partial | Agent loop in AIService |

---

## 4. Architectural Gaps

### 4.1 BLOCKER Gaps

| Gap | Why It Matters | Depends On | Unlocks |
|------|---------------|------------|----------|
| **No user identity/session** | Cannot scope memory, permissions, or context to a user. All operations are anonymous. | None | Personalized memory, multi-user, secure API |
| **No actual tool implementations** | The entire tool execution pipeline exists but cannot do anything useful. | None | Autonomous coding, filesystem access, Git operations |
| **No memory retrieval** | Memory is write-only. AI cannot query past knowledge. | Identity | Context-aware AI, personalization |

### 4.2 IMPORTANT Gaps

| Gap | Why It Matters | Depends On | Unlocks |
|------|---------------|------------|----------|
| **No filesystem tools** | Agent cannot read/write files except through workspace Git checkpoints. | Tool implementations | Real autonomous coding |
| **No process/subprocess control** | Cannot run builds, tests, linters as tools. | Tool implementations, permissions | Autonomous verification |
| **No browser/computer use** | Cannot interact with web apps or desktop. | Tool implementations, permissions | Personal assistant |
| **No external integrations** | Cannot access email, calendar, messaging, cloud storage. | Identity, auth, tool framework | Personal OS capabilities |
| **No workflow engine** | Cannot chain events into autonomous multi-step workflows. | Events, history, recovery | Event-driven autonomy |
| **No vector/RAG infrastructure** | Cannot do semantic memory search. | Memory retrieval, embeddings | Advanced memory |

### 4.3 USEFUL LATER Gaps

| Gap | Why It Matters | Depends On |
|------|---------------|------------|
| Multi-agent/agent teams | Complex collaboration scenarios | Identity, workflow, memory |
| Semantic code understanding | Better planning and verification | Embeddings, code model |
| Remote Git operations | Push/PR creation | Git tools, network tools |
| Crash recovery across restarts | Full process-crash resilience | Durable execution handles |
| Distributed tracing | Debugging complex flows | Observability infrastructure |

### 4.4 NOT CURRENTLY WORTH BUILDING

| Capability | Reason |
|------------|--------|
| Agent Teams | No identity/session foundation |
| Workflow Engine | No memory retrieval or external tools |
| Computer Use | No browser/process tools |
| Vector DB | No memory retrieval need yet |
| External Integrations | No identity/auth foundation |
| REST API for Memory | Service is internal-only per Stage 14.5 |

---

## 5. Dependency Graph

### 5.1 Current Hard Dependencies

```
Identity (none today)
  ↓
User-scoped Memory, Permissions, Context Assembly

Tool Execution Pipeline
  ToolRegistry → PermissionEngine → ApprovalService → ToolExecutionService → ToolExecutor
  ↓
External Capabilities (filesystem, Git, process, browser)

Memory
  MemoryService → EventEmitter → EventRepository
  ↓
HistoryService → History retrieval

Events
  EventEmitter → EventRepository → NotificationPolicy → NotificationRepository
  ↓
History, Notifications, Recovery, Monitoring

Scheduler
  SchedulerWorker → SchedulerRepository → SchedulerHandoffService
  ↓
Task Execution, Plan Execution, Notifications

AI Manager
  AIManager → ManagerPlanningService → PlanManager → PlanService → TaskRepository
  ↓
Task Execution, Verification

Autonomous Development
  AutonomousPlanExecutor → PlanExecutionService → RunController → Orchestrator
  ↓
TaskStateMachine, WorkspaceManager, VerificationGate, DevelopmentAgent
```

### 5.2 Soft Dependencies

| Component | Soft Dependency | Current State |
|-----------|---------------|---------------|
| MemoryService | EventEmitter | Optional — works without events |
| SchedulerWorker | NotificationPolicy | Optional — works without notifications |
| BackgroundExecutionManager | None | Independent |
| RecoveryManager | WorkspaceManager | Optional — works without workspace |
| AI Service | ToolExecutionService | Required for agent loop |
| Desktop Client | Core API | Thin client, no offline logic |

### 5.3 Optional Dependencies

| Component | Optional Dependency | Effect if Missing |
|-----------|-------------------|-------------------|
| MemoryService | EventEmitter | No lifecycle events |
| SchedulerWorker | NotificationPolicy | No scheduler notifications |
| Orchestrator | DevelopmentAgent | Falls back to stub agent |
| RecoveryManager | WorkspaceManager | No workspace cleanup |
| AIService | ApprovalService | No approval flow |

### 5.4 Circular Dependencies

**None detected.** The architecture is layered with clear dependency direction:
- `app.api` → `app.services` → `app.repositories` → `app.db.models`
- `app.tools` is a standalone security layer
- `app.events` is a standalone infrastructure layer
- `app.memory` depends on `app.events` but not vice versa

### 5.5 Components That Should Remain Independent

| Component | Reason |
|-----------|--------|
| EventRepository | Foundation for history, notifications, recovery |
| PermissionEngine | Central security boundary |
| TaskStateMachine | Sole authority for task transitions |
| WorkspaceManager | Sole authority for Git/worktree operations |
| VerificationGate | Sole authority for code verification |
| ToolExecutionService | ONLY component that calls both PermissionEngine and ToolExecutor |
| MemoryService | Data authority, not execution authority |

---

## 6. Security Architecture Review

### 6.1 Current Strengths

1. **Single execution authority:** ToolExecutionService is the ONLY component that calls both PermissionEngine.evaluate() and ToolExecutor.execute()
2. **Fail-closed permissions:** CRITICAL risk always denied; unknown tools denied; disabled tools denied
3. **Approval mediation:** All ASK_USER decisions go through ApprovalService with server-side reconstruction
4. **Immutable events:** Event store is append-only; no UPDATE/DELETE except retention
5. **Memory as data:** MemoryService explicitly does not grant execution authority
6. **Workspace isolation:** Primary worktree is protected; autonomous worktrees are isolated
7. **Audit logging:** Fail-soft audit never alters permission decisions

### 6.2 Potential "Second Authority" Risks

| Risk | Current State | Severity |
|------|---------------|----------|
| AI Manager bypassing execution controls | Bridge service explicitly does NOT call execution layers | ✅ Safe |
| Memory content granting authority | Payload excludes content/metadata; stored as data only | ✅ Safe |
| Events triggering execution | No event-driven execution in current architecture | ✅ Safe |
| Approval bypass via frontend | Server-side reconstruction of ToolCall from PendingApproval | ✅ Safe |
| Direct tool execution without PermissionEngine | Code structure enforces order; no other component imports both | ✅ Safe |
| ContextVar correlation propagation | No ContextVar exists; correlation is explicit per emit | ⚠️ Low risk |
| Background execution handles in memory | BackgroundExecutionManager uses in-memory dict; crash loses handles | ⚠️ Medium risk |

### 6.3 Key Security Gaps

1. **No authentication:** Entire API is unauthenticated. Any local process can call any endpoint.
2. **No authorization:** No concept of user identity or role-based access.
3. **In-memory execution handles:** BackgroundExecutionManager state is lost on crash.
4. **No input sanitization on API boundary:** API routes trust caller input beyond basic validation.

### 6.4 Recommendations (Discovery Only)

- Do NOT add auth middleware yet — no identity foundation exists
- Do NOT expose MemoryService via REST yet — no auth to protect it
- DO ensure any future identity system integrates with existing PermissionEngine grants
- DO ensure future workflow engine cannot bypass ToolExecutionService

---

## 7. Autonomy Readiness

### 7.1 Autonomous Developer

**Status:** ⚠️ Partial

**Supported foundations:**
- Task state machine with atomic claims
- Workspace isolation per task
- Verification gate
- Repair loop
- Crash recovery
- Event sourcing
- Permission/approval framework

**Missing foundations:**
- Actual filesystem tools (read/write/edit files)
- Process tools (run builds, tests, linters)
- Git tools (commit, push, PR)
- Memory retrieval for context
- Intelligent planning beyond basic DAG

**Dangerous shortcuts:**
- Do NOT bypass VerificationGate
- Do NOT allow direct shell execution
- Do NOT skip approval for HIGH/CRITICAL tools

### 7.2 Autonomous Research

**Status:** ❌ Not supported

**Missing foundations:**
- Web search tools
- File reading tools
- Content extraction
- Citation/source tracking
- Memory for research findings

### 7.3 Personal Assistant

**Status:** ❌ Not supported

**Missing foundations:**
- User identity and sessions
- Memory retrieval and personalization
- Context assembly across conversations
- External integrations (email, calendar, messaging)
- Long-term memory with decay/archival

### 7.4 Computer-Use Agent

**Status:** ❌ Not supported

**Missing foundations:**
- Browser automation tools
- Screen reading/control
- Process management
- Desktop interaction
- Safety boundaries for destructive actions

### 7.5 Multi-Agent/Agent Teams

**Status:** ❌ Not supported

**Missing foundations:**
- Agent identity and registry
- Inter-agent communication
- Shared memory/knowledge
- Coordination protocols
- Conflict resolution

### 7.6 Event-Driven Autonomous Workflows

**Status:** ⚠️ Partial

**Supported foundations:**
- Immutable event store
- Notification policy
- Event correlation

**Missing foundations:**
- Workflow definition language
- Event-driven triggers (currently poll-based)
- State machine for workflow instances
- Compensation/rollback for workflows

### 7.7 Long-Term Personalized AI

**Status:** ❌ Not supported

**Missing foundations:**
- User identity
- Memory retrieval/search
- Conversation continuity
- Preference learning
- Knowledge management

---

## 8. Candidate Next Stages

### 8.1 NOW-CANDIDATES

#### Stage 16: Identity & Session Foundation

**Purpose:** Establish user identity, application sessions, and secure API authentication.

**Prerequisites:** None — this is a foundational stage.

**Capabilities unlocked:**
- User-scoped memory queries
- Secure API access
- Multi-instance isolation
- Foundation for personalization

**Architectural risk:** Low — adds new layers without modifying existing ones.

**Implementation complexity:** Medium — requires new models, API auth, session management.

**Explicit non-scope:**
- No OAuth/external identity providers
- No RBAC beyond basic user identity
- No password authentication (use OS-level auth)

#### Stage 17: Tool Implementation Foundation

**Purpose:** Add actual filesystem, Git, and process tools to make the tool execution pipeline useful.

**Prerequisites:** Stage 16 (identity) for secure tool authorization.

**Capabilities unlocked:**
- Read/write files in workspaces
- Run builds/tests/linters
- Git commit operations
- Real autonomous coding

**Architectural risk:** Medium — increases attack surface; must maintain PermissionEngine boundaries.

**Implementation complexity:** High — requires careful security review for each tool.

**Explicit non-scope:**
- No shell command execution
- No arbitrary subprocess execution
- No browser/computer use

#### Stage 18: Memory Retrieval & Context Assembly

**Purpose:** Enable memory search and context-aware AI responses.

**Prerequisites:** Stage 16 (identity) for user-scoped queries; Stage 17 (tools) for file-based context.

**Capabilities unlocked:**
- Semantic memory search
- Context assembly for AI prompts
- Personalized AI responses
- Knowledge management foundation

**Architectural risk:** Medium — introduces embeddings/vector store; must preserve Memory as data.

**Implementation complexity:** High — requires embedding model, vector store, retrieval pipeline.

**Explicit non-scope:**
- No RAG over external documents
- No automatic memory generation from events
- No knowledge graph

### 8.2 LATER-CANDIDATES

#### Stage 19: External Integrations Foundation

**Purpose:** Add email, calendar, and messaging integrations.

**Prerequisites:** Stage 16 (identity), Stage 17 (tools), Stage 18 (memory).

**Architectural risk:** High — external dependencies, auth complexity.

#### Stage 20: Browser/Computer Use Foundation

**Purpose:** Add browser automation and desktop interaction.

**Prerequisites:** Stage 16, 17, 18, 19.

**Architectural risk:** Very High — safety-critical, complex security boundaries.

#### Stage 21: Policy Management & Governance

**Purpose:** Add fine-grained policy engine for autonomy modes, tool restrictions, and compliance.

**Prerequisites:** Stage 16 (identity).

**Architectural risk:** Medium — policy complexity.

#### Stage 22: Observability & Monitoring Enhancement

**Purpose:** Add metrics, distributed tracing, health dashboards.

**Prerequisites:** None — can be done in parallel.

**Architectural risk:** Low.

### 8.3 DEFER

| Candidate | Reason to Defer |
|-----------|----------------|
| Agent Teams | Requires identity, memory, and workflow foundations |
| Workflow Engine | Requires external tools and memory retrieval |
| Vector DB | Premature without memory retrieval need |
| Remote Git Operations | Requires network tools and auth |
| Crash Recovery Across Restarts | BackgroundExecutionManager handles most cases |
| Distributed Tracing | Premature without multi-service deployment |
| Multi-Provider Model Routing | Current provider selection is sufficient |
| Semantic Code Understanding | Requires embeddings and code model |

---

## 9. Proposed 5–8 Stage Roadmap

### Stage 16: Identity & Session Foundation

**Objective:** Establish user identity and application session management.

**Prerequisite:** None.

**Scope:**
- User identity model (local, OS-integrated)
- Application session management
- API authentication (token-based, local only)
- User-scoped settings/preferences
- Identity-aware memory queries (foundation only)

**Non-scope:**
- No external identity providers
- No RBAC
- No password auth
- No multi-user server deployment

**Acceptance criteria:**
- `POST /api/auth/login` creates a session
- `GET /api/auth/me` returns current identity
- Memory queries can be filtered by user_id
- All existing tests pass without modification

### Stage 17: Tool Implementation Foundation

**Objective:** Implement the first useful tools: filesystem read/write, Git operations, and process execution.

**Prerequisite:** Stage 16.

**Scope:**
- `files.read_file`, `files.write_file`, `files.list_directory`
- `git.status`, `git.diff`, `git.log`
- `process.run` (restricted, no shell)
- Permission rules for new tools
- Approval flows for HIGH risk tools

**Non-scope:**
- No shell execution
- No arbitrary subprocess
- No browser automation
- No network tools

**Acceptance criteria:**
- Agent can read/write files in workspace
- Agent can run `pytest`, `ruff`, `mypy` in workspace
- All tools go through PermissionEngine + ApprovalService
- Existing security tests pass

### Stage 18: Memory Retrieval & Context Assembly

**Objective:** Enable memory search and context-aware AI responses.

**Prerequisite:** Stage 16.

**Scope:**
- Memory query API (by scope, category, time)
- Simple keyword search (no embeddings yet)
- Context assembly for AI prompts
- Memory relevance scoring
- Privacy-preserving retrieval

**Non-scope:**
- No vector embeddings
- No RAG over external docs
- No automatic memory generation
- No knowledge graph

**Acceptance criteria:**
- AI can query memories by keyword
- Context assembly respects trust levels
- Memory retrieval is logged to events
- Existing memory tests pass

### Stage 19: External Integrations Foundation

**Objective:** Add email and calendar integrations for personal assistant capabilities.

**Prerequisite:** Stage 16, 17.

**Scope:**
- Email read/send (IMAP/SMTP or local client)
- Calendar read/create (local or CalDAV)
- Integration tools with PermissionEngine rules
- Notification mapping for external events

**Non-scope:**
- No messaging (Slack, Teams, etc.)
- No cloud storage
- No browser automation
- No real-time sync

**Acceptance criteria:**
- Agent can read email and create calendar events
- External integrations go through PermissionEngine
- External data can be stored as memory
- Security tests pass

### Stage 20: Process & System Tools

**Objective:** Add safe process management and system interaction tools.

**Prerequisite:** Stage 16, 17.

**Scope:**
- `process.run` (allowlisted commands, no shell)
- `system.get_info` (OS, disk, memory)
- Process timeout and resource limits
- Audit logging for all process execution

**Non-scope:**
- No shell execution
- No privileged operations
- No daemon management
- No Windows-specific APIs beyond existing DPAPI

**Acceptance criteria:**
- Agent can run builds, tests, linters
- Process execution is audited
- CRITICAL risk processes are always denied
- Existing tests pass

### Stage 21: Autonomous Workflow Engine

**Objective:** Enable event-driven multi-step autonomous workflows.

**Prerequisite:** Stage 16, 17, 18.

**Scope:**
- Workflow definition format (YAML/JSON)
- Workflow instance state machine
- Event triggers for workflow steps
- Compensation/rollback for failed steps
- Workflow history and monitoring

**Non-scope:**
- No visual workflow editor
- No distributed workflow execution
- No workflow marketplace

**Acceptance criteria:**
- Workflows can be defined, executed, and monitored
- Failed workflows compensate correctly
- Workflow events appear in history
- Security boundaries are preserved

### Stage 22: Observability & Monitoring

**Objective:** Enhance observability for production use.

**Prerequisite:** None — can run in parallel.

**Scope:**
- Prometheus metrics endpoint
- Distributed tracing (OpenTelemetry)
- Health check enhancements
- Alerting rules
- Dashboard configuration

**Non-scope:**
- No cloud monitoring integration
- No log aggregation service
- No APM

**Acceptance criteria:**
- `/metrics` endpoint exposes task, event, and tool metrics
- Traces can be exported to Jaeger/Zipkin
- Health checks cover all subsystems

---

## 10. Explicit Non-Goals

The following are **explicitly out of scope** for the next 5–8 stages and should NOT be built:

1. **Agent Teams / Multi-Agent** — No identity, memory, or workflow foundation exists
2. **Computer Use / Browser Automation** — No process or browser tools exist
3. **Vector DB / Embeddings** — Memory retrieval can use keyword search first
4. **RAG over External Documents** — No file reading tools exist yet
5. **External Identity Providers** — Local identity is sufficient for V1
6. **Cloud Sync / Remote Storage** — Local-only is the current model
7. **Semantic Code Understanding** — Requires embeddings and code model
8. **Workflow Marketplace** — Internal workflows only
9. **Mobile/Web Deployment** — Desktop Tauri app is the only client
10. **Microservices Architecture** — Monolith with clear layers is correct for V1

---

## 11. Stage 15 Exit Criteria

1. **What architectural capabilities exist today?**
   - Autonomous task execution with verification/repair/recovery
   - Event sourcing with history and notifications
   - Memory storage with authority enforcement
   - Permission/approval-based tool execution framework
   - AI Manager decision-to-plan bridge
   - Scheduler with lease-based claims
   - Conversation persistence
   - Settings with encrypted credentials (Windows)

2. **What are the most important missing foundations?**
   - User identity and session management (BLOCKER)
   - Actual tool implementations (BLOCKER)
   - Memory retrieval/search (BLOCKER)
   - Filesystem and process tools (IMPORTANT)
   - External integrations (IMPORTANT)

3. **Which missing capability should be addressed before external integrations?**
   - **Identity & Session Foundation** — external integrations require user context and secure auth

4. **Which missing capability should be addressed before advanced Memory/RAG?**
   - **Identity & Session Foundation** — memory must be user-scoped before retrieval
   - **Tool Implementation Foundation** — agent needs tools to create useful context

5. **Which missing capability should be addressed before Computer Use?**
   - **Identity & Session Foundation** → **Tool Implementation Foundation** → **Process & System Tools** → Computer Use

6. **Which missing capability should be addressed before autonomous Personal Assistant behavior?**
   - **Identity** → **Memory Retrieval** → **External Integrations** → Personal Assistant

7. **Which capabilities can safely remain deferred?**
   - Vector DB/embeddings (use keyword search first)
   - Agent teams (requires full identity + memory + workflow)
   - Computer use (requires process + browser tools)
   - Cloud sync (local-only V1)
   - Distributed tracing (premature)

8. **What should the next implementation stage investigate/build?**
   - **Stage 16: Identity & Session Foundation** — local user identity, session management, API authentication

9. **What must explicitly NOT be built yet?**
   - REST API for Memory (no auth to protect it)
   - Memory embeddings/vector store (no retrieval need yet)
   - External integrations (no identity/auth)
   - Computer use tools (no process/browser foundation)
   - Agent teams (no identity/workflow)
   - Workflow engine (no external tools)

---

## 12. Final Discovery Conclusions

**STAGE_15_DISCOVERY_COMPLETE**

### Current Architectural State

The AI Operator has a **solid, security-conscious foundation** for autonomous development tasks. The architecture correctly separates concerns: TaskStateMachine owns state, WorkspaceManager owns Git, VerificationGate owns verification, ToolExecutionService owns execution, and MemoryService owns memory data. The event/history system is well-designed and immutable.

### Critical Blockers

1. **No user identity** — everything is anonymous; no user-scoped operations
2. **No tool implementations** — the execution pipeline exists but has nothing to execute
3. **No memory retrieval** — memory is write-only; AI cannot query past knowledge

### Important Gaps

1. No filesystem tools
2. No process/subprocess tools
3. No external integrations
4. No workflow engine
5. No browser/computer use

### Proposed Next-Stage Candidates

**NOW:**
1. **Stage 16: Identity & Session Foundation** — unblocks everything else
2. **Stage 17: Tool Implementation Foundation** — makes the system actually useful
3. **Stage 18: Memory Retrieval & Context Assembly** — enables personalized AI

**LATER:**
4. Stage 19: External Integrations Foundation
5. Stage 20: Process & System Tools
6. Stage 21: Autonomous Workflow Engine
7. Stage 22: Observability & Monitoring

### Proposed Roadmap

```
Stage 16: Identity & Session Foundation (BLOCKER)
    ↓
Stage 17: Tool Implementation Foundation (BLOCKER)
    ↓
Stage 18: Memory Retrieval & Context Assembly (BLOCKER)
    ↓
Stage 19: External Integrations Foundation (IMPORTANT)
    ↓
Stage 20: Process & System Tools (IMPORTANT)
    ↓
Stage 21: Autonomous Workflow Engine (USEFUL)
    ↓
Stage 22: Observability & Monitoring (USEFUL)
```

### Unresolved Questions

1. Should identity be local-only (Windows Hello) or support external providers?
2. Should memory retrieval use keyword search first, or jump to embeddings?
3. Should the desktop app manage identity, or should the core service?
4. What is the minimum viable tool set for Stage 17?
5. Should external integrations be push or pull based?

---

*This document is the sole output of Stage 15. No production code was modified. No implementation was started. This is a discovery document only.*
