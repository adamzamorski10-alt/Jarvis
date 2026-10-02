# Stage 18.6 — files.search Final Report

## 1. Implementation Summary

Implemented `files.search` as a registered `BaseTool` in the AI Operator tool framework. The tool follows the established `ToolExecutionService → ToolRegistry → PermissionEngine → ToolExecutor → FilesSearch → result validation → audit` boundary.

**Files created:**
- `services/core/app/tools/implementations/files_search.py`
- `services/core/tests/test_files_search.py`

**Files modified:**
- `services/core/app/core/lifecycle.py` — registered `FilesSearch` at startup

---

## 2. Exact Contract

| Property | Value |
|----------|-------|
| Tool ID | `files.search` |
| Action | `search` |
| Category | `FILES` |
| Risk Level | `LOW` |
| `requires_confirmation` | `False` |
| Parameters | `query` (string, required, maxLength 256), `path` (string, optional, default ".", maxLength 4096), `max_results` (integer, optional, default 50, maximum 200) |
| Result | `success` (boolean), `query` (string), `path` (string), `matches` (array of `{path, name, type, size}`), `truncated` (boolean) |
| Workspace Dependency | Requires `context.workspace.workspace_path` |
| Approval | `requires_confirmation=False`; LOW risk → no confirmation required |

---

## 3. Search Semantics

**V1: Filename/path substring search only.**

- Query is treated as a literal substring, not regex/glob
- Matching is case-insensitive for ASCII
- Matches against both filename and full workspace-relative path
- No content inspection
- No regex interpretation
- No wildcard semantics

**Match types returned:**
- `file` — regular files
- `directory` — directories
- `symlink` — symlinks (not followed)
- `other` — other filesystem objects

---

## 4. Path-Security Analysis

The implementation reuses the established path-security model:

| Check | Implementation | Classification |
|-------|---------------|----------------|
| Absolute path rejection | `p.is_absolute() or p.drive != ""` | PROTECTED V1 |
| Literal `..` rejection | `any(part == ".." for part in p.parts)` | PROTECTED V1 |
| Resolution + containment | `root_path.relative_to(workspace_root)` | PROTECTED V1 |
| Symlink rejection (root) | `is_symlink()` + re-resolve | PROTECTED V1 |
| Directory requirement | `root_path.is_dir()` | PROTECTED V1 |
| Workspace root authority | From `ToolExecutionContext.workspace.workspace_path` | PROTECTED V1 |

Verified cases:
- `../outside` → blocked
- `..\outside` → blocked
- `/etc/passwd` → blocked
- `C:\Windows\...` → blocked (Windows)
- `\\server\share\...` → blocked
- Symlinked search root → blocked
- Workspace root escape → blocked

---

## 5. Resource Limits

| Resource | V1 Bound | Enforcement |
|----------|----------|-------------|
| `query` length | 256 characters | Schema `maxLength` |
| `path` length | 4096 characters | Schema `maxLength` |
| `max_results` | 200 (hard cap) | Implementation |
| `max_depth` | 10 | Implementation |
| `max_files_inspected` | 5,000 | Implementation |
| Execution time | 30,000 ms | `ToolExecutionService` global |

**Truncation:** `truncated=True` is set when:
- `max_results` is reached
- `max_files_inspected` is reached
- `max_depth` is reached

---

## 6. Traversal Behavior

- **Deterministic ordering:** Results sorted by workspace-relative path, case-insensitive
- **Symlink directories:** Not traversed
- **Symlink files:** Reported as `type="symlink"`, not followed
- **No automatic exclusions:** `.git`, `node_modules`, `dist`, `build`, `.venv` are NOT excluded
- **No shell/subprocess:** Pure Python `pathlib` traversal
- **Bounded recursion:** Depth-limited to 10 levels

---

## 7. Symlink / Reparse Behavior

- **Symlink directories:** Skipped during traversal
- **Symlink files:** Reported in results as `type="symlink"` without following
- **Symlink search roots:** Rejected
- **Windows junctions/reparse points:** BEST EFFORT — `is_symlink()` returns `False` for junctions; same limitation as other filesystem tools

---

## 8. Permission/Approval Verification

| Scenario | Expected | Observed | Result |
|----------|----------|----------|--------|
| DENY (no grant, MANUAL mode) | No traversal | `CANCELLED` | VERIFIED |
| ASK_USER (no grant, AUTONOMOUS mode) | No traversal | `CANCELLED` | VERIFIED |
| ALLOW (with grant, AUTONOMOUS mode) | Search executes | Results returned | VERIFIED |
| Invalid approval context | No traversal | `DENIED` | VERIFIED |
| `requires_confirmation` | `False` for LOW risk | No confirmation required | VERIFIED |

---

## 9. Audit Verification

- Audit events recorded via existing audit mechanism
- Event types include `PERMISSION_GRANTED`, `PERMISSION_DENIED`, `PERMISSION_EVALUATED`
- No file contents logged
- No absolute workspace paths logged
- Result is bounded

---

## 10. Test Inventory

**Focused `files.search` tests (45 total):**
- A. Basic behavior: 6 tests (filename match, nested match, multiple matches, no match, deterministic ordering, case-insensitive)
- B. Query validation: 4 tests (empty query, max length accepted, over max length, special characters)
- C. Search root: 8 tests (root dot, valid subdirectory, nonexistent, absolute, drive, UNC, traversal, mixed separators, symlink root)
- D. Bounds: 5 tests (default max_results, custom max_results, capped at 200, max_depth, truncated on max_results)
- E. Symlink security: 4 tests (symlink file, symlink directory, resolved outside, symlink root)
- F. Permission boundary: 5 tests (DENY, ASK_USER, ALLOW, requires_no_confirmation, invalid approval)
- G. Context authority: 1 test (workspace_path required)
- H. Audit: 2 tests (event exists, result bounded)
- I. Failure behavior: 2 tests (missing root, no mutation on deny)
- J. Regression: 7 tests (read_file, list_directory, write_file, delete_file, TES boundary, PermissionEngine boundary)
- Windows-specific: 2 tests (mixed separators, reserved name)
- Skipped: 4 (Windows symlink tests)

**Focused test result:** 41 passed, 4 skipped, 0 failed

---

## 11. False-Positive Audit

- **DENY/ASK_USER:** Tests use real `ToolExecutionService` with `PermissionEngine`; no direct tool invocation
- **Workspace containment:** Verified through production `ToolExecutionContext` construction
- **Traversal bounds:** Verified through actual directory creation and depth testing
- **Symlink behavior:** Verified through actual symlink creation on non-Windows
- **No content reading:** Implementation uses `iterdir()` only, never `open()` or `read_text()`

---

## 12. Full Regression Result

```
========== 2488 passed, 21 skipped, 119 warnings in 686.54s (0:11:26) ==========
```

All tests passed. No failures.

---

## 13. Git Scope

| File | Change |
|------|--------|
| `services/core/app/tools/implementations/files_search.py` | NEW — FilesSearch implementation |
| `services/core/app/core/lifecycle.py` | MODIFIED — files_search registration |
| `services/core/tests/test_files_search.py` | NEW — comprehensive test suite |

---

## 14. Known Limitations

| Limitation | Classification | Evidence |
|------------|----------------|----------|
| TOCTOU between validation and traversal | BEST EFFORT | Inherent to user-space Python |
| Windows junctions/reparse points | BEST EFFORT | `is_symlink()` returns `False` for junctions |
| No content search | DEFERRED | By design for V1 |
| No regex/glob | DEFERRED | By design for V1 |
| No automatic directory exclusions | DEFERRED | By design for V1 |

---

## 15. Deferred Work

- Text-content search
- Regex/glob pattern matching
- Indexed search
- Search caching
- Automatic directory exclusions (`.git`, `node_modules`, etc.)
- Memory Retrieval (Stage 19)
- Context Assembly (post-Stage 19)
- Model Router (Stage 21)
- `files.copy_file`
- `files.move_file`

---

## 16. Stage 18 Completion

Stage 18 now has **5 filesystem tools**:

| Tool | Status |
|------|--------|
| `files.read_file` | Implemented |
| `files.list_directory` | Implemented |
| `files.write_file` | Implemented |
| `files.delete_file` | Implemented |
| `files.search` | Implemented (this stage) |

This satisfies the Master Specification's "5+ file tools" requirement.

---

## 17. Final Status

**STAGE_18_6_FINAL_ACCEPTANCE_PASS**

All Stage 18.6 acceptance criteria are satisfied:
- `files.search` implemented with filename/path substring search only
- No content search, regex, or glob
- Bounded traversal: `max_results=200`, `max_depth=10`, `max_files_inspected=5000`
- Workspace containment enforced
- Symlinks not followed
- PermissionEngine boundary intact
- DENY/ASK_USER prevent traversal
- Audit integrated
- 41 focused tests pass (4 skipped)
- 2488 complete repository tests pass (21 skipped, 0 failed)
- No unrelated security regression
