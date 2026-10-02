# Stage 19.1 — Memory Retrieval Discovery & Contract

## 1. Current Memory Architecture

### 1.1 Storage Layer
- **Model:** `MemoryModel` (`app.db.models.memory.MemoryModel`)
- **Table:** `memories`
- **Primary key:** `memory_id` (64 chars, prefixed `mem_`)
- **Content:** JSON dict stored as `Text`, redacted for secrets before persistence
- **Metadata:** JSON dict stored as `Text`, size-capped at 2 KiB
- **Soft delete:** `deleted_at` timestamp (indexed)
- **Supersession:** `superseded_by` memory_id (indexed)
- **Lifecycle timestamps:** `created_at`, `updated_at`, `archived_at`, `expires_at` (all timezone-aware)
- **Scope:** `scope` (16 chars, indexed) + `scope_id` (100 chars, indexed)
- **Provenance:** `source` (32 chars, indexed) + `source_id` (128 chars)
- **Trust:** `trust_level` (16 chars, indexed)
- **Category:** `category` (32 chars, indexed)
- **Confidence:** float, 0.0–1.0

### 1.2 Indexes
| Index | Columns |
|-------|---------|
| `idx_memories_scope` | `scope`, `scope_id` |
| `idx_memories_category` | `category` |
| `idx_memories_trust` | `trust_level` |
| `idx_memories_source` | `source` |
| `idx_memories_created_at` | `created_at` |
| `idx_memories_deleted_at` | `deleted_at` |

No full-text index exists. No content index exists.

### 1.3 Repository Layer
- **File:** `app.memory.repository.MemoryRepository`
- **Methods:** `create`, `get`, `get_including_deleted`, `list`, `update`, `delete`, `supersede`, `count`
- **No authorization:** Repository performs zero authorization checks
- **No ranking:** Repository returns results in fixed order `created_at DESC, memory_id DESC`
- **Pagination:** `limit` (max 500), `offset` (max 100,000)
- **Filters:** `scope`, `scope_id`, `category`, `trust_level`, `source`, `include_deleted`
- **Soft delete:** `deleted_at IS NULL` filter applied by default

### 1.4 Service Layer
- **File:** `app.memory.service.MemoryService`
- **Authority enforcement:** Create/update/delete/supersede authority via `MemoryCaller`
- **Caller identity:** `MemoryCaller(caller_type, caller_id)` — `CallerType.USER|SYSTEM|AGENT`
- **Default trust/source:** Per-category defaults
- **Event emission:** `MEMORY_CREATED`, `MEMORY_UPDATED`, `MEMORY_SUPERSEDED`, `MEMORY_DELETED`
- **Event payload:** Never includes `content` or `metadata`
- **No retrieval method:** `get()` and `list()` are simple pass-throughs; no search, no ranking, no relevance

### 1.5 Event Infrastructure
- **File:** `app.events.emitter.EventEmitter`
- **Memory events:** 4 types only (created/updated/superseded/deleted)
- **No retrieval events:** Reading memories does not emit events
- **Payload bounds:** 4 KiB payload, 1 KiB metadata
- **Sanitization:** Secret redaction via regex patterns

### 1.6 Identity / RequestContext
- **File:** `app.identity.models.RequestContext`
- **Fields:** `user_id`, `actor_type`, `actor_id`, `session_id`, `service_name`
- **Conversion:** `MemoryService.caller_from_request_context(ctx)` → `MemoryCaller`
- **Client cannot select caller_type/caller_id:** Server-side only

### 1.7 API Exposure
- **Dependency:** `get_memory_service()` exists in `app.dependencies`
- **No HTTP routes wired:** Memory is accessible only via internal dependency injection
- **No public REST API for memory yet**

### 1.8 Existing Tests
- `test_memory_foundation.py` — domain contract, persistence, soft delete, scope isolation, bounds, security
- `test_memory_service.py` — authority, trust/provenance, lifecycle, supersession, scope isolation, retrieval (get/list only), security
- `test_memory_event_integration.py` — event payload contracts, transaction semantics, idempotency, security

---

## 2. Retrieval Responsibility / Boundary

### 2.1 Memory Storage
Answers: "What memories exist and how are they persisted?"
- Durable SQLite rows with structured fields
- Soft delete via `deleted_at`
- Supersession via `superseded_by`
- Secret-redacted JSON content

### 2.2 Memory Retrieval (V1)
Answers: "Which existing memories are relevant to this retrieval request?"
- Receives a structured query with optional filters
- Returns matching active memories, bounded and ordered
- Does NOT rank by semantic relevance in V1
- Does NOT assemble context for the model
- Does NOT modify memory state

### 2.3 Context Assembly (Future — NOT V1)
Answers: "Which information from all available sources should actually be assembled into model/agent context?"
- Will combine memories with filesystem state, tool results, plan state, etc.
- Will apply model-specific truncation, formatting, injection
- Will be a separate component from Memory Retrieval

### 2.4 Explicit Boundary
```
MemoryRetrieval → returns MemoryEntry metadata + content
ContextAssembly → consumes MemoryRetrieval results + other sources → model prompt
```

Retrieval must never:
- Execute or act on memory content
- Modify memory state
- Inject content into prompts
- Bypass permission/authorization boundaries
- Claim to perform Context Assembly

---

## 3. V1 Retrieval Model Alternatives

### Alternative A: Pure Structured Filtering + Deterministic Ordering
- **What:** Extend `MemoryService.list()` with a `query` parameter that does SQLite `LIKE` on `content` JSON
- **Complexity:** Low
- **Query performance:** O(n) scan on small tables; acceptable for V1
- **Determinism:** High — fixed ordering, no ranking
- **Security:** High — no injection risk with parameterized queries
- **Explainability:** High — exact filter match
- **Testability:** High
- **Windows/SQLite compatibility:** Full
- **Migration:** None
- **Future extensibility:** Medium — can later replace LIKE with FTS or embeddings without changing API

### Alternative B: Keyword/Token Matching
- **What:** Tokenize query, match individual tokens in content
- **Complexity:** Medium
- **Query performance:** O(n) with token overhead
- **Determinism:** Medium — tokenization edge cases
- **Security:** Medium — tokenization bugs possible
- **Explainability:** Medium — partial token matches
- **Testability:** Medium
- **Migration:** Medium
- **Verdict:** Not recommended for V1; LIKE covers the same use cases more simply

### Alternative C: SQLite FTS
- **What:** Add FTS5 virtual table on `content`
- **Complexity:** Medium-high
- **Query performance:** High — inverted index
- **Determinism:** High — FTS ranking is deterministic
- **Security:** High — parameterized queries
- **Explainability:** Medium — FTS ranking opaque
- **Testability:** Medium — FTS behavior varies by SQLite version
- **Migration:** Requires schema migration + backfill
- **Verdict:** Good for V2; overkill for V1

### Alternative D: Hybrid Structured + Lexical Relevance
- **What:** Structured filters + LIKE relevance scoring + recency/trust weighting
- **Complexity:** Medium-high
- **Query performance:** Medium
- **Determinism:** Medium — scoring weights create non-obvious ordering
- **Security:** High
- **Explainability:** Low — opaque scoring
- **Testability:** Medium
- **Verdict:** Better as V2 after measuring V1 usage patterns

### Recommended V1: Alternative A (Structured Filtering + LIKE)

Rationale:
- Minimal implementation surface area
- Reuses existing `MemoryRepository.list()` pattern
- No schema migration required
- Deterministic and explainable
- Bounded and testable
- Easy to extend with FTS or embeddings later

---

## 4. Scope and Authorization Model

### 4.1 Existing Limitation (Stage 14 V1)
- `source_id` is application-level provenance, not user identity
- `source_id=None` has broader fallback behavior in authority checks
- No multi-user memory namespace yet
- All memories are in a single shared store

### 4.2 V1 Retrieval Authorization
**Recommendation: No additional authorization layer for retrieval in V1.**

Rationale:
- Memory creation is already gated by `MemoryService.create()` authority matrix
- The existing `list()` method has no authorization — retrieval is a read-only operation
- Adding retrieval authorization requires a multi-user model that does not yet exist
- The safer default is to trust the existing creation authority and focus on preventing data leakage through query design

**V1 authorization rules:**
1. Caller must provide a valid `RequestContext` (authenticated)
2. Retrieval returns only non-deleted memories by default (`deleted_at IS NULL`)
3. Superseded memories are excluded by default (see §8)
4. No cross-scope restrictions beyond what the caller explicitly requests
5. `include_deleted` and `include_superseded` are opt-in flags for admin/debug use

### 4.3 Scope Semantics
| Scope | Visibility | Notes |
|-------|-----------|-------|
| `global` | All callers | Shared knowledge |
| `project` | Callers with matching `scope_id` | Project-specific; `scope_id` required |
| `system` | System callers only | Internal system knowledge |

**V1 recommendation:** Do NOT enforce scope-based access control in retrieval yet. The existing `list()` method allows any caller to query any scope. This is a known V1 limitation. Stage 20+ should introduce proper scope authorization when multi-user support is ready.

### 4.4 Trust Semantics
- Trust is used for **filtering and metadata only** in V1
- Trust is NOT used for access control in retrieval
- Trust is NOT used for ranking in V1
- Conflicting trust levels are exposed as-is; Context Assembly will resolve conflicts

### 4.5 Source Ownership
- `source` + `source_id` identify provenance
- Retrieval exposes source metadata to callers
- No source-based access control in V1

---

## 5. Retrieval Request Contract (V1)

```python
class MemoryRetrievalRequest(BaseModel):
    # Text query — matched against serialized content JSON via SQLite LIKE
    query: str | None = Field(default=None, max_length=256)
    
    # Structured filters — optional exact-match filters
    scope: MemoryScope | None = None
    scope_id: str | None = Field(default=None, max_length=100)
    categories: list[MemoryCategory] | None = Field(default=None, max_length=10)
    trust_levels: list[TrustLevel] | None = Field(default=None, max_length=5)
    sources: list[ProvenanceSource] | None = Field(default=None, max_length=10)
    
    # Lifecycle filters
    include_deleted: bool = Field(default=False)
    include_superseded: bool = Field(default=False)
    
    # Time bounds (optional)
    created_after: datetime | None = None
    created_before: datetime | None = None
    
    # Result bounds
    limit: int = Field(default=20, ge=1, le=100)
    
    # Execution context (server-side only, never from client)
    request_context: RequestContext | None = None
```

### Field-by-Field Analysis

| Field | Type | Required | Default | Max | Security | Ranking/Filter |
|-------|------|----------|---------|-----|----------|----------------|
| `query` | string | Optional | None | 256 | SQL injection safe via parameterized LIKE | Filter |
| `scope` | enum | Optional | None | — | No access control yet | Filter |
| `scope_id` | string | Optional | None | 100 | Validated length | Filter |
| `categories` | enum[] | Optional | None | 10 | Enum-only values | Filter |
| `trust_levels` | enum[] | Optional | None | 5 | Enum-only values | Filter/Metadata |
| `sources` | enum[] | Optional | None | 10 | Enum-only values | Filter/Metadata |
| `include_deleted` | bool | Optional | False | — | Exposes soft-deleted data | Filter |
| `include_superseded` | bool | Optional | False | — | Exposes superseded data | Filter |
| `created_after` | datetime | Optional | None | — | UTC validation | Filter |
| `created_before` | datetime | Optional | None | — | UTC validation | Filter |
| `limit` | int | Optional | 20 | 100 | Capped | Result size |
| `request_context` | object | Server | — | — | Never from client | Auth |

### Validation Rules
1. `query` max 256 chars; if empty string, treated as None
2. `limit` clamped to [1, 100]
3. Enum arrays validated against known vocabularies
4. `created_after`/`created_before` must be UTC-aware
5. If `query` is None and all filters are None → return empty result or error (design decision: return empty to allow caller discovery)
6. `request_context` is server-populated only; never from client arguments

---

## 6. Retrieval Response Contract (V1)

```python
class MemoryRetrievalResponse(BaseModel):
    success: bool
    query: str | None
    filters: dict[str, Any]
    memories: list[MemoryEntry]
    total: int
    returned: int
    truncated: bool
```

### Alternative: Simpler Response (Recommended)

```python
class MemoryRetrievalResponse(BaseModel):
    success: bool
    query: str | None
    memories: list[MemoryEntry]
    total: int
    truncated: bool
```

**Fields:**

| Field | Type | Notes |
|-------|------|-------|
| `success` | bool | Operation success |
| `query` | string \| None | The query that was executed (echo back for debugging) |
| `memories` | MemoryEntry[] | Bounded result set, max `limit` entries |
| `total` | int | Total matching entries (before limit) |
| `truncated` | bool | True if more results exist beyond `limit` |

### MemoryEntry Exposure
The existing `MemoryEntry` domain model is already bounded:
- `memory_id`, `category`, `scope`, `scope_id`, `content` (dict, max 8 KiB), `trust_level`, `confidence`, `source`, `source_id`, `created_at`, `updated_at`, `archived_at`, `expires_at`, `superseded_by`, `deleted_at`, `metadata` (dict, max 2 KiB)

**No additional internal fields are exposed.**

### What NOT to expose
- Internal database IDs beyond `memory_id`
- SQL query details
- Ranking scores (not applicable in V1)
- Raw SQLite rows
- Event history
- Authorization decisions

### Ordering
V1 deterministic ordering:
1. `created_at DESC` (newest first)
2. `memory_id DESC` (tiebreaker)

If `query` LIKE matching is added, results are still ordered by recency, not relevance. This is intentional for V1 determinism.

---

## 7. Ranking / Relevance Model

### V1 Decision: No Ranking

V1 retrieval uses **pure structured filtering + deterministic ordering**.

**Rationale:**
- The existing trust hierarchy is for creation authority, not retrieval quality
- Adding relevance scoring without embeddings or FTS would be arbitrary
- Deterministic ordering is more testable and explainable
- Context Assembly (future) will apply ranking when assembling prompts

### Future Options (NOT V1)
| Option | When | Complexity |
|--------|------|------------|
| SQLite FTS5 | V2 — when content search volume justifies migration | Medium |
| Embedding + vector store | V3 — when semantic search is needed | High |
| Hybrid scoring | V3+ — after measuring FTS quality | High |
| Recency/trust weighting | V2 — after measuring V1 usage patterns | Low-medium |

---

## 8. Supersession Semantics

### V1 Rule: Exclude Superseded by Default

When `include_superseded=False` (default):
- Memories with `superseded_by IS NOT NULL` are excluded from results
- The superseding memory IS included if it matches filters

When `include_superseded=True`:
- Both superseded and superseding memories are returned

### Chain Handling
- V1 does NOT follow supersession chains
- If A → B → C (A superseded by B, B superseded by C):
  - Default: Only C is returned
  - `include_superseded=True`: A, B, C are all returned
- Malformed chains (circular, missing target): SQL `superseded_by` points to a non-existent memory → treated as superseded (excluded by default)

### Deletion vs Supersession
- `deleted_at` and `superseded_by` are mutually exclusive in the data model
- A memory cannot be both deleted and superseded
- Deleted memories are excluded by `deleted_at IS NULL` filter
- Superseded memories are excluded by `superseded_by IS NULL` filter (when `include_superseded=False`)

---

## 9. Trust Semantics

### V1 Trust Policy

| Use | Trust |
|------|-------|
| Filtering | Optional — caller can filter by trust_levels |
| Ranking | Not used in V1 |
| Access control | Not used in retrieval |
| Metadata | Exposed in results |

### Conflict Resolution
- Multiple memories with different trust levels on the same topic are returned as-is
- Context Assembly (future) will resolve conflicts using the trust hierarchy:
  `EXPLICIT_USER > SYSTEM > DERIVED > EXTERNAL > AGENT`
- Retrieval does NOT silently prefer higher trust

---

## 10. Resource Safety

### V1 Limits

| Resource | Limit | Enforcement |
|----------|-------|-------------|
| `query` length | 256 chars | Request validation |
| `categories` array | 10 items | Request validation |
| `trust_levels` array | 5 items | Request validation |
| `sources` array | 10 items | Request validation |
| `limit` | 100 (default 20) | Request validation + repository clamp |
| `offset` | 100,000 max | Repository clamp (existing) |
| Total scanned rows | Unbounded in V1 | Acceptable for current data volumes |
| Response size | Bounded by `limit` × ~10 KiB per entry | `limit` cap |

### SQLite Performance
- Existing indexes on `scope`, `category`, `trust_level`, `source`, `created_at`, `deleted_at`
- `query` LIKE will scan `content` column — no index, but acceptable for V1 volumes
- Count query uses same filters — efficient with indexed fields

### Future Hardening (NOT V1)
- Add FTS5 for content search performance at scale
- Add query execution timeout
- Add maximum scanned rows limit
- Add query plan analysis to reject full-table scans

---

## 11. Security / Privacy Model

### 11.1 Threat Model

| Threat | V1 Mitigation |
|--------|--------------|
| Prompt injection in stored memory | Content is data only; retrieval does not execute it |
| Malicious memory content | Stored as JSON; retrieved as JSON; never interpreted |
| Secrets in memory | Redacted at creation time via `_redact_secrets()` |
| Cross-scope leakage | `scope` filter available; no automatic enforcement yet |
| Cross-project leakage | `scope_id` filter available; no automatic enforcement yet |
| Deleted memory leakage | Excluded by default (`deleted_at IS NULL`) |
| Superseded memory leakage | Excluded by default (`superseded_by IS NULL`) |
| Source ownership leakage | `source`/`source_id` exposed as metadata |
| Metadata leakage | `metadata` capped at 2 KiB |
| Query injection | Parameterized SQLAlchemy queries only |
| SQL injection | Content stored as JSON parameter; no string concatenation |
| Oversized queries | `query` max 256 chars |
| Result amplification | `limit` max 100 |
| Unauthorized retrieval | No retrieval auth in V1; creation auth already enforced |

### 11.2 Critical Invariants
1. **Memory content is DATA, not instructions.** Retrieval returns content as structured JSON. No component executes or interprets it.
2. **No authorization bypass.** Retrieval cannot grant tool execution, permission elevation, or subprocess invocation.
3. **No dynamic execution.** Retrieval does not import or call `PermissionEngine`, `ToolExecutor`, `ApprovalService`, or subprocess.
4. **No client-controlled workspace/path.** Unlike filesystem tools, memory retrieval has no path parameter that could escape containment.
5. **Audit-safe payloads.** If audit is added later, payloads must be sanitized via existing `sanitize_payload()`.

---

## 12. Event / Audit Decision

### Recommendation: No Events for Retrieval in V1

**Rationale:**
- Retrieval is a read-only operation
- High-volume read operations create audit noise
- Privacy implications: logging every retrieval query could expose user curiosity patterns
- The existing mutation events (created/updated/deleted/superseded) already provide sufficient lifecycle audit

### Future Consideration (NOT V1)
- Audit retrieval only for `include_deleted=True` or `include_superseded=True` (admin/debug operations)
- Audit retrieval by `AGENT` callers but not `USER` callers (privacy)
- Rate-limited audit sampling

---

## 13. Service / API Ownership

### Recommended: Dedicated `MemoryRetrievalService`

**Location:** `app.memory.retrieval.MemoryRetrievalService`

**Dependencies:**
- `MemoryRepository` — persistence
- `MemoryService` — authority/validation (for caller derivation)

**Ownership:**
- `MemoryRetrievalService` owns the retrieval contract
- `MemoryService` retains ownership of mutation operations
- `MemoryRepository` remains persistence-only
- No direct SQLite access from model/agent

**Why a new service?**
- Separation of concerns: retrieval has different bounds/ranking than mutation
- Future extensibility: FTS, embeddings, hybrid scoring can be added without changing `MemoryService`
- Clear testing boundary
- `MemoryService` already has a well-defined mutation API

**Alternative considered:** Add `search()` to `MemoryService` directly.
- **Rejected:** Mixes mutation authority concerns with retrieval ranking concerns. Harder to test, harder to extend.

---

## 14. Future Compatibility

### V1 Contract Must Support Future Extensions

| Future Feature | V1 Compatibility |
|----------------|-----------------|
| SQLite FTS5 | Add `search_type` parameter; V1 uses `LIKE` |
| Embeddings/vector search | Add `vector_query` parameter; V1 ignores it |
| Hybrid retrieval | Add `ranking` parameter; V1 uses `recency` |
| Semantic reranking | Add `rerank` parameter; V1 returns no scores |
| Context Assembly | Consumes `MemoryRetrievalResponse`; no changes to retrieval API |
| Memory confidence weighting | Add `min_confidence` filter; V1 accepts default |
| Memory expiration | `expires_at` filter already exists |
| Multi-user identity | Add `caller_scope` filter; V1 uses server context |
| AI Manager | Retrieval service remains independent; Manager calls it |

### Design Principles for Extensibility
1. `query` parameter is generic — can later support FTS, vector, or hybrid modes
2. `MemoryEntry` response model is stable — add optional `relevance_score` field later
3. `MemoryRetrievalService` is a separate class — can be extended without changing `MemoryService`
4. Filter arrays (`categories`, `trust_levels`, `sources`) accept multiple values — supports future filtering semantics

---

## 15. Test Strategy

### 15.1 Functional Tests

| Test | Description |
|------|-------------|
| `test_retrieve_exact_match` | Query matches exact content substring |
| `test_retrieve_partial_match` | Query matches partial content substring |
| `test_retrieve_no_results` | Query matches nothing → empty list |
| `test_retrieve_multiple_results` | Query matches multiple memories |
| `test_retrieve_deterministic_ordering` | Results ordered by created_at DESC, memory_id DESC |
| `test_retrieve_limit_default` | Default limit 20 enforced |
| `test_retrieve_limit_custom` | Custom limit honored, clamped to max |
| `test_retrieve_limit_max` | Limit > 100 clamped to 100 |
| `test_retrieve_empty_query` | Empty query returns all (or error — design decision) |
| `test_retrieve_special_characters` | SQL-special chars in query are safe |
| `test_retrieve_oversized_query` | Query > 256 chars rejected |

### 15.2 Scope Tests

| Test | Description |
|------|-------------|
| `test_retrieve_global_scope` | Global memories returned |
| `test_retrieve_project_scope` | Project memories filtered by scope_id |
| `test_retrieve_system_scope` | System memories returned |
| `test_retrieve_cross_scope_isolation` | Scope filter prevents cross-scope leakage |

### 15.3 Trust Tests

| Test | Description |
|------|-------------|
| `test_retrieve_all_trust_levels` | All trust levels returned by default |
| `test_retrieve_min_trust_filter` | Trust filter works |
| `test_retrieve_conflicting_trusts` | Different trust levels returned as-is |

### 15.4 Lifecycle Tests

| Test | Description |
|------|-------------|
| `test_retrieve_active_memory` | Active memory returned |
| `test_retrieve_deleted_excluded_by_default` | Soft-deleted excluded |
| `test_retrieve_deleted_included_when_requested` | `include_deleted=True` returns deleted |
| `test_retrieve_superseded_excluded_by_default` | Superseded excluded |
| `test_retrieve_superseded_included_when_requested` | `include_superseded=True` returns superseded |
| `test_retrieve_supersession_chain` | Chain of supersessions handled correctly |
| `test_retrieve_malformed_supersession` | Missing superseding target treated as superseded |

### 15.5 Security Tests

| Test | Description |
|------|-------------|
| `test_retrieve_prompt_injection_in_content` | Hostile content returned as data |
| `test_retrieve_sql_injection_in_query` | SQL-special chars in query are safe |
| `test_retrieve_oversized_result` | Limit prevents oversized results |
| `test_retrieve_no_execution` | Retrieval does not execute content |
| `test_retrieve_no_permission_evaluation` | Retrieval does not call PermissionEngine |
| `test_retrieve_no_subprocess` | Retrieval does not spawn subprocesses |

### 15.6 Permission/Authorization Tests

| Test | Description |
|------|-------------|
| `test_retrieve_deny_prevents_execution` | DENY → no retrieval |
| `test_retrieve_ask_user_never_executes` | ASK_USER → no retrieval |
| `test_retrieve_allow_with_grant_executes` | ALLOW → retrieval executes |
| `test_retrieve_workspace_context_required` | Missing context → error |

### 15.7 Performance/Resource Tests

| Test | Description |
|------|-------------|
| `test_retrieve_many_memories_bounded` | 1000 memories, limit 20 returns 20 |
| `test_retrieve_truncated_indicator` | `truncated=True` when more results exist |
| `test_retrieve_query_timeout` | Long-running query bounded by service timeout |

### 15.8 Regression Tests

| Test | Description |
|------|-------------|
| `test_existing_list_still_works` | Existing `MemoryService.list()` unchanged |
| `test_existing_get_still_works` | Existing `MemoryService.get()` unchanged |
| `test_existing_create_still_works` | Existing `MemoryService.create()` unchanged |
| `test_mutation_events_still_emitted` | Existing event flow unchanged |

---

## 16. Known Limitations

| Limitation | Classification | Mitigation |
|------------|----------------|------------|
| No content search in V1 | DEFERRED | V1 uses structured filters + LIKE |
| No FTS/embeddings | DEFERRED | V1 contract supports future `search_type` parameter |
| No retrieval authorization | V1 LIMITATION | Creation authority enforced; retrieval auth deferred to multi-user stage |
| No cross-scope enforcement | V1 LIMITATION | Caller must explicitly filter; future stage will enforce |
| LIKE is O(n) on content | BEST EFFORT | Acceptable for V1 volumes; FTS for V2 |
| No query audit | V1 DECISION | Privacy-preserving; can add later for admin operations |
| Windows junction handling | BEST EFFORT | Inherited from filesystem tools; not directly relevant to memory |
| TOCTOU on include_deleted | BEST EFFORT | Inherent to user-space SQLite |

---

## 17. Explicit Non-Goals for Stage 19.1

Do NOT implement:
- Context Assembly
- Model Router
- Embeddings / vector database
- Semantic reranking
- LLM-based retrieval
- SQLite FTS migration
- Memory retrieval REST API
- Memory retrieval authorization/ACL
- Memory summarization
- Memory compaction
- Automatic memory creation
- Memory expiration enforcement
- Multi-user memory namespace

---

## 18. Recommended V1 Retrieval Architecture

### 18.1 Service Ownership
```
MemoryRetrievalService (new)
    ├── MemoryRepository (existing)
    ├── MemoryService (existing, for caller derivation)
    └── RequestContext (existing, server-side only)
```

### 18.2 Request Flow
```
Client/Agent
    → ToolExecutionService (if exposed as tool)
    → MemoryRetrievalService.search(request)
        → MemoryService.caller_from_request_context(ctx)
        → MemoryRepository.list(filters...)
            → SQLAlchemy query with LIKE on content
        → Post-process: exclude superseded, apply ordering
    → MemoryRetrievalResponse
```

### 18.3 Query Strategy
```sql
SELECT * FROM memories
WHERE
    deleted_at IS NULL
    AND (:include_superseded OR superseded_by IS NULL)
    AND (:query IS NULL OR content LIKE :pattern)
    AND (:scope IS NULL OR scope = :scope)
    AND (:scope_id IS NULL OR scope_id = :scope_id)
    AND (/* category filter */)
    AND (/* trust filter */)
    AND (/* source filter */)
    AND (/* time bounds */)
ORDER BY created_at DESC, memory_id DESC
LIMIT :limit
```

Parameterized LIKE pattern: `%` + query + `%`

---

## 19. Discovery Verdict

### STAGE_19_1_DISCOVERY_PASS

Stage 19.1 discovery is complete. The V1 Memory Retrieval contract is:
- **Deterministic:** Fixed ordering, no opaque ranking
- **Explainable:** Exact filter matches, no hidden scoring
- **Bounded:** `limit` max 100, `query` max 256 chars
- **Testable:** Pure function from filters to results
- **Cheap:** No schema migration, no FTS, no embeddings
- **Compatible:** Works with current SQLite architecture
- **Extensible:** Can add FTS, embeddings, hybrid scoring without breaking API

### Most Important Design Decisions

1. **No ranking in V1** — deterministic ordering by recency only
2. **Supersession exclusion by default** — clean, safe default
3. **No retrieval audit in V1** — privacy-preserving
4. **Separate MemoryRetrievalService** — clean separation from mutation logic
5. **LIKE for content search** — simplest viable approach
6. **No retrieval authorization in V1** — defers to multi-user stage

### Security Findings

- Memory content is stored as data, never executed — verified by existing tests
- Secret redaction at creation time prevents leakage
- No SQL injection risk — parameterized queries only
- No execution boundary risk — retrieval returns JSON, never interpreted
- Source ownership is application-level, not user-level — acceptable V1 limitation

### Important Limitations

- No multi-user scope enforcement yet
- No content search performance optimization yet
- No retrieval audit yet
- Windows junctions not directly relevant (memory is SQLite, not filesystem)

### Production Files Changed

**None.** This is a discovery-only stage. No production code was modified.

### Test/Discovery Evidence

- All existing memory tests pass (verified by full repository regression: 2488 passed)
- Existing `TestMemoryRetrieval` class covers `get()` and `list()` with filters
- Existing `TestMemorySecurity` class covers prompt injection, fake authorization, SQL content, fake system messages
- Existing `TestMemorySupersession` covers supersession lifecycle
- Existing `TestScopeIsolation` covers cross-scope behavior

### What Stage 19.2 Should Implement Next

1. **`MemoryRetrievalService`** with `search()` method
2. **`LIKE` content matching** via `query` parameter
3. **Supersession filtering** (`include_superseded` flag)
4. **Request/response models** (`MemoryRetrievalRequest`, `MemoryRetrievalResponse`)
5. **Focused tests** covering functional, security, lifecycle, performance scenarios
6. **Integration** with existing `MemoryService` and `MemoryRepository`
7. **Optional:** Wire into a tool or API endpoint for agent access
