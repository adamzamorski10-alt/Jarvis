# STAGE 8.14 — REST API & TASK EXECUTION ENDPOINTS

REASONING LEVEL: HIGH

We are continuing the AI Operator project from the verified Stage 8.13 checkpoint.

## VERIFIED BASELINE

Stage 8.9:
* CLOSED
* commit: `693cdbcee110fccce3ea2f5e05212cbb5724ec0f`

Stage 8.10:
* CLOSED
* REAL LOCAL OLLAMA E2E PASS WITH LIMITATIONS
* real `qwen2.5-coder:7b` completed a real coding task

Stage 8.11:
* CLOSED
* commit: `3e00a4e`
* controlled execution capability exists
* `dev.run_test` exists
* DevelopmentAgent can request controlled tools
* PermissionEngine remains the authority
* ToolExecutionService performs controlled execution
* Audit records decisions/execution
* DevelopmentAgent/provider layer has no direct subprocess authority

Stage 8.12:
* CLOSED
* commit: `26913ca`
* Manager/PlanManager/PlanService integration complete
* AutonomousRunController wired to PlanExecutionService
* TaskRepository atomic CAS mechanics verified

Stage 8.13:
* CLOSED
* commit: `ab6f2ff`
* 995 passed, 2 skipped, 0 failed (full regression)
* Bounded autonomous repair loop implemented
* REPAIR_REQUIRED to RUNNING atomic transition
* Structured VerificationReport diagnostics injected into DevelopmentAgentContext
* format_verification_feedback() in repair_diagnostics.py (max 4096 chars)
* max_attempts / attempt_count immutable by model output
* Git checkpoint only on VerificationGate PASS verdict
* Tool calls during repair governed by PermissionEngine
* 6 repair tests (REPAIR-01..30) passing
* test_repair_ollama_e2e.py present (skipped if Ollama offline)

---

## CURRENT ARCHITECTURAL TARGET

```
USER REQUEST
    |
REST API (FastAPI)
    |
PlanExecutionService / PlanService
    |
AutonomousRunController
    |
AutonomousTaskOrchestrator
    |
DevelopmentAgent (repair loop included)
    |
VerificationGate
    |
Git checkpoint (only on PASS)
```

The current system has a solid backend core. What is missing is:
1. A REST API surface to trigger task execution, query task status, cancel runs, and retrieve results from external callers (e.g. the frontend, CLI, or future remote clients).
2. Proper FastAPI routing wiring PlanExecutionService and AutonomousRunController to HTTP endpoints.
3. Background task management — allowing /runs/{task_id}/start to fire off execution as a background asyncio task and return immediately with a run_id.
4. Status polling endpoints — GET /tasks/{task_id} and GET /runs/{run_id} returning current task/run state.
5. Cancellation endpoint — POST /runs/{task_id}/cancel wiring to AutonomousRunController.cancel_run().

---

## SCOPE OF STAGE 8.14

### What to implement (STRICTLY within this stage):

1. Task Execution REST Endpoints in the existing FastAPI app (services/core/app/main.py or services/core/app/routers/):
   - POST /api/tasks/{task_id}/execute — start a run in the background, return {"run_id": "...", "status": "STARTED"} immediately
   - GET /api/tasks/{task_id} — return current DevelopmentTask state (status, attempt_count, etc.)
   - POST /api/tasks/{task_id}/cancel — cancel a running task via AutonomousRunController.cancel_run()
   - GET /api/runs/{run_id}/result — return the AutonomousRunResult if execution is complete, or {"status": "RUNNING"} if still in progress

2. Background Run Manager (in-process asyncio task registry):
   - Track active asyncio.Task objects by run_id
   - Store completed AutonomousRunResult objects in memory (for polling)
   - Clean up completed tasks from registry

3. Dependency wiring — connect PlanExecutionService and AutonomousRunController into FastAPI dependency injection (via app.state or a DI container)

4. Tests for all new endpoints (using FastAPI TestClient or httpx):
   - test_api_task_execute.py or similar
   - Must cover: successful start, duplicate start rejection, cancel, status polling, result retrieval

### What NOT to implement in this stage:
- Authentication / authorization (future stage)
- WebSocket streaming (future stage)
- Frontend changes (future stage)
- Persistent run history in DB (can be in-memory for now)
- Plan creation via API (optional for now)

---

## EXISTING CODE TO UNDERSTAND FIRST

Before implementing, read and understand:
- services/core/app/main.py — existing FastAPI app setup
- services/core/app/routers/ — existing router structure (if any)
- services/core/app/services/plan_execution_service.py — execute_planned_task()
- services/core/app/orchestration/run_controller.py — start_run(), cancel_run()
- services/core/app/orchestration/models.py — AutonomousRunResult, RunStatus
- services/core/app/tasks/models.py — DevelopmentTask, TaskStatus
- services/core/app/repositories/task_repository.py — get_task()
- services/core/app/db/session.py — AsyncSession setup

---

## ARCHITECTURE CONSTRAINTS (NON-NEGOTIABLE)

1. PermissionEngine remains the authority for all tool calls — REST endpoints do NOT bypass it.
2. TaskStateMachine remains the authority for all task state transitions — REST endpoints do NOT directly modify task state.
3. AutonomousRunController is the ONLY entry point for starting/cancelling runs.
4. Git checkpoints occur ONLY after VerificationGate PASS — the API does NOT commit.
5. Background tasks must be properly cancelled on application shutdown (lifespan events).
6. Run IDs are generated by the run controller, not by the API.
7. Duplicate start protection: if a task is already RUNNING, return HTTP 409 Conflict.

---

## SUCCESS CRITERIA

1. All new endpoints return correct HTTP status codes and JSON bodies.
2. POST /api/tasks/{task_id}/execute fires execution as a background asyncio task and returns immediately.
3. GET /api/tasks/{task_id} reflects live DB state from TaskRepository.
4. POST /api/tasks/{task_id}/cancel triggers AutonomousRunController.cancel_run().
5. GET /api/runs/{run_id}/result returns AutonomousRunResult when complete.
6. All new tests pass.
7. Full backend regression: 0 failures (run .venv\Scripts\pytest.exe).
8. Single commit: Stage 8.14: REST API task execution endpoints.

---

## VERIFICATION PLAN

```bash
# New endpoint tests
.venv\Scripts\pytest.exe tests/test_api_task_execute.py -v

# Full regression
.venv\Scripts\pytest.exe
```

Expected: 0 failures, 2 skipped (Ollama e2e tests skipped if offline).

---

## GIT STATE

- HEAD: ab6f2ff (Stage 8.13: autonomous repair loop)
- Branch: main
- Working tree: clean

---

## NOTES

- The existing services/core/app/main.py already has some routes — extend it, do not replace it.
- Use AsyncSession from SQLAlchemy for DB access in endpoints (already wired).
- Background tasks should use asyncio.create_task() within the FastAPI async context.
- Store AutonomousRunResult objects in a simple dict {run_id: result} on app.state — no DB persistence needed for this stage.
- If the existing router structure already has task endpoints partially, refactor/extend them rather than creating parallel routes.
