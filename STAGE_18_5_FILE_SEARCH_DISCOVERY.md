# Stage 18.5 — files.search Architecture & Contract Discovery

## 1. Executive Summary

Stage 18.5 is a **DISCOVERY-ONLY** stage. No production code was modified.

This document analyzes whether `files.search` should be implemented as part of Stage 18 (File Tool Expansion), defines its exact V1 contract, and establishes clear boundaries with future Memory Retrieval and Context Assembly.

**Recommendation:** Implement `files.search` as a **filename/path-only search** tool in a follow-on Stage 18 sub-stage. Text-content search is deferred to Stage 19 (Memory Retrieval) or a dedicated future stage.

---

## 2. Current Filesystem Tool Inventory

| Tool | Status | Risk | Registration |
|------|--------|------|-------------|
| `files.read_file` | Implemented (Stage 18.1) | LOW | lifecycle.py |
| `files.list_directory` | Implemented (Stage 18.2) | LOW | lifecycle.py |
| `files.write_file` | Implemented (Stage 18.3) | MEDIUM | lifecycle.py |
| `files.delete_file` | Implemented (Stage 18.4) | HIGH | lifecycle.py |
| `files.search` | **NOT IMPLEMENTED** | TBD | — |

**Current count:** 4 tools.

**Master Specification requirement:** "5+ file tools" for Stage 18.

**Gap:** `files.search` is the single missing tool to satisfy the "5+ file tools" criterion.

---

## 3. Existing Search-Related Code

### 3.1 Filesystem Traversal in Production

| Location | Usage | Scope |
|----------|-------|-------|
| `app/orchestration/agents/kilo_agent.py:332` | `workspace.rglob("*")` | Workspace snapshot for change detection |
| `app/tools/implementations/files_list.py:137-148` | `directory.iterdir()` | Immediate and recursive directory listing |

**Finding:** There is NO existing general-purpose filesystem search infrastructure. The `rglob` usage in `kilo_agent.py` is internal snapshot logic, not a reusable search service.

### 3.2 Memory Search

| Component | Status | Notes |
|-----------|--------|-------|
| `MemoryService.list()` | Implemented | Filter by scope, category, trust_level, source |
| `MemoryService.search()` | **NOT IMPLEMENTED** | Stage 19 deliverable |
| Full-text memory search | **NOT IMPLEMENTED** | Stage 19 deliverable |
| Semantic memory search | **NOT IMPLEMENTED** | Future |

**Finding:** Memory search does not exist yet. Stage 19 will add it.

### 3.3 Database Indexes

The `MemoryModel` has extensive indexes:
- `idx_memories_scope`, `idx_memories_category`, `idx_memories_trust`, `idx_memories_source`, `idx_memories_created_at`, `idx_memories_deleted_at`

**Finding:** These are for structured memory queries, not filesystem search.

---

## 4. Primary Architectural Question

### 4.1 Why the AI Operator Needs `files.search`

The AI Operator performs tasks on behalf of the user in a workspace. Realistic tasks require finding files:

1. **"Find the file containing X"** — locate a specific configuration or implementation file
2. **"Find all TypeScript files under src/"** — enumerate files by extension/pattern
3. **"Find where function `foo` is defined"** — locate source definitions
4. **"Find recent project files"** — discover workspace structure

Without `files.search`, the AI must use `files.list_directory` recursively and manually filter results. This is:
- Inefficient for large workspaces
- Inconvenient for the model
- Potentially expensive in tokens

### 4.2 Is `files.list_directory + files.read_file` Sufficient?

**No, not for practical AI utility.** Consider:

| Task | With list+read | With search |
|------|---------------|-------------|
| Find all `.py` files | Requires recursive list + manual filtering | Single query |
| Find files named `config` | Requires recursive list + manual filtering | Single query |
| Find `foo` in filenames | Requires recursive list + manual filtering | Single query |
| Find `bar` in file contents | Requires recursive list + read each file | Direct query |

`list_directory` returns directory entries. Searching across hundreds or thousands of files requires:
1. Multiple tool calls
2. Manual filtering by the AI
3. Token consumption proportional to directory size

A dedicated search tool is architecturally justified.

### 4.3 What Should `files.search` Search?

**V1 Recommendation: Filename/path search only.**

| Search Type | V1 | Rationale |
|-------------|----|-----------|
| Filename/path substring | **YES** | Bounded, safe, immediately useful |
| Glob/pattern (`*.py`) | **NO** | Regex/glob has DOS concerns; substring covers most use cases |
| Text content search | **NO** | High I/O cost, binary file handling, duplicates Memory Retrieval |
| Combined filename + content | **NO** | Combines disadvantages of both |

**Why not text content search in V1:**
1. **DOS risk:** Reading every file in a large workspace is unbounded I/O
2. **Binary files:** Need binary detection, encoding handling, truncation logic
3. **Duplicate functionality:** Stage 19 Memory Retrieval will include full-text search over stored knowledge
4. **Security:** Content search can leak information from files the user didn't intend to surface
5. **Complexity:** Encoding edge cases (UTF-8 BOM, UTF-16, invalid sequences) add significant complexity

**Why filename/path search is sufficient for V1:**
1. Solves the most common AI task: "find the file"
2. Bounded by directory traversal cost only
3. No content reading = no encoding/binary issues
4. Low risk — does not expose file contents
5. Can be extended to content search in a later stage

---

## 5. Proposed V1 Contract

### 5.1 Tool Definition

```python
ToolDefinition(
    tool_id="files.search",
    action="search",
    name="Search Files",
    description="Searches for files by filename/path substring within the workspace.",
    version="1.0.0",
    category=ToolCategory.FILES,
    risk_level=RiskLevel.LOW,
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Substring to match against file names and relative paths",
                "maxLength": 256,
            },
            "path": {
                "type": "string",
                "description": "Relative subdirectory to search within",
                "maxLength": 4096,
                "default": ".",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return",
                "maximum": 200,
                "default": 50,
            },
        },
        "required": ["query"],
    },
    result_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "name": {"type": "string"},
                        "type": {"type": "string", "enum": ["file", "directory", "symlink", "other"]},
                        "size": {"type": ["integer", "null"]},
                    },
                    "required": ["path", "name", "type", "size"],
                },
            },
            "truncated": {"type": "boolean"},
        },
        "required": ["query", "path", "matches", "truncated"],
    },
)
```

### 5.2 Parameter Justification

| Parameter | Why | Model-Controlled | Risk |
|-----------|-----|------------------|------|
| `query` | Search term — required to prevent unbounded listing | Yes | Low — bounded by maxLength |
| `path` | Optional subdirectory root for search | Yes | Low — validated by existing path security |
| `max_results` | Prevent unbounded output | Yes | Low — capped at 200 |

### 5.3 Result Format

```json
{
  "success": true,
  "query": "config",
  "path": ".",
  "matches": [
    {
      "path": "src/config/settings.py",
      "name": "settings.py",
      "type": "file",
      "size": 1234
    }
  ],
  "truncated": false
}
```

**No file contents are returned.** Only metadata.

---

## 6. Path-Security Model

### 6.1 Search Root Semantics

**Model A (Recommended):** Allow both workspace root and relative subdirectory.

- `path` defaults to `"."` (workspace root)
- `path` can be any relative subdirectory
- Absolute paths, `..`, drive letters, UNC paths — all rejected
- Symlinked search roots — rejected
- Missing search root — rejected with error

**Why not Model B (always workspace root)?** The AI often needs to search within `src/` or `tests/`, not the entire workspace. Limiting to root would force the AI to filter results manually.

### 6.2 Reuse Existing Path Security

All existing path-security checks from `files.read_file`, `files.list_directory`, `files.write_file`, and `files.delete_file` apply:

| Check | Implementation | Classification |
|-------|---------------|----------------|
| Absolute path rejection | `p.is_absolute() or p.drive != ""` | PROTECTED V1 |
| Literal `..` rejection | `any(part == ".." for part in p.parts)` | PROTECTED V1 |
| Resolution + containment | `target_path.relative_to(workspace_root)` | PROTECTED V1 |
| Symlink rejection (root) | `is_symlink()` + re-resolve | PROTECTED V1 |
| Directory requirement | `target_path.is_dir()` | PROTECTED V1 |

### 6.3 Symlink / Reparse Behavior

**Safe default: Do not follow symlinks during traversal.**

Implementation approach:
- When descending into subdirectories, skip symlink directories
- When matching files, report symlinks as `type="symlink"` without following
- Do not expose symlink targets in results

**Windows limitation:** Junctions/reparse points are not detected by `is_symlink()`. This is a known BEST EFFORT limitation inherited from all other filesystem tools.

---

## 7. Resource / DOS Analysis

### 7.1 Worst-Case Behavior

| Scenario | Risk | Mitigation |
|----------|------|------------|
| `node_modules` with millions of files | High | `max_results` cap + `max_files_inspected` limit |
| Deeply nested directories | Medium | `max_depth` limit (same as `list_directory`: 10) |
| Huge binary files | Low | No content reading in V1 |
| Symlink-heavy trees | Medium | Skip symlink directories |
| Repeated searches | Low | Execution time bounded by `ToolExecutionService` (30s) |

### 7.2 Safe V1 Bounds

| Resource | Bound | Rationale |
|----------|-------|-----------|
| `max_results` | 200 (hard cap) | Prevent token exhaustion from large result sets |
| `max_depth` | 10 | Same as `list_directory` |
| `max_files_inspected` | 5,000 | Prevent scanning enormous directories |
| `max_query_length` | 256 characters | Prevent pathological input |
| `max_execution_time_ms` | 30,000 | Same as global tool limit |

### 7.3 No Text Reading in V1

By design, `files.search` does NOT read file contents. This eliminates:
- Binary file handling
- Encoding detection
- Content truncation logic
- Line-by-line scanning cost

If content search is added later, it will require:
- File-size limits (e.g., 1 MiB)
- Binary detection
- Encoding fallback chain (UTF-8, UTF-8 BOM, UTF-16, latin-1)
- Truncation per file
- Separate tool or explicit mode flag

---

## 8. Generated / Special Directories

### 8.1 Should V1 Exclude Common Directories?

**No.**

V1 does NOT automatically exclude:
- `.git`
- `node_modules`
- `dist` / `build`
- `.venv` / `venv`
- caches
- temporary directories

**Rationale:**
1. Hard-coding exclusions creates hidden behavior that the AI cannot reason about
2. The user/AI can use `path` parameter to narrow search scope
3. `max_results` and `max_files_inspected` prevent runaway cost
4. Adding exclusions later is easier than removing them

**Future option:** An optional `exclude` parameter could be added in V2 if needed.

---

## 9. Text Search Strategy (Deferred)

If text-content search is added in a future stage:

| Concern | Recommended Approach |
|---------|---------------------|
| Encoding | Try UTF-8, then UTF-8 BOM, then UTF-16 LE, then latin-1 |
| Binary detection | Check for null bytes in first 4 KiB |
| File-size limit | 1 MiB per file (same as `files.read_file`) |
| Truncation | Truncate matches to 200 characters around match |
| Regex | **NOT in V1** — literal substring only |
| Case sensitivity | Case-insensitive for ASCII, case-sensitive for Unicode |
| Line limits | Return at most 5 matching snippets per file |

**Why regex is deferred:**
- Significantly more complex to implement safely
- Higher CPU cost
- Potential ReDoS if not careful
- Literal substring covers 90% of use cases

---

## 10. Performance Model

### 10.1 Filename Search Complexity

| Phase | Cost |
|-------|------|
| Directory traversal | O(number of entries) |
| Filename matching | O(entries × query_length) |
| Result collection | O(matches) |

**Worst case:** 5,000 entries inspected, 200 matches returned.

**Typical case:** 500 entries, 5 matches.

### 10.2 Comparison with Alternatives

| Approach | I/O Cost | CPU Cost | Memory | Complexity |
|----------|----------|----------|--------|------------|
| Filename search (V1) | Low | Low | Low | Low |
| Glob/pattern search | Low | Medium | Low | Medium |
| Text content search | **Very High** | Medium | Medium | High |
| Indexed search | Low (after indexing) | Low | **High** | Very High |

**Conclusion:** Filename search is the optimal V1 choice.

---

## 11. Permission / Risk Analysis

### 11.1 Risk Level

**Recommended: `LOW`**

Justification:
- Read-only operation
- Does not expose file contents
- Does not modify filesystem
- Bounded output (max 200 results)
- Same risk profile as `files.list_directory`

### 11.2 Permission Rules

| Rule | Behavior |
|------|----------|
| DENY | No search performed |
| ASK_USER | No search performed |
| ALLOW | Search performed within bounds |
| `requires_confirmation` | `False` — read-only operation |

### 11.3 Information Exposure

Search results expose:
- File names
- Relative paths
- File sizes
- File types (file/directory/symlink)

**This is the same information exposure as `files.list_directory`.** No additional sensitivity is introduced.

---

## 12. Boundary with Memory Retrieval

### 12.1 Clear Separation

| Capability | Searches | Data Source | Stage |
|------------|----------|-------------|-------|
| `files.search` | Filenames/paths in current workspace | Live filesystem | 18 (proposed) |
| Memory Retrieval | Stored memories/knowledge | SQLite `memories` table | 19 |
| Context Assembly | Composes context from multiple sources | Memory + Events + History | Post-19 |

### 12.2 Why This Matters

If `files.search` included text-content search, it would overlap with Memory Retrieval's full-text search capability. This would create:
- Duplicate functionality
- Unclear ownership of search semantics
- Potential inconsistency in result formats

**By keeping `files.search` to filename/path only:**
- `files.search` finds **where** a file is
- Memory Retrieval finds **what** is known about a topic
- Context Assembly composes both into AI context

### 12.3 The AI Manager's "File Search"

The Master Specification mentions the AI Manager "manages research tasks (web search, file search, memory search)." This refers to the AI Manager orchestrating tool calls — NOT a separate search subsystem.

`files.search` is the tool that fulfills the "file search" research task. The AI Manager will call it through the Tool Framework, just like it calls `files.read_file` or `files.write_file`.

---

## 13. Boundary with Context Assembly

Context Assembly (post-Stage 19) will:
1. Retrieve memories via Memory Retrieval
2. Gather events from the Event system
3. Read project metadata
4. Possibly call `files.search` to discover relevant files
5. Compose everything into an AI context

**`files.search` is a dependency OF Context Assembly, not a competitor.**

Context Assembly does NOT replace `files.search`. It uses `files.search` as one of its input sources.

---

## 14. Stage 18 Boundary

### 14.1 Current Stage 18 Scope

| Tool | Status |
|------|--------|
| `files.read_file` | Implemented |
| `files.list_directory` | Implemented |
| `files.write_file` | Implemented |
| `files.delete_file` | Implemented |
| `files.search` | **Deferred** |

### 14.2 Master Specification Requirement

| Source | Requirement |
|--------|-------------|
| Master Specification §27 | "5+ file tools" |
| Master Specification §27.2 Stage 18 row | "Write, copy, move, delete-to-recycle-bin, search, list" |
| Stage 17.4 corrected roadmap | "File Tool Expansion: Write, copy, move, delete-to-recycle-bin, search, list" |

### 14.3 Discrepancy

The Master Specification lists 6 capabilities for Stage 18:
1. write
2. copy
3. move
4. delete-to-recycle-bin
5. search
6. list

Currently implemented: 4 (write, list, delete, read).

The original Stage 18.1 contract proposed 4 tools and did NOT include search. This appears to be an oversight or deliberate scope reduction.

### 14.4 Recommendation

**Stage 18 should be extended with `files.search` in a follow-on sub-stage (e.g., Stage 18.5 or 18.6).**

Rationale:
1. Master Specification explicitly requires "5+ file tools"
2. `files.search` is the smallest remaining gap (copy/move are more complex)
3. Filename/path search is well-defined and bounded
4. It does not duplicate Memory Retrieval
5. It is immediately useful for AI tasks

**Copy and move are deferred to a later stage** because:
- They involve more complex semantics (overwrite, conflict resolution, atomicity)
- They have higher risk profiles
- They are less immediately critical than search

---

## 15. Alternatives Considered

### Alternative 1: Do Not Implement `files.search` Now

| Pros | Cons |
|------|------|
| No additional complexity | Stage 18 fails Master Specification's "5+ file tools" criterion |
| No additional attack surface | AI must use `list_directory` + manual filtering |
| | Inefficient for common tasks |

**Verdict:** Not recommended. The Master Specification explicitly includes search.

### Alternative 2: Implement Filename/Path Search Only (RECOMMENDED)

| Pros | Cons |
|------|------|
| Bounded, safe, immediately useful | Does not solve content search |
| Low DOS risk | |
| No overlap with Memory Retrieval | |
| Simple contract | |
| Can be extended later | |

**Verdict:** Recommended.

### Alternative 3: Implement Bounded Filename + Literal Content Search

| Pros | Cons |
|------|------|
| More powerful | Higher I/O cost |
| Solves "find where X appears" | Binary file handling complexity |
| | Encoding edge cases |
| | Overlaps with future Memory Retrieval |
| | Larger attack surface |

**Verdict:** Not recommended for V1. Defer to post-Stage 19.

### Alternative 4: Implement Indexed Search System

| Pros | Cons |
|------|------|
| Fast after indexing | High complexity |
| Supports content search | Index maintenance |
| | Not needed for V1 |

**Verdict:** Not recommended. Premature optimization.

---

## 16. Security Model

### 16.1 Protection Classifications

| Protection | Classification | Evidence |
|------------|----------------|----------|
| Absolute path rejection | PROTECTED V1 | Reuses existing path validation |
| Literal `..` rejection | PROTECTED V1 | Reuses existing path validation |
| Resolution + containment | PROTECTED V1 | Reuses existing path validation |
| Symlink rejection (root) | PROTECTED V1 | Reuses existing path validation |
| Symlink rejection (traversal) | PROTECTED V1 | Skip symlink directories during traversal |
| Directory search root | PROTECTED V1 | `target_path.is_dir()` check |
| Result bounded | PROTECTED V1 | `max_results` cap |
| Traversal bounded | PROTECTED V1 | `max_depth` + `max_files_inspected` |
| Permission boundary | PROTECTED V1 | ToolExecutionService enforces evaluation before execution |
| Audit logging | PROTECTED V1 | Fail-soft audit in all security boundaries |
| No content exposure | PROTECTED V1 | Results contain only metadata |

### 16.2 Hostile Path Testing

All existing hostile path tests from `files.read_file`, `files.list_directory`, `files.write_file`, and `files.delete_file` apply directly to `files.search`.

| Attack Class | Expected | Test |
|--------------|----------|------|
| `../outside` | BLOCKED | Reuse existing traversal test |
| `..\outside` | BLOCKED | Reuse existing traversal test |
| `/etc/passwd` | BLOCKED | Reuse existing absolute path test |
| `C:\Windows\...` | BLOCKED | Reuse existing drive path test |
| `\\server\share\...` | BLOCKED | Reuse existing UNC path test |
| Symlinked root | BLOCKED | New test (same pattern as other tools) |
| Workspace root escape | BLOCKED | Reuse existing containment test |

### 16.3 Workspace Root Protection

The search root `path` parameter is validated against the workspace root:
- Cannot be absolute
- Cannot contain `..`
- Cannot resolve outside workspace
- Cannot be a symlink
- Must be a directory

The actual workspace root comes only from `ToolExecutionContext.workspace.workspace_path`.

---

## 17. Resource / DOS Limits

### 17.1 Bounds Summary

| Resource | V1 Bound | Enforcement Point |
|----------|----------|-------------------|
| `query` length | 256 characters | Schema `maxLength` |
| `path` length | 4096 characters | Schema `maxLength` |
| `max_results` | 200 (hard cap) | Implementation |
| `max_depth` | 10 | Implementation (same as `list_directory`) |
| `max_files_inspected` | 5,000 | Implementation |
| Execution time | 30,000 ms | `ToolExecutionService` global |

### 17.2 DOS Scenarios

| Scenario | Risk | Mitigation |
|----------|------|------------|
| `node_modules` with 100K files | Medium | `max_files_inspected` stops traversal |
| Deeply nested tree (depth 100) | Medium | `max_depth` stops recursion |
| Empty query (match everything) | Medium | `query` is required |
| Query with 1M results | Medium | `max_results` caps output |
| Repeated search calls | Low | `MAX_TOOL_CALLS_PER_REQUEST` (10) |

---

## 18. Windows-Specific Analysis

| Risk | Status | Evidence |
|------|--------|----------|
| Junctions/reparse points | BEST EFFORT | `is_symlink()` returns `False` for junctions; same as other tools |
| Drive-relative paths | PROTECTED V1 | `p.drive != ""` check |
| UNC paths | PROTECTED V1 | `p.is_absolute()` check |
| Mixed separators | PROTECTED V1 | `Path` normalizes; `..` check catches traversal |
| Reserved device names | DEFERRED | Same as other tools; OS-level rejection |
| ADS paths | BEST EFFORT | `pathlib` does not expose ADS directly |

**No additional Windows-specific protection beyond existing tools is required for V1.**

---

## 19. Failure Behavior

| Scenario | Behavior |
|----------|----------|
| Missing search root | Return error, no mutation |
| Permission denied | No search performed |
| Invalid approval | No search performed |
| I/O error during traversal | Return partial results with `truncated=true`, or error if no results |
| Execution timeout | `ToolExecutionService` cancels |

**No filesystem mutation occurs. Search is read-only.**

---

## 20. Open Questions / Deferred Hardening

| Question | Status |
|----------|--------|
| Should `query` support case-insensitive matching? | Deferred to implementation |
| Should hidden files (`.git`, `.venv`) be excluded by default? | Deferred — no automatic exclusions in V1 |
| Should directory-only results be included? | Yes — `list_directory` already reports directories |
| Should symlink targets be exposed in results? | No — report as `type="symlink"` |
| Should content search be added later? | Yes — separate mode or separate tool |
| Should `max_results` be model-controlled? | Yes — bounded by hard cap |
| Should `exclude` parameter be added? | Deferred to V2 |

---

## 21. Final Recommendation

### 21.1 Implement `files.search` in Stage 18

**Scope:** Filename/path substring search only.

**Contract:**
- `query` (string, required, maxLength 256)
- `path` (string, optional, default ".", maxLength 4096)
- `max_results` (integer, optional, default 50, max 200)
- Result: `success`, `query`, `path`, `matches[]`, `truncated`

**Risk:** `LOW`

**Approval:** `requires_confirmation=False`

**Resource bounds:**
- `max_results`: 200
- `max_depth`: 10
- `max_files_inspected`: 5,000

**Security:** Reuse all existing path-security checks. No new security mechanisms needed.

### 21.2 Do NOT Implement in Stage 18

- Text-content search
- Glob/pattern matching
- Regex search
- Indexed search
- Automatic directory exclusions

### 21.3 Stage 18 Completion

After adding `files.search`, Stage 18 will have **5 tools**:
1. `files.read_file`
2. `files.list_directory`
3. `files.write_file`
4. `files.delete_file`
5. `files.search`

This satisfies the Master Specification's "5+ file tools" criterion.

**Copy and move remain deferred** to a later stage.

---

## 22. Discovery Completion

**STAGE_18_5_DISCOVERY_PASS**

Repository inspection is complete. The V1 contract is defined. Security, resource, and permission models are documented. The boundary with Memory Retrieval and Context Assembly is explicit. No production code was modified.
