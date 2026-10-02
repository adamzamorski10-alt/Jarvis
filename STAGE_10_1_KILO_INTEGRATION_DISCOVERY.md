# STAGE 10.1 — KILO INTEGRATION DISCOVERY REPORT

## 1. ACTUAL KILO INTERFACE DISCOVERED

Kilo Code is installed on the development machine as a VS Code extension:
- **Extension**: `kilocode.kilo-code-7.6.2-win32-x64`
- **Extension path**: `C:\Users\adamz\.vscode\extensions\kilocode.kilo-code-7.6.2-win32-x64`
- **CLI binary**: `C:\Users\adamz\.vscode\extensions\kilocode.kilo-code-7.6.2-win32-x64\bin\kilo.exe`
- **Version**: 7.6.2

Kilo exposes a standalone CLI with these programmatically relevant commands:

| Command | Description |
|---------|-------------|
| `kilo run [message..]` | Run Kilo with a message |
| `kilo serve` | Start a headless Kilo server |
| `kilo acp` | Start ACP (Agent Client Protocol) server |
| `kilo session` | Manage sessions |
| `kilo worktree` | Manage git worktrees |
| `kilo export [sessionID]` | Export session data as JSON |
| `kilo import <file>` | Import session data |

### Relevant CLI flags for `kilo run`:
- `--format json` — output raw JSON events
- `--dir <path>` — working directory (defaults to `F:\Projects\AI-Operator`)
- `-s, --session <id>` — continue a specific session
- `-c, --continue` — continue the last session
- `--fork` — fork session before continuing
- `-m, --model <provider/model>` — model selection
- `--agent <name>` — agent selection
- `-f, --file <path>` — attach files to message
- `--pure` — run without external plugins
- `--command <cmd>` — command to run
- `-i, --interactive` — interactive mode (default: false)
- `--auto` — auto-approve permissions (DANGEROUS)

### Relevant CLI flags for `kilo serve`:
- `--port <number>` — port to listen on (default: 0 = random)
- `--hostname <host>` — hostname to listen on (default: 127.0.0.1)
- `--cwd <path>` — working directory

### Relevant CLI flags for `kilo acp`:
- `--port <number>` — port to listen on (default: 0 = random)
- `--hostname <host>` — hostname to listen on (default: 127.0.0.1)
- `--cwd <path>` — working directory

### Kilo state:
- **Database**: `C:\Users\adamz\.local\share\kilo\kilo.db` (SQLite, ~1.5GB)
- **State dir**: `C:\Users\adamz\.local\state\kilo\`
- **Config**: `C:\Users\adamz\.config\kilo\kilo.jsonc`
- **Cache**: `C:\Users\adamz\.cache\kilo\` (contains `models.json`, `rg.exe`)

### Kilo permissions (from `kilo.jsonc`):
```jsonc
{
  "permission": {
    "bash": "allow",
    "external_directory": {
      "D:\\*": "allow",
      "F:\\Projects\\I-Operator\\apps\\desktop\\*": "allow",
      "F:\\Projects\\AI-Operator\\core\\*": "allow",
      "C:\\Users\\adamz\\.cache\\kilo\\*": "allow"
    }
  }
}
```

## 2. AVAILABLE INVOCATION MECHANISM

### Verified mechanisms:

1. **Subprocess invocation** — `kilo.exe` is a standalone native binary that can be invoked from Python using `asyncio.create_subprocess_exec` or `subprocess.Popen`.

2. **Headless server mode** — `kilo serve` starts a background HTTP server that could potentially accept API requests.

3. **ACP server mode** — `kilo acp` starts an Agent Client Protocol server, which is a standardized agent protocol.

4. **Session management** — `kilo session` and `--session`/`--continue`/`--fork` flags allow session resumption.

### NOT verified (requires further investigation):
- The actual JSON event schema produced by `kilo run --format json`
- Whether `kilo run` can complete non-interactively without a configured model
- The HTTP API schema of `kilo serve` or `kilo acp`
- Whether Kilo's `serve`/`acp` modes support structured task input/output
- The exact lifecycle events exposed by Kilo (start, progress, completion, failure)

## 3. ENVIRONMENT PREREQUISITES

- **OS**: Windows (Win32)
- **Python**: 3.14.3
- **Node.js**: v24.15.0
- **Kilo**: VS Code extension v7.6.2, CLI available at known path
- **No Python package for Kilo exists** — Kilo is NOT available as a PyPI package or importable Python module
- **No MCP integration in AI Operator repository** — No MCP server configuration found in `services/core/`
- **Kilo is NOT a library** — it is an interactive agent runtime with its own sandbox, permission system, state management, and terminal UI

## 4. RECOMMENDED INTEGRATION BOUNDARY

### Decision: Option C — External execution backend behind a narrow adapter

Kilo Code is **NOT a `CodingProvider`** and **NOT a simple LLM provider**. It is a full agent runtime that:
- Executes shell commands and terminal operations
- Manages its own sandbox and permissions
- Owns its own session state and conversation history
- Operates via a TUI/CLI interface (not a structured API by default)
- Has its own Git/worktree management (`kilo worktree`)
- Has its own approval/permission system

Trying to fit Kilo into the existing `CodingProvider` interface would require:
1. Inventing a mapping from Kilo's terminal output to `CodingOperation` objects
2. Trusting Kilo's claims about files changed (violates application authority)
3. Bypassing WorkspaceWriter, VerificationGate, and Git authority
4. Giving Kilo shell execution capability within the CodingProvider contract (violates "no subprocess" rule)

### Correct integration: Kilo as a `DevelopmentAgent` implementation

```
DevelopmentAgent (interface)
        │
        ▼
KiloDevelopmentAgent (narrow adapter)
        │
        ▼
Kilo CLI subprocess (kilo run / kilo serve / kilo acp)
        │
        ▼
authorized workspace (--dir parameter)
```

**KiloDevelopmentAgent** implements `DevelopmentAgent.execute(context: DevelopmentAgentContext) -> DevelopmentResult` and:
- Launches Kilo as a managed subprocess
- Passes `--dir <workspace_path>` to constrain Kilo's working directory
- Translates `DevelopmentAgentContext` into Kilo task description
- Parses Kilo's JSON output (if available) or monitors process completion
- Enforces timeout via `asyncio.wait_for`
- Enforces cancellation via process termination (`proc.kill()`)
- **Does NOT trust Kilo's claims** about:
  - Success/failure — uses process exit code + filesystem inspection
  - Changed files — uses authoritative `WorkspaceWriter` / filesystem scan
  - Verification — `VerificationGate` remains authoritative
  - Git commits — `WorkspaceManager` remains authoritative
- Returns `DevelopmentResult` with application-owned data

### Why NOT as a `CodingProvider`?

The `CodingProvider` contract is designed for LLM providers that return structured `CodingOperation` objects (READ, WRITE, EDIT, TOOL_REQUEST, FINISH). Kilo:
- Is a full agent runtime, not a structured code generator
- Can execute shell commands (violates CodingProvider boundary)
- Has its own state management (violates provider neutrality)
- Doesn't naturally map to per-step structured operations
- Would require the adapter to "parse" Kilo's terminal behavior, which is unreliable and insecure

### Why NOT as a second orchestration system?

The existing `AutonomousTaskOrchestrator`, `RunController`, `AutonomousPlanExecutor`, `TaskStateMachine`, and `ExecutionBridge` already own:
- Task state
- Run state
- Permissions
- Approvals
- Workspace authorization
- Verification authority
- Git authority
- Retry policy
- Attempt counters
- Execution identity

Kilo must NOT own any of these. Kilo only performs the code-writing step within the existing lifecycle.

## 5. PROPOSED MINIMAL KILO CONTRACT

### Input (application-owned):
- `task_id` — application-generated
- `project_id` — application-controlled
- `workspace_path` — application-created Git worktree (authoritative)
- `description` — task description from plan
- `attempt_count` — application-tracked
- `expected_base_commit` — application-tracked
- `metadata` — application-owned dict
- `cancel_event` — application-owned `asyncio.Event`
- `abort_event` — application-owned `asyncio.Event`
- `previous_verification_result` — application-owned `VerificationReport`
- `repair_reason` — application-owned string

### Kilo invocation parameters:
- `--dir <workspace_path>` — constrains Kilo to authorized workspace
- `--format json` — requests structured JSON output (if verified to work non-interactively)
- `--pure` — disables external plugins for security
- `--session <id>` — optional session resumption (application tracks session IDs)
- `-m, --model <provider/model>` — application-specified model
- Task description passed as message argument

### Output (application-interpreted):
- `status` — derived from process exit code + workspace state inspection
- `changed_files` — derived from filesystem scan, NOT from Kilo claims
- `summary` — optional, from Kilo's JSON output if available
- `error_message` — from process stderr if failed
- `details` — application-owned dict with Kilo session ID, process info

### Process lifecycle:
- Launch: `asyncio.create_subprocess_exec` with stdout/stderr pipes
- Monitor: read JSON events from stdout, track process state
- Timeout: `asyncio.wait_for` with application-configured timeout
- Cancellation: `proc.kill()` + `proc.wait()` when `cancel_event` is set
- Cleanup: ensure process termination, remove temp files

### Recovery:
- Kilo sessions can be exported/imported via `kilo export/import`
- Application tracks Kilo session IDs in task metadata
- Session resumption via `--session` flag
- If Kilo crashes, application detects via process exit code and triggers retry through existing RunController

## 6. APPLICATION-OWNED vs KILO-OWNED STATE

### APPLICATION-OWNED (never delegated to Kilo):
- Task state (`PENDING`, `RUNNING`, `VERIFYING`, `PASSED`, `COMMITTED`, `FAILED`, `CANCELLED`, `ABORTED`)
- Run state (`RunController`)
- Attempt counters (`TaskStateMachine`)
- Execution identity (`task_id`, `run_id`, `execution_token`)
- Permissions (`PermissionEngine`)
- Approvals (`CapabilityApprovalService`)
- Workspace authorization (`WorkspaceManager` creates worktree)
- Verification authority (`VerificationGate` is sole verifier)
- Git authority (`WorkspaceManager` creates commits/checkpoints)
- Retry policy (`max_attempts` in `DevelopmentTask`)
- Cancellation authority (`RunController.cancel_run`)
- Timeout authority (`RunController.start_run` timeout)
- Changed files list (authoritative filesystem scan)

### KILO-OWNED (treat as untrusted observations):
- Kilo session state (stored in Kilo's SQLite DB)
- Kilo conversation history
- Kilo's model/provider configuration
- Kilo's internal tool execution results
- Kilo's claims about files changed
- Kilo's claims about verification status
- Kilo's claims about Git commits

### Critical rule:
Kilo's output is **untrusted input**. The application independently verifies:
1. Process exit code (success/failure)
2. Filesystem state (what files actually exist in workspace)
3. VerificationGate verdict (independent check)
4. Git commit SHA (authoritative from WorkspaceManager)

## 7. SECURITY BOUNDARY

### Boundaries enforced by KiloDevelopmentAgent adapter:

1. **Process containment** — Kilo runs in a managed subprocess with:
   - `--dir` constraining working directory
   - `--pure` disabling external plugins
   - No environment variables containing secrets passed to Kilo
   - No shell injection vectors (direct exec, no shell=True)

2. **No authority delegation** — Kilo cannot:
   - Modify application task state
   - Modify run state
   - Create/consume approvals
   - Bypass PermissionEngine
   - Bypass VerificationGate
   - Make Git commits (adapter does not invoke Git for Kilo)
   - Modify its own attempt counter
   - Trigger retries or recovery

3. **Output sanitization** — Kilo's stdout/stderr are:
   - Parsed for JSON events only if format is verified
   - Not used for security decisions
   - Logged but not trusted
   - Truncated to prevent memory exhaustion

4. **Process lifecycle enforcement** — Adapter ensures:
   - Timeout kills process via `proc.kill()`
   - Cancellation kills process via `proc.kill()`
   - Orphan detection on application startup

## 8. THREAT ANALYSIS

### KILO-SEC-01: Kilo operates outside authorized workspace
- **Current protection**: `--dir <workspace_path>` constrains Kilo; WorkspaceWriter also validates paths
- **Missing protection**: Need to verify Kilo respects `--dir` boundary (discovery needed)
- **Protection belongs in**: Adapter (pass `--dir`) + WorkspaceWriter (application-side validation)

### KILO-SEC-02: Kilo executes arbitrary shell operations outside capability boundary
- **Current protection**: Kilo has its own sandbox; adapter passes `--pure`; Kilo's own permission system is independent
- **Missing protection**: Need to verify Kilo's sandbox boundaries with `--pure` mode; adapter cannot prevent Kilo from using its own internal tools
- **Protection belongs in**: Adapter (use `--pure`, configure restrictive Kilo permissions) + Kilo's own sandbox policy

### KILO-SEC-03: Kilo receives or discovers credentials/secrets
- **Current protection**: Application does not pass secrets to Kilo; task description contains no credentials
- **Missing protection**: Kilo may have its own credential store (KILO_API_KEY, etc.)
- **Protection belongs in**: Adapter (ensure no secrets in environment, no secrets in task description)

### KILO-SEC-04: Kilo attempts to bypass PermissionEngine
- **Current protection**: Kilo runs in separate subprocess; has no access to application's PermissionEngine
- **Missing protection**: None — Kilo cannot access application services from subprocess
- **Protection belongs in**: Architecture (subprocess isolation is sufficient)

### KILO-SEC-05: Kilo attempts to bypass ApprovalService
- **Current protection**: ExecutionBridge enforces approval BEFORE creating agent; Kilo never sees approval state
- **Missing protection**: None — approval is checked before Kilo is launched
- **Protection belongs in**: ExecutionBridge (already enforced)

### KILO-SEC-06: Kilo attempts to bypass VerificationGate
- **Current protection**: VerificationGate runs AFTER Kilo completes; Kilo cannot influence it
- **Missing protection**: None — VerificationGate is independent
- **Protection belongs in**: Orchestrator (already enforced)

### KILO-SEC-07: Kilo attempts to modify Git state outside WorkspaceManager authority
- **Current protection**: Kilo can use `kilo worktree` commands but adapter does not authorize Git operations; WorkspaceManager is the only Git authority
- **Missing protection**: Need to verify Kilo doesn't auto-commit or push in `--pure` mode
- **Protection belongs in**: Adapter (do not grant Kilo Git authority; post-run Git state verification)

### KILO-SEC-08: Kilo continues running after cancellation
- **Current protection**: `RunController._terminate_execution` uses `asyncio.shield` + hard deadline; adapter must also `proc.kill()`
- **Missing protection**: Need to verify `proc.kill()` reliably terminates Kilo process tree
- **Protection belongs in**: Adapter (`proc.kill()` + `proc.wait()` with timeout)

### KILO-SEC-09: Kilo continues running after timeout
- **Current protection**: `asyncio.wait_for` + `proc.kill()` in adapter
- **Missing protection**: Need to verify process group termination on Windows
- **Protection belongs in**: Adapter (implement process group kill)

### KILO-SEC-10: Kilo process survives application termination and later modifies workspace
- **Current protection**: No current protection — requires implementation
- **Missing protection**: Orphan process detection on startup; workspace lock validation
- **Protection belongs in**: Adapter (detect and terminate orphans on startup)

### KILO-SEC-11: Kilo reports success while leaving incomplete/invalid workspace
- **Current protection**: Application does NOT trust Kilo's success claim; uses `VerificationGate` + filesystem inspection
- **Missing protection**: None — architecture already enforces independent verification
- **Protection belongs in**: Orchestrator (already enforced)

### KILO-SEC-12: Kilo output contains prompt injection
- **Current protection**: Kilo output is parsed as data, not instructions; `BaseDevelopmentAgent` has explicit injection resistance instruction
- **Missing protection**: Need to ensure adapter does not execute Kilo output as code
- **Protection belongs in**: Adapter (treat all Kilo output as untrusted data; never eval/exec)

### KILO-SEC-13: Kilo session replayed against another task/project
- **Current protection**: Application tracks session IDs per task; adapter passes `--session` only for valid task resumption
- **Missing protection**: Session binding to task_id in adapter
- **Protection belongs in**: Adapter (bind Kilo session IDs to task_id; never reuse cross-task)

### KILO-SEC-14: Kilo session resumed with stale authorization
- **Current protection**: No Kilo-specific authorization model; application authorization is checked before agent creation
- **Missing protection**: None needed — Kilo sessions don't carry application authorization
- **Protection belongs in**: Architecture (ExecutionBridge already enforces)

### KILO-SEC-15: Two Kilo sessions operate concurrently against same unauthorized workspace
- **Current protection**: WorkspaceManager creates unique worktree per task_id; TaskStateMachine prevents concurrent claims
- **Missing protection**: Need to verify Kilo doesn't escape its `--dir` worktree
- **Protection belongs in**: Adapter + WorkspaceManager (already enforced)

### KILO-SEC-16: Kilo adapter introduces hidden retry loop conflicting with RunController
- **Current protection**: RunController manages retry policy; adapter returns single `DevelopmentResult`
- **Missing protection**: Adapter must NOT retry internally; must propagate failure to RunController
- **Protection belongs in**: Adapter design (no internal retry loop; single attempt per `execute()` call)

## 9. CANCELLATION/TIMEOUT FEASIBILITY

### Cancellation:
- **Mechanism**: `asyncio.Event` in `DevelopmentAgentContext`; adapter polls `cancel_event.is_set()` and calls `proc.kill()`
- **Feasibility**: HIGH — subprocess can be killed; `proc.kill()` is reliable on Windows
- **Caveat**: Need to handle process group termination on Windows (use `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`)

### Timeout:
- **Mechanism**: `asyncio.wait_for(agent.execute(context), timeout=timeout_seconds)` in `RunController`; adapter must also implement subprocess-level timeout
- **Feasibility**: HIGH — `asyncio.wait_for` wraps the entire execution
- **Caveat**: Kilo may spawn child processes; adapter must ensure entire process tree is terminated

## 10. SESSION/RECOVERY FEASIBILITY

### Session management:
- Kilo supports `--session <id>`, `--continue`, `--fork`, `kilo export/import`
- **Feasibility**: MEDIUM — Kilo's session mechanism is Kilo-owned state
- **Application integration**: Application can store Kilo session IDs in `DevelopmentTask.metadata` for repair continuation
- **Limitation**: Kilo sessions are NOT portable across machines/contexts without `export/import`

### Recovery:
- If Kilo crashes, `RunController` detects via process exit code
- Task state remains in current state; `TaskStateMachine` handles transition to `REPAIR_REQUIRED` or `FAILED`
- New agent execution can optionally use `--continue` to resume Kilo session
- **Feasibility**: MEDIUM — requires careful state management between Kilo sessions and application task state

## 11. REQUIRED CONFIGURATION

### New configuration (to be added in Stage 10.2):
```python
# Example configuration model
class KiloConfig(BaseModel):
    kilo_executable_path: str = "C:\\Users\\adamz\\.vscode\\extensions\\kilocode.kilo-code-7.6.2-win32-x64\\bin\\kilo.exe"
    kilo_enabled: bool = False
    kilo_default_model: str | None = None
    kilo_session_tracking: bool = True
    kilo_pure_mode: bool = True
    kilo_auto_approve: bool = False  # DANGEROUS — must be False
    kilo_subprocess_timeout_seconds: float = 300.0
    kilo_kill_timeout_seconds: float = 15.0
```

### Settings service additions:
- `kilo_executable_path` — path to Kilo CLI binary
- `kilo_enabled` — toggle Kilo provider
- `kilo_default_model` — default model override
- `kilo_pure_mode` — use `--pure` flag

## 12. PROPOSED STAGE 10.2 IMPLEMENTATION

### Scope (minimal):

1. **`KiloDevelopmentAgent`** — new file implementing `DevelopmentAgent`:
   - `execute(context) -> DevelopmentResult`
   - Launches Kilo subprocess with `--dir`, `--format json`, `--pure`
   - Parses stdout for JSON events (if format verified)
   - Enforces timeout and cancellation
   - Returns `DevelopmentResult` with application-owned data

2. **`KiloAdapterConfig`** — configuration model for Kilo path and settings

3. **`KiloProcessManager`** — narrow subprocess lifecycle helper:
   - `start(task_description, workspace_path, session_id) -> asyncio.subprocess.Process`
   - `cancel(process) -> None`
   - `wait_with_timeout(process, timeout) -> tuple[returncode, stdout, stderr]`
   - No retry logic, no state ownership

4. **`ProviderManager.get_coding_provider()`** — add `kilo` provider type that returns `KiloDevelopmentAgent` (NOTE: this requires factory-level change, not CodingProvider-level)

5. **Tests**:
   - `test_kilo_adapter.py` — focused tests for KiloDevelopmentAgent
   - Mock subprocess to avoid real Kilo dependency in tests
   - Test: process launch, timeout, cancellation, workspace boundary, output parsing, failure modes

### Files expected to modify/create in 10.2:
- `services/core/app/orchestration/agents/kilo_agent.py` (new)
- `services/core/app/orchestration/models.py` (extend if needed)
- `services/core/app/ai/services/provider_manager.py` (add kilo provider type)
- `services/core/tests/test_kilo_agent.py` (new)

### Files explicitly NOT modified in 10.2:
- `services/core/app/ai_manager/` — no changes
- `services/core/app/services/autonomous_loop.py` — no changes
- `services/core/app/orchestration/run_controller.py` — no changes
- `services/core/app/orchestration/orchestrator.py` — no changes
- `services/core/app/tools/permission_engine.py` — no changes
- `services/core/app/verification/gate.py` — no changes
- `services/core/app/workspace/workspace_manager.py` — no changes
- `services/core/app/ai/providers/coding_base.py` — no changes (Kilo does NOT implement CodingProvider)

## 13. TEST STRATEGY

### Unit tests for KiloDevelopmentAgent:
1. `test_construction_with_config` — valid config creates agent
2. `test_construction_without_executable_raises` — missing kilo.exe raises
3. `test_execute_launches_subprocess` — subprocess launched with correct args
4. `test_execute_passes_workspace_dir` — `--dir` matches workspace_path
5. `test_execute_passes_pure_mode` — `--pure` flag present
6. `test_execute_timeout_kills_process` — timeout triggers proc.kill()
7. `test_execute_cancellation_kills_process` — cancel_event triggers proc.kill()
8. `test_execute_success_returns_success` — exit code 0 returns SUCCESS
9. `test_execute_failure_returns_error` — non-zero exit code returns ERROR
10. `test_execute_malformed_json_ignored` — invalid JSON doesn't crash adapter
11. `test_changed_files_from_filesystem_not_kilo` — changed_files from WorkspaceWriter scan
12. `test_no_internal_retry_loop` — single subprocess invocation per execute()
13. `test_no_permission_bypass` — PermissionEngine not invoked by adapter
14. `test_no_verification_bypass` — VerificationGate not invoked by adapter
15. `test_no_git_authority` — adapter does not invoke Git commands
16. `test_session_id_tracking` — Kilo session ID stored in result details
17. `test_concurrent_execution_isolation` — two tasks don't share Kilo session
18. `test_output_truncation` — large stdout/stderr truncated
19. `test_prompt_injection_resistance` — Kilo output not executed as code
20. `test_orphan_detection` — pre-existing Kilo processes detected and terminated

### Integration tests:
1. `test_kilo_in_orchestrator` — KiloDevelopmentAgent plugged into AutonomousTaskOrchestrator
2. `test_kilo_with_verification_gate` — end-to-end with VerificationGate
3. `test_kilo_with_git_checkpoint` — end-to-end with WorkspaceManager checkpoint

## 14. RISKS/LIMITATIONS

1. **Kilo run non-interactive behavior unverified**: `kilo run --format json` may hang waiting for interactive input or model configuration. This is the **primary blocker** for Stage 10.2.

2. **Kilo serve/acp API schema unknown**: The HTTP/ACP APIs are not documented or explored. Using them requires reverse-engineering.

3. **Kilo sandbox policy conflicts**: Kilo has its own permission system (`kilo.jsonc`). Integrating with AI Operator's `PermissionEngine` requires careful reconciliation.

4. **Kilo's shell execution capability**: Kilo can execute bash commands internally. Even with `--pure` and workspace constraints, Kilo's internal sandbox may allow operations that AI Operator would deny.

5. **Process management on Windows**: Killing process trees on Windows requires special handling (`CREATE_NEW_PROCESS_GROUP`, `taskkill /T`).

6. **Kilo state persistence**: Kilo stores state in a SQLite DB. Concurrent access from multiple AI Operator tasks may cause conflicts.

7. **Model configuration dependency**: Kilo requires a configured model/provider. AI Operator must ensure Kilo's configuration is valid before launching.

8. **No structured output guarantee**: The `--format json` flag may not produce machine-parseable output for all Kilo operations.

## 15. EXPLICITLY REJECTED INTEGRATION DESIGNS

### Rejected A: Kilo as CodingProvider
**Reason**: `CodingProvider` is for LLM providers returning structured `CodingOperation` objects. Kilo is a full agent runtime with shell execution, state management, and sandbox. Fitting Kilo into this interface would require:
- Inventing unreliable output parsing
- Trusting Kilo's claims about file changes
- Bypassing WorkspaceWriter/VerificationGate/Git authority

### Rejected B: Kilo as second orchestration system
**Reason**: Kilo must NOT own task state, run state, permissions, approvals, verification, Git, retry policy, or execution identity. These remain application-owned.

### Rejected C: Kilo MCP server integration
**Reason**: No MCP configuration exists in AI Operator repository. While Kilo supports MCP (`kilo mcp`), adding MCP infrastructure is out of scope for Stage 10.

### Rejected D: Kilo as HTTP API client
**Reason**: While `kilo serve` exists, its API schema is unknown. Adding HTTP client infrastructure before understanding the API is speculative.

### Rejected E: Direct Kilo Python SDK
**Reason**: Kilo does not expose a Python SDK or importable package. It is a CLI/desktop application.

## 16. EXACT FILES THAT STAGE 10.2 WOULD LIKELY MODIFY

| File | Action | Description |
|------|--------|-------------|
| `services/core/app/orchestration/agents/kilo_agent.py` | CREATE | KiloDevelopmentAgent implementation |
| `services/core/app/orchestration/agents/__init__.py` | MODIFY | Export KiloDevelopmentAgent |
| `services/core/app/ai/services/provider_manager.py` | MODIFY | Add `kilo` provider type to `get_coding_provider()` |
| `services/core/app/services/settings_service.py` | MODIFY | Add Kilo-specific settings |
| `services/core/tests/test_kilo_agent.py` | CREATE | Focused unit tests |

### Files explicitly NOT modified:
- `services/core/app/ai_manager/`
- `services/core/app/services/autonomous_loop.py`
- `services/core/app/orchestration/run_controller.py`
- `services/core/app/orchestration/orchestrator.py`
- `services/core/app/tools/permission_engine.py`
- `services/core/app/verification/gate.py`
- `services/core/app/workspace/workspace_manager.py`
- `services/core/app/ai/providers/coding_base.py`
- `services/core/app/ai/providers/coding_ollama.py`
- `services/core/app/ai/providers/coding_openrouter.py`

## 17. RECOMMENDED NEXT STEP

### Stage 10.2 blocker must be resolved first:

**Verify `kilo run --format json` non-interactive behavior**:
1. Configure a valid Kilo model in Kilo's settings
2. Run: `kilo run --format json --pure --dir "F:\Projects\AI-Operator" "Create a file test.txt with content hello"`
3. Capture actual JSON event schema
4. Determine if Kilo can complete a coding task non-interactively
5. If `kilo run` hangs, test `kilo serve` + HTTP API or `kilo acp` + ACP protocol instead

### If `kilo run --format json` works:
- Proceed with KiloDevelopmentAgent as subprocess wrapper
- Implement Stage 10.2 as described in Section 12

### If `kilo run` does not work non-interactively:
- Explore `kilo serve` HTTP API schema
- Explore `kilo acp` ACP protocol schema
- If neither works, Stage 10.2 scope expands to include Kilo API reverse-engineering

### Known blockers for Stage 10.2:
1. Kilo's non-interactive `run` behavior is **unverified** — may require model configuration
2. JSON event schema from `kilo run --format json` is **unknown**
3. `kilo serve`/`kilo acp` API schemas are **unknown**
4. Kilo's sandbox behavior with `--pure` flag and `--dir` constraint is **unverified**

---

**STAGE 10.1 DISCOVERY COMPLETE**

Kilo integration recommendation: Kilo as a `DevelopmentAgent` implementation (subprocess-backed adapter), NOT as a `CodingProvider`.

Exact architectural boundary: `DevelopmentAgent` interface → `KiloDevelopmentAgent` adapter → `kilo.exe` subprocess → authorized workspace (via `--dir`).

Key security conclusions: Kilo must never own application state. All security-sensitive decisions (verification, Git, permissions, approvals, task state) remain in application-owned services. Kilo output is untrusted. Process lifecycle is adapter-managed.

Stage 10.2 implementation scope: One narrow `KiloDevelopmentAgent` adapter, `KiloProcessManager` helper, ProviderManager wiring, focused unit tests. No changes to ExecutionBridge, RunController, VerificationGate, PermissionEngine, or WorkspaceManager.

Known blockers: Kilo's non-interactive execution behavior and JSON output schema must be verified before implementation.

Test strategy: Mock subprocess-based unit tests covering construction, launch, timeout, cancellation, workspace boundary, output parsing, failure modes, injection resistance, and no-hidden-retry enforcement.
