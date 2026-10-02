# Stage 18.1 — File Tools Architecture & Contract Discovery

## Executive Summary

Stage 18.1 defines the architecture, security model, and tool contracts for the AI Operator filesystem capability layer. This is a **DISCOVERY/CONTRACT stage only** — no implementation, no refactoring, and no production code modifications are performed.

The filesystem layer is subordinate to the existing Tool Framework (`ToolExecutionService`, `PermissionEngine`, `ApprovalService`). All new file tools must flow through the same security pipeline as existing tools.

**Verdict: STAGE_18_1_CONTRACT_READY**

---

## 1. Current State Assessment

### 1.1 Existing Filesystem Tool

| Property | Value |
|----------|-------|
| Tool ID | `files.read_file` |
| Implementation | `FilesReadFile` in `services/core/app/tools/implementations/files_read.py` |
| Risk Level | `LOW` |
| Category | `FILES` |
| Registration | Static at startup via `lifecycle.py` |

### 1.2 Path Security Controls (Current)

The `FilesReadFile` implementation enforces the following checks in order:

1. **Absolute path rejection** — `p.is_absolute() or p.drive != ""` rejects absolute paths, drive letters, and UNC paths.
2. **Literal `..` rejection** — `any(part == ".." for part in p.parts)` rejects traversal components.
3. **Resolution + containment** — `workspace_root.resolve()` and `target_path.relative_to(workspace_root)` reject paths escaping the workspace after symlink resolution.
4. **Directory rejection** — `target_path.is_dir()` rejects directory targets.
5. **Symlink rejection** — `target_path.is_symlink()` plus redundant re-resolve check rejects symlinks and symlink-induced path changes.

### 1.3 Known Limitations (Current)

| Limitation | Severity | Status |
|------------|----------|--------|
| TOCTOU race between validation and read | Low | Inherent to user-space Python |
| Windows junctions/reparse points not caught by `is_symlink()` | Medium | Not explicitly tested on Windows |
| Mixed path separators (`subdir\\..\\nested.txt`) on Windows | Low | Not explicitly tested |
| No `files.write_file`, `files.list_directory`, `files.delete_file` | N/A | Deferred to implementation stages |

---

## 2. Architecture

### 2.1 Authoritative Execution Path

All tool execution flows through a single, enforced pipeline:

```
ToolExecutionService.execute_tool_call()
    ├── 1. Tool existence check (ToolRegistry)
    ├── 2. Enabled check (ToolRegistry)
    ├── 3. Argument validation (validate_arguments)
    ├── 4. PermissionEngine.evaluate()
    │       ├── Rule 1: Tool existence
    │       ├── Rule 2: Tool enabled state
    │       ├── Rule 3: Action validation
    │       ├── Rule 4: Grant evaluation
    │       ├── Rule 5: Confirmation requirement
    │       ├── Rule 6: Autonomy mode evaluation
    │       ├── Rule 7: Fail-closed default
    │       └── _evaluate_approval_override()
    ├── 5. ONLY if ALLOW → ToolExecutor.execute()
    │       └── impl.execute(arguments, context)
    ├── 6. Result validation (validate_result)
    └── 7. Audit logging (fail-soft)
```

**Invariant**: `PermissionEngine.evaluate()` is ALWAYS evaluated before `ToolExecutor.execute()`. DENY and ASK_USER decisions NEVER reach the executor.

### 2.2 Context Authority

The `ToolExecutionContext` is built by `ToolExecutionService._build_execution_context()` and is **never** derived from model arguments:

| Property | Source | Trust Level |
|----------|--------|-------------|
| `request_id` | Explicit param or `tool_call.request_id` | Application-owned |
| `actor_id` | Explicit param from authenticated session | Application-owned |
| `workspace.workspace_path` | Explicit `workspace_path` param or trusted context dict | Application-owned |
| `tool_call_id` | `tool_call.tool_call_id` | Application-owned |
| `approval_context` | Server-side `ApprovalVerificationToken` | Application-owned |

**Critical Invariant**: The model must NEVER be able to choose or override:
- Workspace root
- Execution identity
- Authorization context
- Permission result
- Approval state

### 2.3 Workspace Isolation

The `WorkspaceManager` provides Git worktree isolation for autonomous tasks:

- Autonomous worktrees are placed **outside** the primary worktree directory.
- Primary worktree is protected from destructive operations (`reset --hard`, `clean -fd`, etc.).
- Worktree paths are derived from `task_id` and `execution_token` using a fixed scheme.
- Ownership is verified via `.ai_operator_ownership` marker and `git config ai-operator.execution-token`.

The `files.read_file` tool operates within the **conversation workspace** (`context.workspace.workspace_path`), not the autonomous worktree. These are separate concerns.

---

## 3. Security Model

### 3.1 Protection Classifications

All filesystem protections are classified into one of four tiers:

| Classification | Definition |
|----------------|------------|
| **PROTECTED V1** | Enforced in production code, tested, cannot be bypassed by model input |
| **BEST EFFORT** | Implemented but has known theoretical bypasses (e.g., TOCTOU) |
| **DEFERRED** | Planned for future implementation stage |
| **NOT PRACTICALLY SOLVABLE** | Cannot be reliably enforced in user-space Python |

### 3.2 Current Protection Classifications

| Protection | Classification | Evidence |
|------------|----------------|----------|
| Absolute path rejection | PROTECTED V1 | `p.is_absolute() or p.drive != ""` in `files_read.py:71-72` |
| Literal `..` rejection | PROTECTED V1 | `any(part == ".." for part in p.parts)` in `files_read.py:73-74` |
| Resolution + containment | PROTECTED V1 | `target_path.relative_to(workspace_root)` in `files_read.py:84-86` |
| Directory target rejection | PROTECTED V1 | `target_path.is_dir()` in `files_read.py:90-91` |
| Symlink rejection | PROTECTED V1 | `is_symlink()` + re-resolve in `files_read.py:94-101` |
| Permission boundary | PROTECTED V1 | `ToolExecutionService` enforces evaluation before execution |
| Audit logging | PROTECTED V1 | Fail-soft audit in all security boundaries |
| Approval binding | PROTECTED V1 | `tool_id`, `action`, `args_hash`, `request_id`, `fingerprint` binding |
| Workspace path authority | PROTECTED V1 | Derived from explicit param or trusted context, never from arguments |
| TOCTOU prevention | NOT PRACTICALLY SOLVABLE | Multiple `stat()` calls between validation and read |
| Windows junction detection | BEST EFFORT | `resolve()` follows junctions, but not explicitly tested |
| Windows ADS access | BEST EFFORT | Python `pathlib` does not expose ADS paths directly |
| Windows reserved names | DEFERRED | `CON`, `PRN`, `AUX`, `NUL` etc. not currently checked |
| Race-condition hardening | NOT PRACTICALLY SOLVABLE | User-space Python cannot atomically validate-and-read |

### 3.3 Windows-Specific Security Risks

| Risk | Description | Classification |
|------|-------------|----------------|
| **Junctions / Reparse Points** | `is_symlink()` returns `False` for junctions; `resolve()` follows them, potentially escaping workspace | BEST EFFORT |
| **Alternate Data Streams (ADS)** | `path:stream` syntax can access hidden data; `Path` normalizes away the stream in some cases | BEST EFFORT |
| **Reserved Device Names** | `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9` can be opened as files | DEFERRED |
| **Mixed Path Separators** | `subdir\\..\\nested.txt` may bypass naive string checks; `Path` normalizes on Windows | BEST EFFORT |
| **UNC Paths** | `\\server\share\file` can bypass drive-letter checks | PROTECTED V1 (absolute path check catches) |
| **Case Sensitivity** | Windows is case-insensitive; `..\\..\\` vs `..\..\` normalization | BEST EFFORT (Path handles) |
| **TOCTOU on Windows** | Same as POSIX; file can be replaced between validation and read | NOT PRACTICALLY SOLVABLE |

---

## 4. Tool Contracts (Proposed)

### 4.1 `files.read_file` (Existing)

| Property | Value |
|----------|-------|
| Tool ID | `files.read_file` |
| Action | `read_file` |
| Risk Level | `LOW` |
| Category | `FILES` |
| Parameters | `path` (string, required, maxLength 4096), `max_bytes` (integer, optional, max 1048576) |
| Result | `content` (string), `size` (integer), `encoding` (string), `truncated` (boolean) |
| Workspace Dependency | Requires `context.workspace.workspace_path` |

### 4.2 `files.write_file` (Proposed)

| Property | Value |
|----------|-------|
| Tool ID | `files.write_file` |
| Action | `write_file` |
| Risk Level | `MEDIUM` |
| Category | `FILES` |
| Parameters | `path` (string, required, maxLength 4096), `content` (string, required, maxLength 1048576), `create_dirs` (boolean, optional) |
| Result | `success` (boolean), `path` (string), `size` (integer) |
| Workspace Dependency | Requires `context.workspace.workspace_path` |
| Approval | `requires_confirmation=True` in AUTONOMOUS mode |

**Security Requirements**:
- Same path containment as `files.read_file`
- Reject write to existing directories
- Reject write to symlink targets
- Atomic write: write to temp file, then rename
- UTF-8 encoding only; reject invalid UTF-8 with clear error
- `create_dirs=False` by default; when `True`, only create missing intermediate directories

### 4.3 `files.list_directory` (Proposed)

| Property | Value |
|----------|-------|
| Tool ID | `files.list_directory` |
| Action | `list_directory` |
| Risk Level | `LOW` |
| Category | `FILES` |
| Parameters | `path` (string, required, maxLength 4096), `recursive` (boolean, optional) |
| Result | `entries` (array of `{name, type, size}`), `path` (string) |
| Workspace Dependency | Requires `context.workspace.workspace_path` |

**Security Requirements**:
- Same path containment as `files.read_file`
- Reject listing outside workspace
- Do not follow symlinks when resolving directory entries
- Limit recursion depth when `recursive=True`
- Exclude hidden files/directories starting with `.` (configurable)

### 4.4 `files.delete_file` (Proposed)

| Property | Value |
|----------|-------|
| Tool ID | `files.delete_file` |
| Action | `delete_file` |
| Risk Level | `HIGH` |
| Category | `FILES` |
| Parameters | `path` (string, required, maxLength 4096) |
| Result | `success` (boolean), `path` (string) |
| Workspace Dependency | Requires `context.workspace.workspace_path` |
| Approval | `requires_confirmation=True` always |

**Security Requirements**:
- Same path containment as `files.read_file`
- Reject deletion of directories (use `files.delete_directory` if needed)
- Reject deletion of symlink targets
- Move to trash/recycle bin instead of permanent delete (if OS supports)
- Log deletion in audit trail with full path

---

## 5. Implementation Constraints

### 5.1 Non-Negotiable Invariants

1. **No dynamic imports**: Tool implementations must be statically registered at startup.
2. **No shell execution**: All subprocess calls must use `shell=False` with explicit argument arrays.
3. **No eval/exec**: No dynamic code execution in tool implementations.
4. **Permission first**: `PermissionEngine.evaluate()` always precedes `ToolExecutor.execute()`.
5. **Fail-closed**: Any permission evaluation failure results in DENY.
6. **Audit fail-soft**: Audit logging failures never alter permission decisions.
7. **Context authority**: Workspace path, actor identity, and approval state are never derived from model arguments.

### 5.2 Size Limits

| Limit | Value | Enforced At |
|-------|-------|-------------|
| `MAX_EXECUTION_TIME_MS` | 30,000 | `ToolExecutionService` |
| `MAX_ARGUMENTS_SIZE_BYTES` | 65,536 | `ToolExecutionService` |
| `MAX_RESULT_SIZE_BYTES` | 262,144 | `ToolExecutionService` |
| `MAX_READ_BYTES` | 1,048,576 | `files.read_file` implementation |
| `MAX_WRITE_BYTES` | 1,048,576 | Proposed `files.write_file` |
| `MAX_TOOL_CALLS_PER_REQUEST` | 10 | `ToolExecutionService` |

---

## 6. Testing Requirements

### 6.1 Mandatory Test Coverage

Every new file tool must include tests for:

| Test Category | Requirement |
|---------------|-------------|
| Valid operation | Reads/writes/lists/deletes within workspace |
| Path traversal | `../outside`, `..\\outside`, mixed separators |
| Absolute paths | `/etc/passwd`, `C:\\Windows`, `\\\\server\\share` |
| Symlink containment | Symlinks pointing outside workspace |
| Junction containment | Junctions pointing outside workspace (Windows) |
| Directory operations | Attempting to read/write/delete directories |
| Permission boundary | DENY/ASK_USER prevents execution |
| Approval binding | Approval cannot be reused for different arguments |
| Workspace spoofing | Model arguments cannot override workspace_path |
| Size limits | Oversized reads/writes are rejected |
| TOCTOU awareness | Documented limitation, not testable in CI |
| Audit trail | Operations are recorded in audit log |

### 6.2 Windows-Specific Tests

Tests marked `@pytest.mark.skipif(sys.platform != "win32")` must cover:

- Junction point escape attempts
- ADS path attempts (`file.txt:stream`)
- Reserved device names (`CON`, `PRN`, `NUL`, etc.)
- Mixed path separators
- Case-insensitive path matching

---

## 7. Gap Analysis

### 7.1 Current Gaps

| Gap | Severity | Recommended Action |
|-----|----------|-------------------|
| No `files.write_file` | High | Implement in next stage |
| No `files.list_directory` | Medium | Implement in next stage |
| No `files.delete_file` | Medium | Implement with HIGH risk and approval requirement |
| Windows junctions not tested | Medium | Add Windows-specific tests |
| Reserved device names not checked | Low | Add validation in path security layer |
| No atomic write pattern | Medium | Implement temp-file-then-rename for writes |
| TOCTOU not mitigated | Low | Document as accepted V1 limitation |

### 7.2 Dual Provider Hierarchy

**CLARIFIED in `STAGE_18_1A_PROVIDER_ARCHITECTURE_CLARIFICATION.md`:**

- `app/ai/providers/` — active, production-used hierarchy for `AIService` and autonomous development
- `app/ai_manager/providers/` — **dead code** in production; `AIManager` is never instantiated in any route, service, or orchestrator
- `app/manager/providers/` — also dead code (`PlanProvider` hierarchy)

**CONCLUSION:** This is **NOT an architectural blocker for Stage 18**. The active provider hierarchy (`app/ai/providers/`) is healthy and production-ready. File tools integrate with `ToolExecutionService` and `PermissionEngine`, not directly with any provider hierarchy. The dead code should be documented and deferred, not blocking Stage 18.

---

## 8. Acceptance Criteria

### Stage 18.1 Criteria (This Document)

- [x] Current filesystem tool implementation verified
- [x] Security boundaries documented and classified
- [x] Windows-specific risks analyzed
- [x] Proposed tool contracts defined
- [x] Implementation constraints documented
- [x] Testing requirements specified
- [x] Gap analysis completed
- [x] **No production code modified**

### Future Implementation Criteria (Stage 18.2+)

- [ ] All proposed tools implemented with PROTECTED V1 path security
- [ ] Windows-specific tests pass on Windows CI
- [ ] Full repository regression green
- [ ] Audit trail covers all filesystem operations
- [ ] Approval flow works for MEDIUM/HIGH risk file tools

---

## 9. References

| Document | Path |
|----------|------|
| Master Specification | `AI_OPERATOR_MASTER_SPECIFICATION.md` |
| Stage 17.2 Final Report | `STAGE_17_2_FINAL_REPORT.md` |
| Stage 17.3A Final Report | `STAGE_17_3A_FINAL_REPORT.md` |
| Stage 17.4 Alignment | `STAGE_17_4_ARCHITECTURE_ALIGNMENT.md` |
| Tool Execution Service | `services/core/app/tools/tool_execution_service.py` |
| Permission Engine | `services/core/app/tools/permission_engine.py` |
| Files Read Implementation | `services/core/app/tools/implementations/files_read.py` |
| Workspace Manager | `services/core/app/workspace/workspace_manager.py` |
| Git Runner | `services/core/app/workspace/git_runner.py` |
| Tool Models | `services/core/app/tools/models.py` |
| Test Suite | `services/core/tests/test_files_read_file.py` |
