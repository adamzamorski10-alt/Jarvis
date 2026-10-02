# STAGE_20_1_OPERATOR_PIPELINE_DISCOVERY.md

## AI Operator — Unified Operator Request Pipeline Discovery

---

## 1. EXECUTIVE SUMMARY

The AI Operator has a collection of well-designed, security-conscious subsystems:
Context Assembly, Information Sufficiency, Clarification Loop, AI Manager, PlanManager,
ExecutionBridge, TaskRepository, PermissionEngine, ApprovalService, ToolExecutionService,
AutonomousTaskOrchestrator, and an Event/History system.

However, these subsystems are **not yet connected into one authoritative request-to-execution
pipeline**. The current `/api/ai/chat` flow in `AIService.chat()` is the closest thing to a
canonical path, but it is missing several critical elements:

- No canonical request contract (no `OperatorRequest`)
- No unified normalization step
- No explicit intent/operation classification boundary
- No structured response contract for the frontend
- No event emission for request lifecycle transitions
- No coherent failure model
- Some duplicate orchestration logic across AI Service and AI Manager

The goal of Stage 20 is to connect these existing foundations into one authoritative
application-owned flow **without redesigning the architecture**.

---

## 2. CURRENT REQUEST FLOW

### 2.1 HTTP Entry Point

**File:** `services/core/app/api/routes/ai.py`
**Line:** 183-243
**Function:** `async def chat(request: Request, req: ChatRequest, ai: AIService = Depends(get_ai_service))`

**Flow:**
1. `require_authenticated(request)` — validates session
2. `get_request_context(request)` — builds `RequestContext`
3. `ai.chat(req.conversation_id, req.message, req.client_message_id, actor_id=ctx.actor_id, request_context=ctx)`
4. Returns `ChatResponse` with `conversation`, `message`, optional `approval_required`, `clarification_required`, `agent_loop`

### 2.2 AIService.chat() — The De Facto Canonical Path

**File:** `services/core/app/ai/services/ai_service.py`
**Line:** 641-837

**Full call chain:**
```
AIService.chat()
  │
  ├── Provider config validation (lines 649-706)
  ├── Conversation get/create (lines 709-717)
  ├── ConversationService.add_message() (line 711/716)
  ├── ConversationService.build_context_messages() (line 719)
  │
  ├── Context Assembly (lines 725-741)
  │   └── ContextAssemblyService.assemble()
  │       └── MemoryRetrievalService.retrieve_memories()
  │
  ├── Information Sufficiency + Clarification Loop (line 744)
  │   └── _process_clarification_if_needed()
  │       ├── ClarificationService.list_pending_for_conversation()
  │       │
  │       ├── IF active clarification:
  │       │   └── _handle_clarification_answer()
  │       │       ├── ClarificationService.respond()
  │       │       ├── ContextAssemblyService.assemble() (reassembly)
  │       │       ├── InformationSufficiencyService.assess()
  │       │       ├── ClarificationService.mark_satisfied() / advance_round()
  │       │       └── Returns clarification response dict
  │       │
  │       └── IF no active clarification:
  │           └── _run_sufficiency_and_maybe_clarify()
  │               ├── InformationSufficiencyService.assess()
  │               ├── IF INSUFFICIENT/AMBIGUOUS:
  │               │   └── ClarificationService.create()
  │               │       └── Returns clarification response dict
  │               └── IF SUFFICIENT: returns None
  │
  ├── Agent Loop (line 760)
  │   └── _run_agent_loop() (lines 469-569)
  │       ├── Gets tool definitions
  │       ├── Builds provider messages
  │       ├── LOOP:
  │       │   ├── provider.chat()
  │       │   ├── IF tool calls:
  │       │   │   └── _execute_tool_calls()
  │       │   │       └── ToolExecutionService.execute_tool_call()
  │       │   │           ├── PermissionEngine.evaluate()
  │       │   │           └── ToolExecutor.execute()
  │       │   └── IF CANCELLED with ask_user:
  │       │       └── ApprovalService.create_pending_approval()
  │       └── Returns LoopResult
  │
  ├── ConversationService.add_message() (line 814)
  └── Returns result dict (lines 822-837)
```

### 2.3 Key Observation

`AIService.chat()` already integrates:
- Context Assembly
- Information Sufficiency
- Clarification Loop
- Agent Loop with tool execution and approval

But it is **missing**:
- A canonical request contract
- Explicit operation classification
- Structured response states
- Event emission
- AI Manager integration
- Task creation
- Plan creation
- Execution bridge integration

---

## 3. EXISTING REUSABLE SERVICES

### 3.1 Context Assembly Service

**File:** `services/core/app/context_assembly/service.py`
**Input:** `ContextAssemblyRequest` (application-controlled)
**Output:** `AssembledContext` (application-owned)
**Callers:**
- `AIService.chat()` (line 726)
- `AIService._handle_clarification_answer()` (line 303)
- `AutonomousTaskOrchestrator.start_task()` (orchestrator.py line 296)

**Security boundary:** Read-only. Does NOT execute tools, mutate state, or grant permissions.

### 3.2 Information Sufficiency Service

**File:** `services/core/app/information_sufficiency/service.py`
**Input:** `InformationSufficiencyRequest` (application-controlled)
**Output:** `SufficiencyResult` (application-owned)
**States:** `SUFFICIENT`, `INSUFFICIENT`, `AMBIGUOUS`
**Callers:**
- `AIService._process_clarification_if_needed()` (line 408)
- `AIService._handle_clarification_answer()` (line 324)

### 3.3 Clarification Service

**File:** `services/core/app/clarification/service.py`
**Input:** `ClarificationCreateRequest`, `ClarificationRespondRequest`
**Output:** `ClarificationSession`, `ClarificationResponse`
**States:** `PENDING`, `RESPONDED`, `EXPIRED`, `CANCELLED`, `SATISFIED`
**Callers:**
- `AIService._process_clarification_if_needed()` (line 227, 280, 344, 354, 368)

### 3.4 AI Manager

**File:** `services/core/app/ai_manager/ai_manager.py`
**Input:** `UserIntent` (application-controlled)
**Output:** `ManagerResult` (application-owned)
**DecisionOutcome:** `REJECTED`, `NEEDS_CLARIFICATION`, `NEEDS_APPROVAL`, `PROPOSED`, `READY_FOR_PLANNING`
**Key property:** Does NOT execute actions, import execution modules, or grant permissions.

### 3.5 PlanManager

**File:** `services/core/app/manager/plan_manager.py`
**Input:** `UserPlan` (application-controlled)
**Output:** `ManagerPlanResult` (application-owned)
**Key property:** Thin orchestration over `PlanProvider`. Does NOT persist or execute.

### 3.6 ExecutionBridge

**File:** `services/core/app/ai_manager/execution_bridge.py`
**Input:** `ManagerResult`, `project_id`
**Output:** `ExecutionPrepareResult`, `ExecutionResult`
**Key property:** The ONLY authorized path from AI Manager decisions to plan execution.

### 3.7 TaskRepository

**File:** `services/core/app/repositories/task_repository.py`
**Key methods:** `create_task()`, `get_task()`, `create_tasks_idempotent()`, `transition_status_atomically()`
**Key property:** Uses SQLite with atomic CAS operations for state transitions.

### 3.8 PermissionEngine

**File:** `services/core/app/tools/permission_engine.py`
**Input:** `PermissionEvaluationRequest`
**Output:** `PermissionDecision`
**Key property:** Fail-closed. DENY is the default.

### 3.9 ApprovalService

**File:** `services/core/app/tools/approval_service.py`
**Input:** `conversation_id`, `tool_call`, `arguments`, `action`, `request_id`
**Output:** `PendingApproval`
**Callers:**
- `AIService._run_agent_loop()` (line 539)
- `ExecutionBridge._create_approval()` (line 426)

### 3.10 ToolExecutionService

**File:** `services/core/app/tools/tool_execution_service.py`
**Input:** `ToolCall`, `action`, `autonomy_mode`, `request_id`, `context`, `approval_context`, `actor_id`, `workspace_path`
**Output:** `ToolExecutionResult`
**Key property:** The ONLY production component that calls both `PermissionEngine.evaluate()` and `ToolExecutor.execute()`.

### 3.11 AutonomousTaskOrchestrator

**File:** `services/core/app/orchestration/orchestrator.py`
**Key method:** `start_task()` (line 99)
**Enforces:** 8 architectural invariants including zero raw Git/shell/subprocess calls.

### 3.12 Event/History Infrastructure

**File:** `services/core/app/events/emitter.py`
**Event types:** `TASK_STARTED`, `TASK_COMPLETED`, `TASK_FAILED`, `PLAN_STARTED`, `PLAN_COMPLETED`, `PLAN_FAILED`, `CLARIFICATION_REQUESTED`, `CLARIFICATION_RESPONDED`, etc.
**Key property:** Append-only, idempotent by `event_id`, sanitized.

### 3.13 Conversation Persistence

**File:** `services/core/app/ai/services/conversation_service.py`
**Models:** `Conversation`, `Message` (in `services/core/app/db/models/conversation.py`)
**Key property:** Deduplicates user messages by `client_message_id`.

---

## 4. CURRENT EXECUTION ENTRY POINTS

### 4.1 Agent Loop
- `AIService._run_agent_loop()` — `ai_service.py:760` (only called from `AIService.chat()`)

### 4.2 Tool Execution
- `ToolExecutionService.execute_tool_call()` — `tool_execution_service.py:199`
  - Callers: `ai_service.py:460`, `approval_service.py:192`, `plan_execution_service.py:122`, `api/routes/plans.py:129,488`, `api/routes/files.py:40,75,115`, `api/routes/system.py:91`

### 4.3 Plan Execution
- `AutonomousPlanExecutor.execute_plan()` — `autonomous_loop.py:108`
- `PlanExecutionService.execute_planned_task()` — `plan_execution_service.py:59`
- `RunController.start_run()` — `run_controller.py:163`

### 4.4 Task Execution
- `AutonomousTaskOrchestrator.start_task()` — `orchestrator.py:99`

### 4.5 AI Manager Decision
- `AIManager.decide()` — `ai_manager.py:77` (wired via DI, no external direct callers found)

### 4.6 Plan Creation
- `PlanManager.plan()` — `plan_manager.py:63`
- `ManagerPlanningService.plan_decision()` — `bridge.py:208`

### 4.7 Execution Bridge
- `ExecutionBridge.prepare()` — `execution_bridge.py:235`
- `ExecutionBridge.execute()` — `execution_bridge.py:289`

---

## 5. DUPLICATE ORCHESTRATION LOGIC

### 5.1 Found
- **Multiple chat entry points:**
  - `AIService.chat()` — main AI assistant chat
  - `api/routes/plans.py:202` — `execute_plan` endpoint (separate from chat)
- **Multiple agent loop invocations:** Only one (`AIService._run_agent_loop`), called only from `AIService.chat()`
- **Multiple task execution entry points:**
  - `AutonomousTaskOrchestrator.start_task()` — used by `RunController`
  - `PlanExecutionService.execute_planned_task()` — used by `AutonomousPlanExecutor`
  - `AutonomousPlanExecutor.execute_plan()` — used by `ExecutionBridge` and `background_execution_manager.py`
- **No duplicate task creation logic** — all paths converge through `TaskRepository.create_tasks_idempotent()`

### 5.2 Not Found
- No duplicate operation classification
- No duplicate readiness checks
- No duplicate context assembly

---

## 6. AUTHORITY BOUNDARIES

### 6.1 Application Remains Authoritative Over
- `identity` (user_id, actor_type, session_id)
- `request_context`
- `permissions`
- `approvals`
- `tool execution`
- `workspace boundaries`
- `readiness`
- `task persistence`
- `plan persistence`
- `execution state`

### 6.2 AI Models May
- interpret user intent
- classify operations
- propose tasks
- propose plans
- generate structured decisions
- explain results

### 6.3 AI Models Must NOT
- grant permissions
- grant approvals
- change identity
- select arbitrary workspace outside application policy
- directly execute tools
- bypass Information Sufficiency
- bypass PermissionEngine
- bypass ApprovalService
- bypass VerificationGate
- mutate authoritative state without an authorized application service

---

## 7. MISSING CONTRACTS

### 7.1 Missing: OperatorRequest
There is **no canonical representation of a normalized user request**.
The current flow passes raw `message: str` through all subsystems.

### 7.2 Missing: Structured Response Contract
The current `ChatResponse` returns free-form `dict` with optional fields.
There is no explicit state machine for the response (e.g., ANSWERED, CLARIFICATION_REQUIRED, TASK_CREATED, etc.).

### 7.3 Missing: Event Contract
Events exist for task/plan/clarification lifecycle, but **not** for:
- `operator.request.received`
- `operator.request.normalized`
- `operator.request.classified`
- `operator.request.ready`
- `operator.request.blocked`

### 7.4 Missing: Error/Failure Model
Errors are raised as exceptions with ad-hoc codes. There is no unified failure taxonomy.

### 7.5 Missing: Operation Classification Boundary
`OperationType` has only 4 values: `DECISION`, `PLANNING`, `CODING`, `CHAT`.
The classifier does not exist as a standalone service.

---

## 8. PROPOSED TARGET FLOW

```
USER MESSAGE
    │
    ▼
AUTHENTICATION / RequestContext
    │
    ▼
OPERATOR REQUEST CREATION (new)
    │   - request_id (generated)
    │   - conversation_id
    │   - message_id
    │   - request_context (from auth)
    │   - user_request (raw text)
    │   - correlation_id
    │   - causation_id
    │
    ▼
REQUEST NORMALIZATION (new)
    │   - deterministic field extraction
    │   - preserve original text
    │   - bound all externally-influenced fields
    │   - reject malformed/oversized values
    │
    ▼
OPERATION / INTENT CLASSIFICATION (new)
    │   - deterministic first
    │   - AI-assisted when needed
    │   - bounded by existing OperationType
    │   - fail-closed when classification is required
    │
    ▼
CONTEXT ASSEMBLY (existing)
    │   - ContextAssemblyService.assemble()
    │   - Memory retrieval
    │
    ▼
INFORMATION SUFFICIENCY (existing)
    │   - InformationSufficiencyService.assess()
    │   - fail-closed: only SUFFICIENT continues
    │
    ▼
CLARIFICATION LOOP (existing, unified)
    │   - ClarificationService.create() / respond()
    │   - preserve correlation
    │   - re-run Context Assembly + Sufficiency on answer
    │   - only continue when SUFFICIENT
    │
    ▼
AI MANAGER DECISION (existing)
    │   - UserIntent (application-controlled)
    │   - AIManager.decide()
    │   - ManagerResult (application-owned)
    │
    ▼
APPLICATION POLICY / CAPABILITY GATE (existing)
    │   - PolicyCapabilityGate.authorize()
    │
    ▼
DECISION ROUTING
    │
    ├── REJECTED ──────────────────────────────────────────► RESPONSE
    ├── NEEDS_CLARIFICATION ───────────────────────────────► RESPONSE
    ├── NEEDS_APPROVAL ────────────────────────────────────► APPROVAL FLOW
    ├── PROPOSED ──────────────────────────────────────────► TASK CREATION
    └── READY_FOR_PLANNING ────────────────────────────────► PLAN CREATION
                                                              │
                                                              ▼
                                                        EXECUTION BRIDGE
                                                              │
                                                              ▼
                                                        APPROVAL (if needed)
                                                              │
                                                              ▼
                                                        EXECUTION
                                                              │
                                                              ▼
                                                        RESULT
                                                              │
                                                              ▼
                                                        EVENT / HISTORY
                                                              │
                                                              ▼
                                                        USER RESPONSE
```

---

## 9. MIGRATION STRATEGY

### 9.1 Approach
- **Do not replace** existing subsystems
- **Do not create** a second orchestration architecture
- **Extend** `AIService.chat()` to use the new `OperatorRequest` contract
- **Add** new event types to the existing EventEmitter
- **Add** new response states to the existing response model
- **Integrate** AI Manager into the existing chat flow (currently not called from chat)
- **Preserve** all existing security boundaries

### 9.2 Phases
1. Define `OperatorRequest` contract and normalizer
2. Integrate operation classification
3. Unify clarification with `OperatorRequest`
4. Integrate AI Manager into `AIService.chat()`
5. Integrate Task creation
6. Integrate Plan creation / Execution handoff
7. Add event emission
8. Add structured response model
9. Add error/failure model
10. Hostile audit and regression

### 9.3 Backward Compatibility
- Existing `/api/ai/chat` endpoint behavior preserved
- Existing `AIService.chat()` signature preserved
- Existing subsystems remain importable and usable
- No breaking changes to existing API contracts

---

## 10. RISKS

### 10.1 High Risk
- **AI Manager not currently integrated into chat flow**: Adding it could change behavior for existing users
- **OperationType enum is narrow**: Extending it could affect Context Assembly and other subsystems

### 10.2 Medium Risk
- **Clarification loop integration**: Current clarification logic is tightly coupled to `AIService.chat()`
- **Event emission performance**: Adding events to every request could impact throughput

### 10.3 Low Risk
- **New modules**: Adding `operator_request/` directory is low risk
- **New tests**: Adding tests is always safe

---

## 11. EXPLICIT DECISIONS

### Decision 1: Extend OperationType vs. Create New Enum
**Decision:** Extend existing `OperationType` in `context_assembly/models.py` carefully.
**Rationale:** The spec says "Do NOT create duplicate enums if an existing canonical enum already exists."

### Decision 2: Modify AIService.chat() vs. Create New Service
**Decision:** Extend `AIService.chat()` to use `OperatorRequest` internally.
**Rationale:** The spec says "Do not replace existing subsystems." Creating a parallel chat service would violate this.

### Decision 3: Event Types
**Decision:** Add new event types to existing `EventType` enum in `events/schemas.py`.
**Rationale:** The spec says "Do not duplicate existing event types unnecessarily."

### Decision 4: AI Manager Integration Point
**Decision:** Integrate AI Manager decision AFTER clarification/sufficiency, BEFORE agent loop.
**Rationale:** This is the natural point where the application knows the request is ready and can decide what to do with it.

### Decision 5: Task Creation Trigger
**Decision:** Create Task only when AI Manager returns `PROPOSED` or `READY_FOR_PLANNING` and the request represents durable work.
**Rationale:** The spec says "Do not create tasks for ordinary conversational answers."

---

## 12. ARCHITECTURAL CONTRADICTIONS FOUND

### Contradiction 1: AI Manager Not Called From Chat
**Finding:** The AI Manager (`AIManager.decide()`) is not currently called from `AIService.chat()`. The chat flow goes directly to the agent loop.
**Resolution:** This is intentional — the AI Manager is for structured decision-making, not conversational chat. Stage 20 will integrate it as an optional path for requests that need planning/execution.

### Contradiction 2: OperationType Limited to 4 Values
**Finding:** `OperationType` has only `DECISION`, `PLANNING`, `CODING`, `CHAT`. This is too narrow for the proposed pipeline.
**Resolution:** Extend carefully with `INFORMATION_REQUEST`, `FILE_READ`, `FILE_WRITE`, `FILE_DELETE`, `DEVELOPMENT`, `TASK_CREATE`, `TASK_UPDATE`, `PLAN_CREATE`, `PLAN_EXECUTE`, `SCHEDULE_CREATE`, `MEMORY_QUERY`, `MEMORY_CREATE`, `OTHER`.

### Contradiction 3: No Structured Response Model
**Finding:** `AIService.chat()` returns a `dict` with optional fields. The frontend must parse free-form keys.
**Resolution:** Add a `ChatResponseState` enum and structured response model while preserving backward compatibility.

---

## 13. FILES TO CREATE/MODIFY

### 13.1 New Files (Phase 1-11)
```
services/core/app/operator_request/
    __init__.py
    models.py               # OperatorRequest, OperatorRequestState, OperatorRequestError
    normalizer.py            # RequestNormalizer
    classifier.py            # OperationClassifier
    service.py               # OperatorRequestService
    errors.py                # OperatorRequestError hierarchy
    tests/
        test_models.py
        test_normalizer.py
        test_classifier.py
        test_service.py
        test_hostile_audit.py
```

### 13.2 Modified Files
- `services/core/app/context_assembly/models.py` — extend `OperationType`
- `services/core/app/events/schemas.py` — add operator request event types
- `services/core/app/ai/services/ai_service.py` — integrate `OperatorRequest` flow
- `services/core/app/api/routes/ai.py` — add structured response states
- `services/core/app/dependencies.py` — add `OperatorRequestService` factory

### 13.3 Documentation
- `STAGE_20_1_OPERATOR_PIPELINE_DISCOVERY.md` (this file)
- `STAGE_20_UNIFIED_OPERATOR_REQUEST_PIPELINE.md` (final architecture doc)
- `STAGE_20_FINAL_ACCEPTANCE_REPORT.md` (final report)

---

## 14. KNOWN LIMITATIONS

1. **AI Manager integration is opt-in**: Not all chat requests will go through AI Manager. Only requests that need planning/execution will.
2. **OperationType extension is partial**: The proposed extended enum covers common cases but may need further extension.
3. **Event emission is additive**: Existing events are preserved; new events are added.
4. **Clarification loop is preserved**: The existing clarification logic is unified with `OperatorRequest` but not replaced.
5. **Task creation is conditional**: Tasks are only created for durable work, not for conversational answers.

---

## 15. DEFERRED WORK

- Model Router
- Computer Use
- Browser automation
- External apps
- Email integration
- Calendar integration
- Mobile
- Embeddings
- Vector database
- Agent teams
- Autonomous background behavior
- Notification delivery
- Rich frontend redesign

---

*End of discovery document.*
