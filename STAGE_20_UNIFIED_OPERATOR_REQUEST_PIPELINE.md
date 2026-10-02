# STAGE_20_UNIFIED_OPERATOR_REQUEST_PIPELINE.md

## AI Operator — Unified Operator Request Pipeline

---

## 1. Architecture

The Unified Operator Request Pipeline connects the existing AI Operator subsystems into one
authoritative application-owned flow:

```
USER MESSAGE
    │
    ▼
AUTHENTICATION / RequestContext
    │
    ▼
OPERATOR REQUEST CREATION
    │   - request_id (generated)
    │   - conversation_id
    │   - message_id
    │   - request_context (from auth)
    │   - user_request (raw text)
    │   - correlation_id
    │   - causation_id
    │
    ▼
REQUEST NORMALIZATION
    │   - deterministic field extraction
    │   - preserve original text
    │   - bound all externally-influenced fields
    │   - reject malformed/oversized values
    │
    ▼
OPERATION / INTENT CLASSIFICATION
    │   - deterministic first
    │   - AI-assisted when needed
    │   - bounded by existing OperationType
    │   - fail-closed when classification is required
    │
    ▼
CONTEXT ASSEMBLY
    │   - ContextAssemblyService.assemble()
    │   - Memory retrieval
    │
    ▼
INFORMATION SUFFICIENCY
    │   - InformationSufficiencyService.assess()
    │   - fail-closed: only SUFFICIENT continues
    │
    ▼
CLARIFICATION LOOP (if needed)
    │   - ClarificationService.create() / respond()
    │   - preserve correlation
    │   - re-run Context Assembly + Sufficiency on answer
    │   - only continue when SUFFICIENT
    │
    ▼
AI MANAGER DECISION (optional)
    │   - UserIntent (application-controlled)
    │   - AIManager.decide()
    │   - ManagerResult (application-owned)
    │
    ▼
DECISION ROUTING
    │
    ├── ANSWERED ──────────────────────────────────────────► RESPONSE
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

## 2. Request Lifecycle

### 2.1 States

| State | Description |
|-------|-------------|
| RECEIVED | Request created from user input |
| NORMALIZED | Request normalized |
| CLASSIFIED | Operation type classified |
| READY | Ready for processing |
| CLARIFICATION_REQUIRED | Awaiting user clarification |
| BLOCKED | Blocked by policy/permission |
| REJECTED | Rejected by application policy |
| TASK_CREATED | Task created for durable work |
| PLAN_CREATED | Plan created |
| APPROVAL_REQUIRED | Awaiting user approval |
| EXECUTION_STARTED | Execution in progress |
| EXECUTION_COMPLETED | Execution completed |
| EXECUTION_FAILED | Execution failed |
| COMPLETED | Request fully completed |
| FAILED | Request failed |

### 2.2 Response States

| State | Description |
|-------|-------------|
| ANSWERED | Conversational answer provided |
| CLARIFICATION_REQUIRED | Clarification needed |
| TASK_CREATED | Task created for durable work |
| PLAN_CREATED | Plan created |
| APPROVAL_REQUIRED | Approval needed |
| EXECUTION_STARTED | Execution started |
| EXECUTION_COMPLETED | Execution completed |
| EXECUTION_FAILED | Execution failed |
| BLOCKED | Blocked by policy |
| REJECTED | Rejected |

---

## 3. Contracts

### 3.1 OperatorRequest

**File:** `services/core/app/operator_request/models.py`

The canonical representation of what the application currently understands about a user request.

**Key fields:**
- `request_id` — Unique request identifier (application-generated)
- `conversation_id` — Conversation identifier
- `message_id` — Message identifier
- `request_context` — Authenticated request context (application-controlled)
- `user_request` — Raw user text (preserved as-is)
- `normalized_request` — Normalized text
- `operation_type` — Classified operation type
- `interaction_mode` — conversational, task, planning, execution, clarification
- `project_id` — Project identifier (application-controlled)
- `task_id` — Task identifier (application-controlled)
- `assembled_context` — Context Assembly result
- `readiness_state` — SUFFICIENT, INSUFFICIENT, AMBIGUOUS, UNKNOWN
- `clarification_session_id` — Active clarification session
- `intent` — Classified intent
- `classification_confidence` — Confidence score
- `constraints` — Request constraints
- `assumptions` — Documented assumptions
- `requested_capabilities` — Requested capabilities
- `correlation_id` — Correlation identifier
- `causation_id` — Causation identifier
- `state` — Current lifecycle state

### 3.2 OperatorRequestCreate

Application-controlled input to create an OperatorRequest.

**Key fields:**
- `user_request` — Raw user text (required)
- `request_context` — Authenticated request context (required)
- `conversation_id` — Optional conversation ID
- `project_id` — Optional project ID
- `task_id` — Optional task ID
- `correlation_id` — Optional correlation ID
- `causation_id` — Optional causation ID
- `constraints` — Optional constraints
- `metadata` — Optional metadata

### 3.3 OperatorRequestUpdate

Application-controlled update to an OperatorRequest.

---

## 4. Authority Boundaries

### 4.1 Application Remains Authoritative Over

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

### 4.2 AI Models May

- interpret user intent
- classify operations
- propose tasks
- propose plans
- generate structured decisions
- explain results

### 4.3 AI Models Must NOT

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

## 5. Clarification Integration

Clarification is unified with the Operator Request lifecycle:

1. When Information Sufficiency returns INSUFFICIENT or AMBIGUOUS:
   - `OperatorRequestService.mark_clarification_required()` is called
   - Clarification session is created with correlation preserved
   - `operator.request.clarification_required` event is emitted
   - Structured clarification response is returned to frontend

2. When user answers:
   - Clarification session is validated
   - Context Assembly is re-run
   - Information Sufficiency is re-evaluated
   - Only when SUFFICIENT does the request continue

3. Clarification answers are **untrusted data**:
   - They do NOT automatically make a task sufficient
   - They do NOT grant permissions
   - They are re-evaluated by Information Sufficiency

---

## 6. AI Manager Integration

The AI Manager is integrated as an optional enhancement for structured requests:

1. **When**: After clarification/sufficiency check passes, for operation types
   that benefit from structured decision-making (DEVELOPMENT, TASK_CREATE, PLAN_CREATE, etc.)

2. **How**: `AIService._run_ai_manager_decision()` builds a `UserIntent` from the
   `OperatorRequest`, calls `AIManager.decide()`, and returns the `ManagerResult`.

3. **Outcomes**:
   - `ANSWERED` — Direct answer provided
   - `NEEDS_CLARIFICATION` — Create clarification
   - `REJECTED` — Return rejection
   - `PROPOSED` — Create task
   - `READY_FOR_PLANNING` — Create plan

4. **Security**:
   - AI Manager decision ≠ authorization
   - AI Manager decision ≠ approval
   - AI Manager decision ≠ execution
   - Application validates every model decision against the actual request

---

## 7. Task/Plan Integration

### 7.1 Task Creation

When AI Manager returns `PROPOSED` and the request represents durable work:

1. Task is created through the existing `TaskRepository.create_task()`
2. Task preserves:
   - authenticated user
   - request_id
   - conversation_id
   - project_id
   - correlation_id
   - parent task when applicable
   - original user request
   - normalized request
   - operation type
   - constraints
   - status

### 7.2 Plan Creation

When AI Manager returns `READY_FOR_PLANNING`:

1. `ManagerPlanningService.plan_decision()` converts the decision to a `UserPlan`
2. `PlanManager.plan()` creates the plan
3. Execution proceeds through `ExecutionBridge`

### 7.3 Execution Handoff

The `ExecutionBridge` is the ONLY authorized path from AI Manager decisions to execution:
1. Re-evaluates execution authorization via `PolicyCapabilityGate`
2. Creates approval requests via `CapabilityApprovalService` when needed
3. Plans via `ManagerPlanningService`
4. Executes via `AutonomousPlanExecutor`

---

## 8. Execution Boundary

The following invariant is mandatory:

**UNKNOWN READINESS ≠ READY**

Only `SUFFICIENT` may continue toward execution.

These must block execution:
- INSUFFICIENT
- AMBIGUOUS
- service failure
- persistence failure
- context assembly failure
- classification failure when classification is required
- missing required task information

---

## 9. Event Model

### 9.1 New Event Types

| Event Type | Description |
|------------|-------------|
| `operator.request.received` | Request received |
| `operator.request.normalized` | Request normalized |
| `operator.request.classified` | Request classified |
| `operator.request.clarification_required` | Clarification required |
| `operator.request.ready` | Request ready |
| `operator.request.rejected` | Request rejected |
| `operator.request.blocked` | Request blocked |

### 9.2 Event Payload

Events contain:
- `correlation_id`
- `causation_id` where known
- `request_id`
- `task_id`/`plan_id`/`run_id` where known
- bounded metadata
- no secrets
- no full raw prompts
- no unbounded user content

---

## 10. Error Model

### 10.1 Error Taxonomy

| Error | Description |
|-------|-------------|
| `InvalidOperatorRequestError` | Input validation failed |
| `OversizedRequestError` | Request exceeds size limits |
| `MalformedRequestError` | Request structure is malformed |
| `ClassificationFailedError` | Operation classification failed |
| `ReadinessCheckFailedError` | Readiness check failed |
| `NormalizationFailedError` | Request normalization failed |
| `OperatorServiceUnavailableError` | Required subsystem unavailable |

### 10.2 Error Handling Principles

- Do not leak internal exception details to the user
- Do not convert infrastructure failures into successful execution
- Do not convert unknown state into success
- Do not silently retry dangerous operations

---

## 11. Security Guarantees

1. **Identity**: Client cannot spoof user_id, actor_type, or session_id
2. **Request**: Oversized/malformed requests are rejected
3. **Classification**: Unknown classification remains UNKNOWN
4. **Readiness**: INSUFFICIENT/AMBIGUOUS cannot execute
5. **Authorization**: AI Manager cannot grant permissions
6. **Execution**: No direct model-to-tool execution path
7. **Task integrity**: Cross-user/cross-project access rejected
8. **Prompt injection**: All user content is untrusted data

---

## 12. Idempotency

- Each request gets a unique `request_id`
- Each correlation gets a unique `correlation_id`
- Task creation uses fingerprint-based idempotency
- Event emission is idempotent by `event_id`

---

## 13. Concurrency Assumptions

- OperatorRequest is immutable (frozen)
- State transitions are explicit
- Event emission is fire-and-forget
- Clarification sessions are bound to conversation/session

---

## 14. Known Limitations

1. AI Manager integration is opt-in (only for structured requests)
2. OperationType extension covers common cases but may need further extension
3. Event emission is additive
4. Clarification loop is preserved, not replaced
5. Task creation is conditional (only for durable work)

---

## 15. Deferred Work

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

## 16. Migration Notes

1. Existing `/api/ai/chat` endpoint behavior preserved
2. Existing `AIService.chat()` signature preserved
3. Existing subsystems remain importable and usable
4. No breaking changes to existing API contracts
5. OperatorRequestService is injected as optional dependency

---

*End of architecture document.*
