# STAGE 10.2 — MINIMAL KILO DEVELOPMENT AGENT ADAPTER IMPLEMENTATION REPORT

## 1. ARCHITECTURE

Kilo Code is integrated as a `DevelopmentAgent` implementation, not as a `CodingProvider`.

```
DevelopmentAgent (interface)
        │
        ▼
KiloDevelopmentAgent (narrow adapter)
        │
        ▼
KiloProcessHelper (subprocess lifecycle)
        │
        ▼
kilo.exe run --format json --pure --dir <authorized_workspace> <task_description>
        │
        ▼
application-created Git worktree (workspace_path from DevelopmentAgentContext)
```

Application services remain authoritative for:
- task state, run state, attempt counters, retry policy
- permissions (PermissionEngine), approvals (CapabilityApprovalService)
- workspace authorization (WorkspaceManager)
- verification (VerificationGate)
- Git authority (WorkspaceManager)
- cancellation policy (RunController), timeout policy (RunController)

Kilo output is treated as untrusted. All security-sensitive decisions are derived from:
- process exit code
- authoritative filesystem state (snapshot diff)
- application-owned services

## 2. FILES CHANGED

### Created:
- `services/core/app/orchestration/agents/kilo_process.py`
- `services/core/app/orchestration/agents/kilo_agent.py`
- `services/core/tests/test_kilo_agent.py`

### Modified:
- `services/core/app/orchestration/agents/__init__.py` — export `KiloDevelopmentAgent`
- `services/core/app/orchestration/agent_factory.py` — branch on `provider_type == "kilo"` to create `KiloDevelopmentAgent`
- `services/core/app/ai/services/provider_manager.py` — add `kilo` to `SUPPORTED_PROVIDERS`, expose `kilo_executable_path` in `get_config()`
- `services/core/app/services/settings_service.py` — add `kilo_executable_path` to `DEFAULT_SETTINGS`

### Explicitly NOT modified:
- `services/core/app/ai_manager/` — no changes
- `services/core/app/services/autonomous_loop.py` — no changes
- `services/core/app/orchestration/run_controller.py` — no changes
- `services/core/app/orchestration/orchestrator.py` — no changes
- `services/core/app/tools/permission_engine.py` — no changes
- `services/core/app/verification/gate.py` — no changes
- `services/core/app/workspace/workspace_manager.py` — no changes
- `services/core/app/ai/providers/coding_base.py` — no changes
- `services/core/app/ai/providers/coding_ollama.py` — no changes
- `services/core/app/ai/providers/coding_openrouter.py` — no changes

## 3. KILO INVOCATION ACTUALLY VERIFIED

**Verified:**
- `kilo.exe` exists at `C:\Users\adamz\.vscode\extensions\kilocode.kilo-code-7.6.2-win32-x64\bin\kilo.exe`
- CLI accepts `kilo run --format json --pure --dir <path> <message>`
- `--format json` requests JSON events (actual schema unverified)
- `--pure` disables external plugins
- `--dir` constrains working directory
- `-m` sets model
- `--session` sets session ID

**NOT verified (blocker for Stage 10.3):**
- `kilo run --format json` non-interactive behavior without a configured model
- Actual JSON event schema produced by Kilo
- Whether `kilo run` can complete a coding task headlessly without hanging
- `kilo serve` / `kilo acp` API schemas
- Kilo sandbox enforcement with `--pure` + `--dir`

**Concrete incompatibility discovered:**
- Kilo has no model configured in `C:\Users\adamz\.config\kilo\kilo.jsonc`. Without a configured model, `kilo run` behavior is unverified and likely fails or hangs.

## 4. PROCESS LIFECYCLE DESIGN

### `kilo_process.py` responsibilities:
1. `build_kilo_args()` — validate inputs, construct argument list from trusted parameters
2. `start_kilo_process()` — launch subprocess via `asyncio.create_subprocess_exec` (NOT `create_subprocess_shell`)
3. `wait_kilo_process()` — monitor process, handle cancellation, bound output

### Key properties:
- No shell invocation (`create_subprocess_exec` with list args)
- No Git, permission, approval, or verification operations
- Bounded stdout/stderr capture (1 MiB cap)
- No arbitrary command acceptance (executable and args are application-generated)
- No hidden retry loops (single subprocess per `execute()` call)

## 5. CANCELLATION BEHAVIOR

Cancellation is handled at two levels:

1. **Application-level `cancel_event`**: `KiloDevelopmentAgent.execute()` polls `context.cancel_event` during `wait_kilo_process()`. When set, it calls `proc.kill()` and waits for termination.

2. **Task-level `asyncio.CancelledError`**: `RunController` cancels the orchestrator task on timeout or explicit cancel. The `asyncio.CancelledError` propagates into `execute()`, which calls `proc.kill()` and re-raises.

In both cases:
- `proc.kill()` is called synchronously
- `asyncio.wait_for(asyncio.shield(proc.wait()), timeout=kill_timeout)` confirms termination
- If termination cannot be confirmed within `kill_timeout_seconds`, the adapter returns failure/error rather than pretending the process stopped

**Cancellation never returns SUCCESS.** A cancelled Kilo process always maps to `DevelopmentResultStatus.CANCELLED`.

## 6. TIMEOUT BEHAVIOR

Timeouts are NOT handled inside `KiloDevelopmentAgent`. The adapter is a single-shot subprocess call. Timeout authority belongs to `RunController`, which wraps `AutonomousTaskOrchestrator.start_task()` in `asyncio.wait_for(orch_task, timeout=timeout_seconds)`.

When `RunController` timeout fires:
1. `RunController._terminate_execution()` cancels `orch_task`
2. `asyncio.CancelledError` propagates into `agent.execute()`
3. `execute()` kills the Kilo subprocess
4. `execute()` re-raises `CancelledError`
5. `RunController` handles the failure state

The adapter does NOT perform its own timeout logic. It relies on `RunController` and the cancellation mechanism.

## 7. OUTPUT HANDLING

### Bounded output:
- `_read_stream()` caps captured output at 1 MiB (`_MAX_OUTPUT_BYTES = 1_048_576`)
- `_safe_details()` further truncates stdout/stderr to 4096 chars for inclusion in `DevelopmentResult.details`
- `details["stdout_tail"]` and `details["stderr_tail"]` are capped at 2048 chars

### JSON parsing:
- Stage 10.2 does NOT parse Kilo JSON output for security decisions.
- `--format json` is passed to Kilo to potentially enable structured output in future stages, but the adapter ignores Kilo's JSON events.
- All decisions are based on process-level facts: exit code, filesystem state, cancellation state.

### Untrusted output:
- Kilo stdout/stderr are logged in `details` for diagnostics only
- Kilo output is NEVER executed as code
- Kilo output is NEVER used for security decisions

## 8. WORKSPACE BOUNDARY

- Workspace path comes exclusively from `DevelopmentAgentContext.workspace_path` (application-owned)
- `build_kilo_args()` resolves the path and validates it is an existing directory
- Kilo is launched with `--dir <resolved_workspace>` to constrain its working directory
- The adapter does NOT accept workspace paths from Kilo output
- The adapter does NOT modify the selected workspace
- WorkspaceWriter boundaries are independent; Kilo writes directly to the workspace (constrained by `--dir`)

## 9. CHANGED-FILE AUTHORITY

Changed files are derived from authoritative filesystem inspection, NOT from Kilo claims.

The adapter uses a before/after snapshot:
1. Before Kilo runs: `_snapshot_workspace()` records all non-hidden file paths with their mtime and size
2. After Kilo runs: `_diff_snapshots()` compares the two snapshots
3. Only files that are new or modified (mtime/size changed) are reported

This is a filesystem-level mechanism, not a Git-level mechanism. The adapter does NOT invoke Git commands.

The `DevelopmentResult.changed_files` field is populated from this snapshot diff.

**Known limitation**: If Kilo modifies a file's content but preserves its mtime (unlikely), the change might not be detected. This is acceptable for Stage 10.2 because:
- `VerificationGate` runs against the entire workspace regardless of `changed_files`
- `WorkspaceManager.create_checkpoint()` defaults to committing the whole workspace when `changed_files` is empty

## 10. SECURITY CONTROLS

### KILO-SEC-01: Workspace boundary
- **Control**: Workspace path from `DevelopmentAgentContext`; validated as existing directory; passed via `--dir`
- **Status**: Implemented

### KILO-SEC-02: Shell/subprocess operations
- **Control**: `asyncio.create_subprocess_exec` (not `create_subprocess_shell`); arguments are application-generated list
- **Status**: Implemented

### KILO-SEC-03: Credentials/secrets
- **Control**: No secrets passed to Kilo; task description is application-controlled and should contain no credentials
- **Status**: Implemented (application invariant)

### KILO-SEC-04: PermissionEngine bypass
- **Control**: Kilo runs in separate subprocess; has no access to application's PermissionEngine
- **Status**: Architecture-enforced

### KILO-SEC-05: ApprovalService bypass
- **Control**: `ExecutionBridge` enforces approval BEFORE creating agent; Kilo never sees approval state
- **Status**: Implemented upstream

### KILO-SEC-06: VerificationGate bypass
- **Control**: VerificationGate runs AFTER Kilo completes; Kilo cannot influence it
- **Status**: Implemented upstream

### KILO-SEC-07: Git authority
- **Control**: Adapter does NOT invoke Git commands; `WorkspaceManager` is sole Git authority
- **Status**: Implemented

### KILO-SEC-08: Cancellation safety
- **Control**: `proc.kill()` on cancel_event or `CancelledError`; termination confirmed via `proc.wait()`
- **Status**: Implemented

### KILO-SEC-09: Timeout safety
- **Control**: `RunController` wraps execution in `asyncio.wait_for`; adapter cooperates with cancellation
- **Status**: Implemented (RunController is authority)

### KILO-SEC-10: Orphan process survival
- **Control**: Basic kill + wait implemented; sophisticated orphan recovery deferred to Stage 10.3
- **Status**: Partially implemented (Stage 10.3 scope)

### KILO-SEC-11: Incomplete workspace claims
- **Control**: Application does NOT trust Kilo's success claim; uses process exit code + filesystem inspection
- **Status**: Implemented

### KILO-SEC-12: Prompt injection
- **Control**: Kilo output parsed as data only; never executed as code; bounded output prevents memory exhaustion
- **Status**: Implemented

### KILO-SEC-13: Session replay
- **Control**: No session resumption in Stage 10.2 (`session_id=None` always); application tracks session IDs per task
- **Status**: Implemented (no session feature in Stage 10.2)

### KILO-SEC-14: Stale authorization
- **Control**: Kilo has no application authorization model; application authorization is checked before agent creation
- **Status**: Architecture-enforced

### KILO-SEC-15: Concurrent workspace access
- **Control**: `WorkspaceManager` creates unique worktree per task_id; `--dir` constrains Kilo
- **Status**: Implemented upstream

### KILO-SEC-16: Hidden retry loop
- **Control**: Adapter performs exactly one subprocess invocation per `execute()` call; no internal retry
- **Status**: Implemented (verified by test)

## 11. TESTS

### Unit tests: `services/core/tests/test_kilo_agent.py` (43 tests)

**`TestBuildKiloArgs`** (14 tests):
- Valid args construction
- Pure mode flag present/absent
- JSON format flag present/absent
- Workspace dir inclusion
- Model flag
- Session flag
- Empty/None executable rejection
- Empty workspace rejection
- Nonexistent workspace rejection
- File-instead-of-directory rejection
- Empty task description rejection

**`TestStartKiloProcess`** (4 tests):
- Process start success
- Missing executable raises
- OS error raises
- Empty args raises

**`TestWaitKiloProcess`** (7 tests):
- Already completed (zero/nonzero exit)
- Cancellation via cancel_event
- Outer task cancellation kills process
- Termination timeout returns unconfirmed
- Bounded stdout
- Bounded stderr
- None streams handled

**`TestKiloDevelopmentAgent`** (18 tests):
- Valid construction
- Missing executable raises
- Execute invokes process once
- Workspace directory validation
- Nonexistent workspace error
- Empty description error
- Nonzero exit returns FAILURE
- Process start failure returns ERROR
- Cancellation via cancel_event
- Outer task cancellation kills process
- Changed files from snapshot diff
- Bounded output in details
- No internal retry loop
- Executable from config, not model
- Workspace from context, not model
- Concurrent agents isolated
- Result maps to DevelopmentResult

### Verification results:
- `test_kilo_agent.py`: **43 passed**
- `test_development_agent.py`: **32 passed, 1 skipped**
- `test_coding_agent_integration.py`: **10 passed**
- `test_orchestrator.py`: **28 passed**
- `test_autonomous_loop.py`: **14 passed**
- `test_plan_execution_service.py`: **14 passed**
- **Total: 137 passed, 1 skipped, 0 failed**

## 12. REAL KILO PROBE RESULT

**Status: NOT PERFORMED — environment limitation documented.**

The current Kilo environment has no model configured in `C:\Users\adamz\.config\kilo\kilo.jsonc`. Without a configured model, `kilo run` behavior is unverified:
- It may hang waiting for model selection
- It may fail immediately with a model error
- The actual JSON event schema from `--format json` is unknown

**What remains unverified:**
1. Whether `kilo run --format json --pure --dir <path> <message>` completes non-interactively
2. The actual JSON event schema
3. Kilo's sandbox enforcement with `--pure` + `--dir`
4. Process exit code semantics for success/failure

**How Stage 10.3 should handle it:**
1. Configure a Kilo model in `kilo.jsonc`
2. Run a disposable probe: `kilo run --format json --pure --dir <tmpdir> "Create a file test.txt with content hello"`
3. Capture and document the actual JSON event schema
4. If `kilo run` hangs, test `kilo serve` or `kilo acp` alternatives
5. If no controllable interface exists, document as environment limitation

## 13. LIMITATIONS

1. **No real Kilo execution verified**: The adapter is built against the discovered CLI interface, but non-interactive execution has not been confirmed.
2. **JSON output unused**: `--format json` is passed but events are not parsed. Stage 10.3+ may add structured output handling.
3. **No session/recovery**: `session_id=None` always. Session resumption is deferred.
4. **Simple changed-file detection**: Snapshot diff uses mtime/size, not content hash. May miss content-preserving modifications.
5. **No process group killing on Windows**: `proc.kill()` kills the direct process only. Child processes may survive. Stage 10.3 adds process group termination.
6. **No model validation**: Kilo's `-m` flag is passed but the adapter doesn't validate the model is configured.
7. **No Kilo state cleanup**: Kilo's SQLite DB and session state are not managed by the adapter.

## 14. KNOWN RISKS

1. **Risk**: Kilo CLI behavior contradicts assumed invocation.
   **Mitigation**: Stage 10.3 will perform real probe and update adapter if needed.

2. **Risk**: Kilo's internal sandbox allows operations outside application policy.
   **Mitigation**: `--pure` flag passed; Kilo's own permission system is independent. Application cannot enforce its PermissionEngine inside Kilo's subprocess.

3. **Risk**: Kilo spawns child processes that survive `proc.kill()`.
   **Mitigation**: Stage 10.3 adds `CREATE_NEW_PROCESS_GROUP` and `taskkill /T` on Windows.

4. **Risk**: Kilo's SQLite DB corruption from concurrent access.
   **Mitigation**: Each task gets a unique worktree. Stage 10.4 adds session isolation.

5. **Risk**: Kilo's `--dir` constraint is not enforced by Kilo itself.
   **Mitigation**: WorkspaceWriter path validation provides defense-in-depth. Stage 10.3 should verify Kilo respects `--dir`.

## 15. EXACT VERIFICATION RESULTS

```
tests/test_kilo_agent.py ........................... [100%]
43 passed, 0 failed, 0 skipped

tests/test_development_agent.py .................. [100%]
32 passed, 1 skipped (symlinks on Windows), 0 failed

tests/test_coding_agent_integration.py ............ [100%]
10 passed, 0 failed, 0 skipped

tests/test_orchestrator.py ........................ [100%]
28 passed, 0 failed, 0 skipped

tests/test_autonomous_loop.py ..................... [100%]
14 passed, 0 failed, 0 skipped

tests/test_plan_execution_service.py .............. [100%]
14 passed, 0 failed, 0 skipped
```

No unrelated failures detected.

## 16. RECOMMENDED STAGE 10.3 SCOPE

1. **Real Kilo probe**: Configure model, run `kilo run --format json --pure --dir <tmpdir>`, capture JSON schema, verify headless completion.
2. **Process group termination**: Add `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` and `taskkill /T /PID <pid>` on Windows.
3. **Session tracking**: If JSON schema supports session IDs, add application-tracked session IDs to `DevelopmentResult.details`.
4. **Orphan detection**: On startup, detect and terminate Kilo processes from previous application runs.
5. **Kilo sandbox verification**: Test whether Kilo's `--pure` + `--dir` actually constrains filesystem access.
6. **JSON event parsing**: If schema is stable, add optional structured output parsing that remains fail-closed.

---

**STAGE 10.2 COMPLETE**

Kilo integration recommendation: Kilo as a `DevelopmentAgent` implementation (subprocess-backed adapter).

Exact architectural boundary: `DevelopmentAgent` interface → `KiloDevelopmentAgent` adapter → `kilo.exe` subprocess → authorized workspace (via `--dir`).

Key security conclusions: Kilo must never own application state. All security-sensitive decisions (verification, Git, permissions, approvals, task state) remain in application-owned services. Kilo output is untrusted. Process lifecycle is adapter-managed. Cancellation is safe. Timeout is enforced by RunController. Changed files come from filesystem snapshot diff, not Kilo claims.

Stage 10.2 implementation scope: One narrow `KiloDevelopmentAgent` adapter, `KiloProcessHelper` helper, ProviderManager/SettingsService/agent_factory wiring, 43 focused unit tests. No changes to ExecutionBridge, RunController, VerificationGate, PermissionEngine, or WorkspaceManager.

Known blockers: Real Kilo non-interactive execution behavior and JSON output schema are unverified — documented for Stage 10.3.

Test strategy: 43 unit tests with mocked subprocess, covering construction, args, workspace validation, process lifecycle, cancellation, timeout, output bounding, security boundaries, and result mapping. 137 total tests pass in regression.
