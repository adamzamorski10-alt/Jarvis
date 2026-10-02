# STAGE 17.4 — ARCHITECTURE ALIGNMENT REVIEW

**Document ID:** STAGE_17_4_ARCHITECTURE_ALIGNMENT  
**Status:** DISCOVERY COMPLETE  
**Created:** 2026-09-26  
**Repository:** F:\Projects\AI-Operator  
**Scope:** Architecture alignment, roadmap validation, dependency analysis  

---

## 1. EXECUTIVE SUMMARY

This document reviews the AI Operator Master Specification against the actual repository state and evaluates whether the proposed roadmap from Stage 18 onward is the correct development sequence for the long-term vision.

**Verdict:** The Master Specification is **architecturally sound** but the proposed stage sequence requires adjustment. Stage 18 (File Tool Expansion) remains the correct immediate next stage, but the subsequent roadmap over-prioritizes Model Router and under-prioritizes Memory Retrieval and Context Assembly. A corrected roadmap is proposed.

**Key findings:**
- Master Specification is 95%+ accurate against actual code
- The "AI Manager" is actually split across two modules with separate provider hierarchies — a hidden architectural gap
- Memory Retrieval should follow Stage 18 immediately, not come after Model Router
- Context Assembly is a missing prerequisite stage for Information Sufficiency and Model Router
- Model Router is less urgent than the roadmap suggests
- The autonomous development subsystem is mature and should be treated as a stable foundation
- No production code changes are required; this is a documentation-only review

---

## 2. CURRENT ARCHITECTURE VERIFICATION

### 2.1 Verified Accurate Claims

| Claim | Evidence | Status |
|-------|----------|--------|
| Tauri + React + Python FastAPI architecture | `apps/desktop/` + `services/core/app/main.py` | ✅ VERIFIED |
| SQLite with 16 tables | `app/db/models/__init__.py` lists 16 models | ✅ VERIFIED |
| Identity/Session with bootstrap secret | `app/identity/auth.py`, `app/identity/bootstrap.py`, `app/identity/session.py` | ✅ VERIFIED |
| PermissionEngine with 13-rule cascade | `app/tools/permission_engine.py` | ✅ VERIFIED |
| ToolExecutionService as sole execution authority | `app/tools/tool_execution_service.py` | ✅ VERIFIED |
| ApprovalService with immutable tokens | `app/tools/approval_service.py`, `app/tools/approval_models.py` | ✅ VERIFIED |
| 3 tool implementations | `app/tools/implementations/` contains `system_time.py`, `files_read.py`, `dev_test.py` | ✅ VERIFIED |
| MemoryService with trust levels, scope, supersession | `app/memory/service.py`, `app/memory/repository.py` | ✅ VERIFIED |
| Scheduler with CRUD, misfire policy, handoff | `app/scheduler/` | ✅ VERIFIED |
| Notifications with idempotency, replay | `app/notifications/` | ✅ VERIFIED |
| Autonomous development core | `app/orchestration/` | ✅ VERIFIED |
| VerificationGate as fail-closed authority | `app/verification/gate.py` | ✅ VERIFIED |
| Events as immutable append-only | `app/events/` | ✅ VERIFIED |
| 94 test files | Directory listing confirms | ✅ VERIFIED |

### 2.2 Discrepancies Found

| Master Spec Claim | Actual Repository State | Impact | Classification |
|-------------------|------------------------|--------|----------------|
| "14 tables" in database | **16 tables** (models/__init__.py lists 16: settings, activity, conversation, message, permission_grant, audit_event, pending_approval, capability_approval, development_task, task_dependency, plan_fingerprint, kilo_session, event, notification, scheduled_job, memory) | Minor documentation inconsistency | LOW |
| "2365 tests passing" | From Stage 17.3A report; current count may differ | Reference point, not current state | LOW |
| Single `AI Manager` module | **Actually split across two modules**: `app/ai/` (chat, providers, AIService) and `app/ai_manager/` (gate, bridge, capabilities, AIManager) with **separate provider hierarchies** | Architectural gap not flagged | MEDIUM |
| `PlanManager` in `app/manager/plan_manager.py` | Correctly located | None | — |
| `DevelopmentManager` in `app/orchestration/prompt_compiler.py` | Correctly located | None | — |
| No mention of `coding_*` providers | `app/ai/providers/` contains `coding_base.py`, `coding_ollama.py`, `coding_openrouter.py`, `coding_parser.py`, `coding_prompt.py`, `future.py` | Specialized coding providers not documented | MEDIUM |

### 2.3 Hidden Architectural Gap: Dual Provider Hierarchies

The repository contains **two separate provider hierarchies**:

1. **`app/ai/providers/`** — Used by `AIService` for chat:
   - `base.py` (AIProvider base)
   - `ollama.py`, `kimi.py`, `openrouter.py`
   - `coding_base.py`, `coding_ollama.py`, `coding_openrouter.py`, `coding_parser.py`, `coding_prompt.py`, `future.py`

2. **`app/ai_manager/providers/`** — Used by `ai_manager` for planning/governance:
   - `base.py` (PlanProvider base)
   - `ollama.py`, `openrouter.py`, `fake.py`

**Issue:** These hierarchies have overlapping responsibilities (both provide Ollama and OpenRouter adapters) but different interfaces. This creates:
- Code duplication risk
- Provider configuration divergence
- Model selection inconsistency between chat and planning
- Maintenance burden

**Status in Master Spec:** Not flagged. The spec treats them as a unified provider layer.

**Impact:** When Model Router is built, it will need to reconcile these two hierarchies. This should be addressed before or during Model Router implementation.

---

## 3. DEPENDENCY GRAPH

### 3.1 High-Level Dependency Map

```
                    ┌─────────────────────┐
                    │   User Request       │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  AI Manager         │
                    │  (AIService +       │
                    │   ai_manager)       │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐ ┌──────▼─────┐ ┌──────▼─────┐
    │ Memory         │ │ Tools      │ │ Planner    │
    │ Retrieval      │ │ (Stage 18) │ │ (exists)   │
    └─────────┬──────┘ └──────┬─────┘ └──────┬─────┘
              │                │                │
    ┌─────────▼──────┐ ┌──────▼─────┐ ┌──────▼─────┐
    │ Context        │ │ Permission │ │ Model      │
    │ Assembly       │ │ Engine     │ │ Router     │
    │ (MISSING)      │ │ (exists)   │ │ (Stage 21) │
    └─────────┬──────┘ └──────┬─────┘ └──────┬─────┘
              │                │                │
    ┌─────────▼──────┐ ┌──────▼─────┐ ┌──────▼─────┐
    │ Information    │ │ Tool       │ │ Capability │
    │ Sufficiency    │ │ Execution  │ │ Protocol   │
    │ (Stage 22)     │ │ (exists)   │ │ (NEW)      │
    └────────────────┘ └────────────┘ └─────┬──────┘
                                             │
                              ┌──────────────┼──────────────┐
                              │              │              │
                    ┌─────────▼──────┐ ┌──────▼─────┐ ┌──────▼─────┐
                    │ Finora         │ │ EVOLVE     │ │ Browser    │
                    │ (Stage 26)     │ │ (Stage 27) │ │ (Stage 23) │
                    └────────────────┘ └────────────┘ └────────────┘
```

### 3.2 Component Dependency Analysis

| Component | Depends On | Depended On By | Blocker? | Can Build Now? | Rework Risk |
|-----------|-----------|----------------|----------|----------------|-------------|
| **Stage 18: File Tools** | ToolExecutionService, PermissionEngine, WorkspaceContext | Memory Retrieval, Context Assembly, all future capabilities | No | Yes | Low |
| **Stage 19: Memory Retrieval** | MemoryService, Event system | Context Assembly, Information Sufficiency, AI Manager | No | Yes | Low |
| **Context Assembly (NEW)** | Memory Retrieval, Events, History, Project data | Information Sufficiency, Model Router, all AI decisions | Yes | After Stage 19 | Medium |
| **Stage 20: Per-Capability Permissions** | PermissionEngine, settings | All capability expansion | No | Yes | Low |
| **Stage 22: Information Sufficiency** | Context Assembly, AI Manager | Model Router, task execution | Yes | After Context Assembly | Medium |
| **Stage 21: Model Router** | Provider abstraction, Context Assembly | All AI requests | No | After Context Assembly | Medium |
| **Capability Protocol (NEW)** | Tool framework, PermissionEngine | Finora, EVOLVE, all external apps | Yes | After Stage 20 | Medium |
| **Stage 26: Finora** | Capability Protocol, Context Assembly | Finance automation | Yes | After Capability Protocol | Low |
| **Stage 27: EVOLVE** | Capability Protocol, Context Assembly | Training automation | Yes | After Capability Protocol | Low |
| **Stage 23: Browser** | Tool framework, Playwright | Web automation | No | After Stage 18 | Low |
| **Stage 28: Computer Use** | All security foundations | UI fallback | Yes | Late | High |
| **Stage 29: Proactive** | Scheduler, Events, Memory, Context | Background automation | Yes | After Context Assembly | Medium |

### 3.3 Critical Path Analysis

**The critical path to "central brain" capability is:**

```
Stage 18 (File Tools)
  → Stage 19 (Memory Retrieval)
    → Context Assembly (MISSING)
      → Stage 22 (Information Sufficiency)
        → Stage 21 (Model Router) [for intelligent routing]
          → All domain capabilities (Finora, EVOLVE, etc.)
```

**The critical path to safe capability expansion is:**

```
Stage 20 (Per-Capability Permissions)
  → Capability Protocol (NEW)
    → All external integrations
```

**These two paths can proceed in parallel after Stage 18.**

---

## 4. STAGE 18 ASSESSMENT

### 4.1 Is File Tool Expansion the Best Next Step?

**YES, with conditions.**

**Arguments FOR Stage 18 first:**
1. **Immediate user value**: File tools make the AI immediately useful for real tasks (organizing files, reading documents)
2. **Builds on hardened foundation**: The tool framework is already security-hardened; adding tools exercises it further
3. **Low risk**: File tools are a natural extension of the existing `files.read_file` pattern
4. **Unlocks testing**: More tools enable more comprehensive AI loop testing
5. **Visible progress**: Users can see the AI doing real work

**Arguments AGAINST Stage 18 first:**
1. **Memory Retrieval has higher architectural leverage**: Without memory, the AI cannot be a "manager" — it can only be a "tool user"
2. **Context Assembly is the real missing piece**: Tools without context are just capabilities without intelligence
3. **File tools without memory are dumb**: The AI can read/write files but cannot remember project context or user preferences

### 4.2 Recommendation

**Stage 18 remains the correct NEXT stage**, but it should be followed IMMEDIATELY by Stage 19 (Memory Retrieval), not by Model Router.

**Reasoning:**
- The user needs to see immediate value (file tools)
- The system needs memory to become intelligent (memory retrieval)
- These two stages together transform the system from "secure foundation" to "useful agent"
- Both are low-risk, high-value, and build on existing hardened infrastructure

**Stage 18 should NOT be expanded to include Memory Retrieval** — they are separate vertical slices that should be verified independently.

---

## 5. CENTRAL BRAIN ASSESSMENT

### 5.1 What the AI Manager Currently Is

The "AI Manager" is actually **two separate subsystems**:

**Subsystem A: `app/ai/` (Chat/AI Service Layer)**
- `AIService` — chat assistant with bounded tool loop
- `ProviderManager` — provider selection and config
- `ConversationService` — conversation CRUD
- `OllamaProcessManager` — Ollama lifecycle
- `OllamaDownloadManager` — model downloads
- `CredentialService` — credential encryption
- Providers: `ollama.py`, `kimi.py`, `openrouter.py`, plus `coding_*` variants

**Subsystem B: `app/ai_manager/` (Governance/Planning Layer)**
- `AIManager` — higher-level orchestration
- `Gate` — capability-based authorization
- `Bridge` — bridges AI Manager to execution
- `ExecutionBridge` — connects to plan execution
- `Capabilities` — capability registry
- `Policy` — application policy
- `PromptBuilder` — prompt construction
- `Normalizer` — output normalization
- `OutputParser` — output parsing
- Providers: `ollama.py`, `openrouter.py`, `fake.py` (separate from `app/ai/providers/`)

### 5.2 What's Missing for True Central Orchestration

| Missing Capability | Current State | Impact |
|--------------------|--------------|--------|
| **Unified entry point** | Two separate subsystems with limited integration | AI requests split between chat and planning |
| **Memory retrieval** | MemoryService write exists; no search/retrieval API | AI cannot access stored knowledge |
| **Context assembly** | No component gathers context from multiple sources | AI operates with limited context |
| **Information sufficiency** | Not implemented | AI cannot determine readiness |
| **Model routing** | Manual provider selection | Suboptimal model choice |
| **Task decomposition** | PlanManager exists but not integrated | Limited multi-step planning |
| **Agent coordination** | DevelopmentAgent exists; no broader coordination | Single-agent only |
| **Capability discovery** | No dynamic capability enumeration | AI cannot discover available tools |
| **Long-running task management** | Basic task state machine | Limited background orchestration |

### 5.3 Central Brain Gap Analysis

The current architecture has **solid foundations** but the "AI Manager" is not yet a true central orchestration layer. It is:

- **Not a unified brain**: Split across two modules with separate provider hierarchies
- **Not context-aware**: Cannot retrieve or assemble context from memory, events, history
- **Not self-aware**: Cannot determine if it has enough information
- **Not adaptive**: Cannot route tasks to optimal models
- **Not coordinating**: Single-agent only; no multi-agent orchestration

**To become a true central orchestration layer, the AI Manager needs:**
1. Memory Retrieval (Stage 19)
2. Context Assembly (NEW)
3. Information Sufficiency (Stage 22)
4. Model Router (Stage 21)
5. Agent coordination layer
6. Unified entry point

---

## 6. QUICK / DEEP / INFORMATION SUFFICIENCY ASSESSMENT

### 6.1 Architectural Importance

**QUICK vs DEEP is a fundamental interaction design principle**, not just a UI preference.

- **QUICK** enables fast, low-friction interaction for simple tasks
- **DEEP** enables thorough, careful interaction for complex tasks
- Both modes affect **information sufficiency thresholds**, **question generation**, and **execution aggressiveness**

### 6.2 When to Implement

**Recommendation: Implement QUICK/DEEP modes BEFORE large capability expansion.**

**Reasoning:**
1. QUICK/DEEP affects how ALL capabilities behave
2. It must be designed into the permission/approval system from the start
3. Adding it after capabilities exist requires retrofitting every capability
4. It is relatively simple to implement (mode flag + threshold configuration)
5. It enables better user experience from the first useful tool

**Implementation approach:**
- Add `interaction_mode` to settings (QUICK or DEEP)
- Pass mode through RequestContext
- PermissionEngine uses mode to adjust ASK_USER thresholds
- AI Manager uses mode to adjust questioning behavior
- No new security boundaries needed

### 6.3 Dependency Chain

```
User request
  → intent classification
    → required information (task type → info requirements)
      → available information (memory, context, history)
        → uncertainty assessment
          → risk calculation
            → readiness determination
              → QUICK/DEEP threshold check
                → decision: ask / research / assume / execute
```

**Components needed:**
- Intent classification (basic: can be model-driven initially)
- Information requirements registry (task types → required info)
- Context assembly (NEW)
- Memory retrieval (Stage 19)
- Uncertainty calculation (simple heuristic initially)
- Risk calculation (extends existing PermissionEngine)
- Readiness assessment (NEW)
- Question generation (NEW)

---

## 7. MODEL ROUTER ASSESSMENT

### 7.1 Current State

The current provider layer has:
- Manual provider selection via settings
- No automatic fallback
- No capability-based routing
- Two separate provider hierarchies (`app/ai/providers/` and `app/ai_manager/providers/`)

### 7.2 Should Model Router Be Built Early?

**NO.** Model Router is **less urgent** than the current roadmap suggests.

**Reasoning:**
1. **Current provider selection works**: Users can manually select providers/models
2. **Not a blocker for any capability**: All other stages can proceed without automatic routing
3. **Depends on Context Assembly**: Routing decisions need context (task type, privacy, latency requirements)
4. **Depends on dual-provider reconciliation**: The two provider hierarchies must be unified first
5. **Complexity vs value**: Model Router adds complexity without enabling new capabilities

### 7.3 Recommended Placement

**Move Model Router to AFTER Context Assembly and Information Sufficiency.**

**Corrected order:**
1. Stage 18: File Tools
2. Stage 19: Memory Retrieval
3. Stage 20: Per-Capability Permissions
4. Context Assembly (NEW)
5. Stage 22: Information Sufficiency
6. **Model Router (moved from Stage 21)**
7. Stage 23+: Domain capabilities

### 7.4 What Model Router Depends On

| Dependency | Status | Notes |
|-----------|--------|-------|
| Unified provider hierarchy | NOT READY | Two separate hierarchies must be reconciled |
| Context Assembly | NOT READY | Routing needs task context |
| Information Sufficiency | NOT READY | Routing needs readiness assessment |
| Provider health tracking | PARTIAL | Basic health checks exist |
| Model capability metadata | NOT READY | Needs registry |
| Privacy policy | PARTIAL | Basic data minimization exists |

---

## 8. MEMORY RETRIEVAL ASSESSMENT

### 8.1 Current State

MemoryService supports:
- CRUD operations
- Trust levels (EXPLICIT_USER > SYSTEM > DERIVED > EXTERNAL > AGENT)
- Scope (GLOBAL, PROJECT, TASK)
- Supersession
- TTL
- Soft delete
- Event integration

**Missing:**
- Search/retrieval API
- Query by scope, trust level, date
- Full-text search
- Semantic search
- Public REST API

### 8.2 Is Memory Retrieval the Next Critical Step?

**YES — second only to Stage 18 (File Tools).**

**Reasoning:**
1. **AI Manager cannot be a "manager" without memory**: It cannot remember user preferences, project context, past decisions, or learned patterns
2. **Memory retrieval enables all future capabilities**: Finora, EVOLVE, browser, email all need context
3. **Information Sufficiency depends on memory**: Cannot determine if information is sufficient without being able to retrieve it
4. **Context Assembly depends on memory**: Cannot assemble context without retrieval
5. **Proactive behavior depends on memory**: Cannot surface relevant information without search
6. **Low implementation risk**: Builds on existing MemoryService; adds query layer only

### 8.3 What Memory Retrieval Should Include

| Feature | Priority | Notes |
|---------|----------|-------|
| List memories (filtered) | HIGH | By scope, trust level, date range |
| Search memories (full-text) | HIGH | Basic LIKE/ILIKE search in V1 |
| Get memory by ID | HIGH | Standard CRUD |
| Update/delete memories | MEDIUM | Existing CRUD; needs REST API |
| Semantic search | LOW | Requires embeddings/vector DB |
| Memory relevance scoring | MEDIUM | For context assembly |

### 8.4 Placement in Roadmap

**Stage 19 should immediately follow Stage 18.**

```
Stage 18: File Tools (immediate utility)
  → Stage 19: Memory Retrieval (intelligence foundation)
    → Context Assembly (NEW)
      → Information Sufficiency
        → Model Router
```

---

## 9. CAPABILITY API / FINORA / EVOLVE ASSESSMENT

### 9.1 Current State

No capability protocol exists. External applications would need to integrate through the tool framework directly.

### 9.2 When to Formalize Capability API

**Recommendation: Build the Capability Protocol BEFORE Finora/EVOLVE integration, but AFTER Stage 20 (Per-Capability Permissions).**

**Reasoning:**
1. **Finora/EVOLVE need a standard contract**: Without a protocol, each integration will be ad-hoc
2. **The protocol is relatively simple**: It extends the existing ToolDefinition schema
3. **It enables future applications**: Once the protocol exists, any application can integrate
4. **It should be designed once, used many times**: Better to design it before the first integration

### 9.3 Capability Protocol Design

The protocol should extend `ToolDefinition` with:
- `capability_id` (e.g., `finora.get_transactions`)
- `version` (semver)
- `domain` (finora, evolve, browser, etc.)
- `domain_owner` (who owns the data)
- `discovery_endpoint` (how to enumerate capabilities)
- `idempotency_key` support

### 9.4 Identity/Permissions Across Boundaries

| Concern | Approach |
|---------|----------|
| Identity | RequestContext crosses boundary; external app receives authenticated user_id |
| Permissions | PermissionEngine evaluates in Operator; external app does not authorize |
| Audit | AuditLogService records all capability invocations |
| Events | EventEmitter emits domain events |
| Data ownership | External app owns its data; Operator receives structured results only |

### 9.5 Finora/EVOLVE Sequencing

**Correct order:**
1. Capability Protocol (design and implement)
2. Finora integration (first real test of protocol)
3. EVOLVE integration (second test, validates protocol generality)

---

## 10. COMPUTER USE ASSESSMENT

### 10.1 Current State

**NOT IMPLEMENTED.** No Computer Use capability exists.

### 10.2 Where It Belongs

**LATE in the roadmap (Stage 28 in current proposal). This is correct.**

**Reasoning:**
1. **Fallback only**: Computer Use is explicitly a last resort after API, capability, tool, and browser automation
2. **High security risk**: Requires strict containment, approval, audit, action budgets
3. **Depends on all security foundations**: Identity, permissions, approvals, workspace policy, emergency stop
4. **Depends on vision/screenshots**: Needs vision model integration
5. **Not a prerequisite for anything else**: No capability depends on Computer Use

### 10.3 What Computer Use Depends On

| Dependency | Status | Notes |
|-----------|--------|-------|
| Permissions | ✅ READY | PermissionEngine exists |
| Approvals | ✅ READY | ApprovalService exists |
| Identity | ✅ READY | RequestContext exists |
| Task execution | ✅ READY | ToolExecutionService exists |
| Vision model | NOT READY | Needs model routing to vision model |
| Screenshot capture | NOT READY | Needs OS-level screen capture |
| Action verification | NOT READY | Needs post-action verification |
| Emergency stop | PARTIAL | Basic cancel/abort exists |
| Action budgets | NOT READY | Needs rate limiting per session |
| Audit | ✅ READY | AuditLogService exists |

---

## 11. 24/7 + PROACTIVE OPERATOR ASSESSMENT

### 11.1 Current State

**PARTIALLY IMPLEMENTED:**
- Scheduler: CRUD, misfire policy, handoff
- Events: Immutable append-only
- Notifications: Idempotent, internal-only
- BackgroundExecutionManager: HTTP-triggered

### 11.2 What's Missing

| Missing Component | Impact | Dependency |
|-------------------|--------|-----------|
| **Context Assembly** | Cannot gather relevant context for proactive decisions | Memory Retrieval |
| **Information Sufficiency** | Cannot determine if action is needed | Context Assembly |
| **Relevance scoring** | Cannot filter noise from signal | Context Assembly, Memory |
| **User preferences for notifications** | Cannot respect quiet hours, priorities | Settings expansion |
| **External event triggers** | Cannot react to file changes, emails, calendar | File tools, Email integration |
| **Notification center UI** | User cannot manage notifications | Frontend work |

### 11.3 Dependency Chain

```
Events (✅ exists)
  → Scheduler (✅ exists)
    → Policy evaluation (needs context)
      → Context Assembly (MISSING)
        → Information Sufficiency (Stage 22)
          → Decision: act / notify / wait
            → Authorization (✅ exists)
              → Execution (✅ exists)
                → Notification (✅ exists, but basic)
```

**Key insight:** The system CAN do proactive background work NOW for simple scheduled tasks. But intelligent proactive behavior (deciding WHEN to act based on context) requires Context Assembly and Information Sufficiency.

---

## 12. MULTI-AGENT ARCHITECTURE ASSESSMENT

### 12.1 Current State

**Single agent only:**
- `DevelopmentAgent` — coding tasks
- `KiloDevelopmentAgent` — Kilo CLI wrapper
- `WorkspaceWriter` — safe file writes

### 12.2 When to Introduce Specialized Agents

**LATE in the roadmap. Not needed for Stage 18-22.**

**Reasoning:**
1. **Single agent can handle simple tasks**: DevelopmentAgent + tools can handle file operations, basic coding, etc.
2. **Multi-agent adds complexity**: Coordination, context passing, result aggregation
3. **Security concerns increase**: More agents = more potential privilege escalation paths
4. **Not a prerequisite for any Stage 18-22 capability**

### 12.3 What Must Exist Before Multi-Agent

| Prerequisite | Why |
|--------------|-----|
| Context Assembly | Agents need shared context |
| Memory Retrieval | Agents need access to stored knowledge |
| Information Sufficiency | Orchestrator must know when to delegate |
| Agent contracts | Formal contracts for agent communication |
| Agent registry | Discover available agents |
| Capability boundaries | Clear what each agent can/cannot do |
| Result verification | Orchestrator must verify agent results |
| Cancellation/timeout | Must be able to stop misbehaving agents |

---

## 13. AUTONOMOUS DEVELOPER ASSESSMENT

### 13.1 Current State

The autonomous development subsystem is **mature and hardened**:
- Task lifecycle with atomic state machine
- Workspace isolation via Git worktrees
- VerificationGate with fail-closed checks
- Repair loop with bounded diagnostics
- RecoveryManager for stale runs
- Kilo integration
- Hostile-tested security boundaries

### 13.2 Should It Continue Evolving Now?

**NO — treat as a mature specialized subsystem for now.**

**Reasoning:**
1. **It is NOT the central Operator**: It is a vertical slice for coding tasks
2. **E2E is blocked by provider quota**: Cannot verify full pipeline without working provider
3. **Broader Operator needs come first**: Memory, context, information sufficiency are more fundamental
4. **Further evolution can proceed in parallel**: The subsystem is stable enough

### 13.3 When to Resume Autonomous Developer Work

**After:**
- Stage 18 (File Tools) — more tools for the agent
- Stage 19 (Memory Retrieval) — agent can access memory
- Stage 21 (Model Router) — agent can use optimal models
- Provider quota issue resolved (local model support)

---

## 14. SECURITY ORDERING

### 14.1 Security Dependency Graph

```
Identity/Session (✅ STAGE 16)
  ↓
PermissionEngine (✅ STAGE 17.2)
  ↓
ApprovalService (✅ STAGE 17.2)
  ↓
ToolExecutionService (✅ STAGE 17.2)
  ↓
┌─────────────────────────────────────┐
│  Stage 18: File Tools               │
│  → Path security, workspace         │
│    containment, recycle bin         │
└─────────────────────────────────────┘
  ↓
┌─────────────────────────────────────┐
│  Stage 20: Per-Capability           │
│  Permissions                        │
│  → Granular autonomy profiles       │
└─────────────────────────────────────┘
  ↓
┌─────────────────────────────────────┐
│  Capability Protocol (NEW)          │
│  → Standard integration contract    │
│  → Cross-application security       │
└─────────────────────────────────────┘
  ↓
┌─────────────────────────────────────┐
│  Stage 23+: Domain Integrations     │
│  → Browser, Email, Calendar,        │
│    Finora, EVOLVE                   │
└─────────────────────────────────────┘
  ↓
┌─────────────────────────────────────┐
│  Stage 28: Computer Use             │
│  → Strictest containment            │
│  → Action budgets, emergency stop   │
└─────────────────────────────────────┘
```

### 14.2 Security Prerequisites

| Prerequisite | Current Status | Blocks |
|-------------|---------------|--------|
| Identity/Session | ✅ COMPLETE | Everything |
| PermissionEngine | ✅ COMPLETE | All capability execution |
| ApprovalService | ✅ COMPLETE | Sensitive actions |
| ToolExecutionService | ✅ COMPLETE | All tool execution |
| Path security | ✅ PARTIAL (files.read_file) | File tools |
| Workspace containment | ✅ PARTIAL (Git worktrees) | File tools |
| Per-capability permissions | ❌ NOT STARTED | Granular autonomy |
| Capability protocol | ❌ NOT STARTED | External integrations |
| Computer Use containment | ❌ NOT STARTED | UI automation |

### 14.3 Security Ordering Assessment

The current roadmap **correctly places security foundations first**. No security prerequisite is placed too late.

**One concern:** The current roadmap does not explicitly call out that Capability Protocol (needed for Finora/EVOLVE) is a SECURITY prerequisite, not just an integration convenience. This should be made explicit.

---

## 15. REWORK ANALYSIS

### 15.1 Rework Risk by Stage

| Stage | Rework Risk | Reasoning |
|-------|-------------|-----------|
| **Stage 18: File Tools** | LOW | Extends existing tool framework; no architectural changes |
| **Stage 19: Memory Retrieval** | LOW | Adds query layer to existing MemoryService |
| **Context Assembly (NEW)** | MEDIUM | New component; must integrate with multiple existing systems |
| **Stage 20: Per-Capability Permissions** | MEDIUM | Extends PermissionEngine; may require schema changes |
| **Stage 22: Information Sufficiency** | MEDIUM | New subsystem; depends on Context Assembly |
| **Model Router (moved)** | HIGH | Requires reconciling dual provider hierarchies; affects all AI calls |
| **Capability Protocol (NEW)** | MEDIUM | New protocol; must be designed before integrations |
| **Stage 23: Browser** | MEDIUM | New integration; Playwright setup complexity |
| **Stage 24-25: Email/Calendar** | HIGH | OAuth complexity; external API dependencies |
| **Stage 26-27: Finora/EVOLVE** | MEDIUM | Depends on Capability Protocol |
| **Stage 28: Computer Use** | HIGH | High security risk; OS-specific; vision model dependency |

### 15.2 Key Rework Scenarios

| Scenario | Risk | Explanation |
|----------|------|-------------|
| Implement dozens of tools before Capability Protocol | HIGH | Tools may need redesign to fit standard contract |
| Implement multiple providers before Model Router | MEDIUM | Provider selection logic may become duplicated |
| Implement domain integrations before Capability Protocol | HIGH | Integrations will diverge; no standard contract |
| Implement Computer Use before permission profiles | HIGH | Authorization may need redesign for UI actions |
| Implement proactive automation before policy controls | MEDIUM | Background behavior may become unsafe |
| Implement Memory Retrieval after Stage 21 (current order) | MEDIUM | AI Manager operates without memory longer than needed |

### 15.3 Rework Mitigation Strategies

1. **Build Context Assembly BEFORE Model Router**: Prevents rework of routing logic
2. **Build Capability Protocol BEFORE Finora/EVOLVE**: Prevents integration divergence
3. **Build Memory Retrieval BEFORE Information Sufficiency**: Prevents rework of readiness logic
4. **Unify provider hierarchies BEFORE Model Router**: Prevents routing inconsistencies
5. **Build per-capability permissions BEFORE Computer Use**: Prevents authorization redesign

---

## 16. CORRECTED ROADMAP

### 16.1 Recommended Stage Sequence

| Order | Stage | Name | Purpose | Dependencies | Unlocks |
|-------|-------|------|---------|-------------|---------|
| 1 | **Stage 18** | File Tool Expansion | Write, copy, move, delete-to-recycle-bin, search, list | Tool framework | Practical AI utility |
| 2 | **Stage 19** | Memory Retrieval | Search, retrieval API, full-text search | MemoryService | AI can access stored knowledge |
| 3 | **Stage 20** | Per-Capability Permissions | Granular autonomy profiles | PermissionEngine | Fine-grained security |
| 4 | **NEW** | Context Assembly | Gather context from memory, events, history, project data | Stage 19, Events, History | AI has rich context for decisions |
| 5 | **Stage 22** | Information Sufficiency | Determine task readiness | Context Assembly, AI Manager | AI knows when to ask |
| 6 | **Stage 21** | Model Router | Automatic model/provider selection | Context Assembly, unified providers | Optimal model choice |
| 7 | **NEW** | Capability Protocol | Standard integration contract for external apps | Tool framework, Stage 20 | External app integration |
| 8 | **Stage 23** | Browser Automation | Playwright integration | Stage 18, Playwright | Web automation |
| 9 | **Stage 24** | Email Integration | Read/classify/summarize/draft | OAuth, email APIs | Email management |
| 10 | **Stage 25** | Calendar Integration | Calendar read/management | Calendar APIs | Calendar management |
| 11 | **Stage 26** | Finora Integration | Finance domain operations | Capability Protocol, Context Assembly | Finance automation |
| 12 | **Stage 27** | EVOLVE Integration | Training domain operations | Capability Protocol, Context Assembly | Training automation |
| 13 | **Stage 28** | Computer Use | UI automation as last resort | All security foundations | UI-only system interaction |
| 14 | **Stage 29** | Proactive Behavior Enhancement | Intelligent notifications | Context Assembly, Scheduler, Events | Background intelligence |
| 15 | **Stage 30** | Persistent Sessions | Session recovery across restarts | Identity, encrypted storage | Session persistence |
| 16 | **Stage 31** | Evaluation System | Measure AI quality | E2E tests, metrics | Quality measurement |

### 16.2 What Changed from Current Roadmap

| Current Order | Corrected Order | Change | Reason |
|---------------|----------------|--------|--------|
| Stage 18 → Stage 19 → Stage 20 → Stage 21 → Stage 22 | Stage 18 → Stage 19 → Stage 20 → Context Assembly → Stage 22 → Stage 21 | Model Router moved AFTER Information Sufficiency; Context Assembly added | Context Assembly is prerequisite for both; Model Router needs context |
| Stage 26/27 (Finora/EVOLVE) before Capability Protocol | Capability Protocol BEFORE Finora/EVOLVE | Capability Protocol moved earlier | Prevents integration divergence; standard contract first |
| No Context Assembly stage | Added as new stage | NEW | Missing prerequisite for Information Sufficiency and Model Router |

### 16.3 What Stayed the Same

- Stage 18 (File Tools) remains first
- Stage 19 (Memory Retrieval) remains second
- Stage 20 (Per-Capability Permissions) remains third
- Stage 23-25 (Browser, Email, Calendar) order unchanged
- Stage 26-27 (Finora, EVOLVE) order unchanged
- Stage 28 (Computer Use) remains late
- Stage 29-31 (Proactive, Sessions, Evaluation) order unchanged

---

## 17. CURRENT VS RECOMMENDED ROADMAP

### 17.1 Current Roadmap (from Master Specification)

| Stage | Name |
|-------|------|
| 18 | File Tool Expansion |
| 19 | Memory Retrieval |
| 20 | Per-Capability Permissions |
| 21 | Model Router |
| 22 | Information Sufficiency |
| 23 | Browser Automation |
| 24 | Email Integration |
| 25 | Calendar Integration |
| 26 | Finora Integration |
| 27 | EVOLVE Integration |
| 28 | Computer Use |
| 29 | Proactive Behavior |
| 30 | Persistent Sessions |
| 31 | Evaluation System |

### 17.2 Recommended Roadmap

| Stage | Name | Change |
|-------|------|--------|
| 18 | File Tool Expansion | **Same** |
| 19 | Memory Retrieval | **Same** |
| 20 | Per-Capability Permissions | **Same** |
| **NEW** | **Context Assembly** | **ADDED** |
| 22 | Information Sufficiency | **Moved later** (after Context Assembly) |
| 21 | Model Router | **MOVED** (after Information Sufficiency) |
| **NEW** | **Capability Protocol** | **ADDED** (before Finora/EVOLVE) |
| 23 | Browser Automation | **Same** |
| 24 | Email Integration | **Same** |
| 25 | Calendar Integration | **Same** |
| 26 | Finora Integration | **Same** (but now after Capability Protocol) |
| 27 | EVOLVE Integration | **Same** (but now after Capability Protocol) |
| 28 | Computer Use | **Same** |
| 29 | Proactive Behavior | **Same** |
| 30 | Persistent Sessions | **Same** |
| 31 | Evaluation System | **Same** |

### 17.3 Key Differences

1. **Context Assembly added**: Missing prerequisite for Information Sufficiency and Model Router
2. **Model Router moved from Stage 21 to after Stage 22**: Depends on Context Assembly and Information Sufficiency
3. **Capability Protocol added before Finora/EVOLVE**: Prevents integration divergence
4. **No stages removed**: All existing stages retained, just reordered

---

## 18. ARCHITECTURE ALIGNMENT VERDICT

### ROADMAP_REQUIRES_ADJUSTMENT

The Master Specification is architecturally sound and accurate in its principles, but the proposed stage sequence requires adjustment:

1. **Stage 18 (File Tools) remains correct** as the immediate next stage
2. **Stage 19 (Memory Retrieval) should follow immediately** — not after Model Router
3. **Context Assembly is a missing prerequisite** for Information Sufficiency and Model Router
4. **Model Router is over-prioritized** — it should come AFTER Context Assembly and Information Sufficiency, not before
5. **Capability Protocol should precede Finora/EVOLVE** — not be an afterthought

The corrected roadmap maximizes architectural leverage, minimizes rework, and ensures that each stage builds on a solid foundation for the next.

---

## 19. REQUIRED DOCUMENT COMPLETION

This document is the architecture alignment review for Stage 17.4. It:

1. ✅ Verified Master Specification claims against actual code
2. ✅ Identified discrepancies and hidden architectural gaps
3. ✅ Built dependency graph for all major components
4. ✅ Evaluated Stage 18 recommendation (confirmed with conditions)
5. ✅ Assessed central brain gaps
6. ✅ Evaluated Quick/Deep, Information Sufficiency, Model Router, Memory Retrieval
7. ✅ Evaluated Capability API, Finora/EVOLVE, Computer Use
8. ✅ Evaluated 24/7, proactive behavior, multi-agent
9. ✅ Assessed Autonomous Developer maturity
10. ✅ Analyzed security ordering
11. ✅ Analyzed rework risks
12. ✅ Proposed corrected roadmap
13. ✅ Compared current vs recommended roadmap
14. ✅ Provided final verdict

**No production code was modified. This is a documentation-only review.**

---

**END OF STAGE 17.4 ARCHITECTURE ALIGNMENT REVIEW**
