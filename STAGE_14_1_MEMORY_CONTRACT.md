# Stage 14.1 — Memory Foundation Contract

**Baseline:** `366ddad` — checkpoint: stages 10-13 accepted working tree
**Date:** 2026-09-24
**Status:** STRATEGIC DISCOVERY ONLY — NO IMPLEMENTATION, NO CODE CHANGES

---

## 1. Memory Definition

Memory is durable information the AI Operator needs across sessions to act as a *personal* operating system.

Memory is **NOT**:
- A record of what happened (that is Event/History)
- The current state of an executing task (that is Task state machine)
- The current state of a scheduled job (that is Scheduler state)
- Authorization state (that is PermissionEngine/ApprovalService)
- A user-facing message (that is Notification)
- A transient execution artifact (that is in-memory orchestration state)

Memory is **knowledge the system retains**: user instructions, project facts, learned preferences, decision rationale, and agent observations that should survive process restarts.

**Core principle:** Memory stores *what is known*. Events store *what happened*. These are complementary but distinct.

---

## 2. Memory vs Execution State

### 2.1 Responsibility Matrix

| Component | Execution State? | Belongs in Memory? | Needs Durable Execution Records? | Event/History Sufficient? | Should Remain Ephemeral? |
|-----------|-----------------|-------------------|----------------------------------|--------------------------|------------------------|
| `BackgroundExecutionManager` | YES | NO | YES (Stage 16) | NO | Currently ephemeral; must become durable |
| `PlanExecutionState` | YES | NO | YES (Stage 16) | NO | Currently ephemeral; must become durable |
| `RunStatus` / `AutonomousRunResult` | YES | NO | YES (Stage 16) | NO | Currently ephemeral; must become durable |
| `DevelopmentTask` / `TaskStateMachine` | YES | NO | Already persisted | Partially | Execution state; stays in task tables |
| `KiloSession` state | YES | NO | Already persisted | Partially | Execution state; stays in kilo_sessions |
| `Scheduler` occurrence state | YES | NO | Already persisted | Partially | Execution state; stays in scheduled_jobs |
| `Event` / `History` | NO | NO | N/A | YES — authoritative | Append-only audit trail |
| `PermissionGrant` | NO | NO | N/A | YES | Authorization state |
| `PendingApproval` / `CapabilityApproval` | NO | NO | N/A | YES | Authorization state |
| `VerificationReport` | YES | NO | YES (Stage 16) | NO | Currently ephemeral; must become durable |
| `Notification` | NO | NO | N/A | Derived from Events | User-facing message |
| `AppSetting` | NO | PARTIAL | N/A | NO | Some settings ARE user preferences → Memory |
| `Conversation` / `Message` | NO | PARTIAL | N/A | NO | Chat history is execution artifact; important context may become Memory |
| `DevelopmentResult` | YES | NO | YES (Stage 16) | NO | Currently ephemeral; must become durable |

### 2.2 Critical Distinctions

**Execution State vs Memory:**
- Execution state tracks *what is currently happening* and transitions through well-defined state machines.
- Memory tracks *what the system knows* independently of any current execution.
- A task can be COMMITTED (execution terminal) while its decisions and outcomes populate Memory.

**Event/History vs Memory:**
- Events are immutable, append-only, and ordered by `occurred_at`.
- Memory is mutable (with audit trail), indexed by relevance/scope/trust, and optimized for retrieval.
- An Event records that "task X failed because Y." A Memory entry records "project Z uses framework W" or "user prefers approach V."
- Events can *trigger* memory creation, but are not themselves memory.

**Settings vs Memory:**
- `AppSetting` is a flat key-value store for application configuration.
- Memory is structured, scoped, provenance-tracked, and trust-classified.
- Some settings (UI preferences, autonomy mode) are effectively memory and should be migrated to Memory in V2 or later.
- V1 Memory does NOT replace Settings; they coexist.

---

## 3. Memory Categories

V1 supports the following categories. Each is application-defined and model-independent.

| Category | Description | Example | Trust Default |
|----------|-------------|---------|---------------|
| `user_instruction` | Explicit user-stated preference, rule, or constraint | "Always run tests before commit" | EXPLICIT_USER |
| `project_knowledge` | Fact learned about a project | "Project uses React + TypeScript" | DERIVED |
| `system_knowledge` | System-discovered fact about environment | "Ollama is installed at /usr/local/bin" | SYSTEM |
| `agent_observation` | Fact observed by an agent during execution | "Tests fail on Python 3.12 but pass on 3.11" | AGENT |
| `decision_rationale` | Why a specific decision was made | "Chose Ollama over OpenRouter for cost" | DERIVED |
| `contextual` | Temporary but important context | "User is currently refactoring auth module" | AGENT |

**Deferred categories (not V1):**
- `external_knowledge` — imported from outside systems
- `agent_identity` — agent self-knowledge (deferred to Agent Teams)
- `skill_knowledge` — knowledge about available tools/capabilities

**Rule:** Categories are application-owned enums. The model cannot create new categories or modify category definitions.

---

## 4. Scope Model

V1 supports three scopes. Multi-user tenancy is explicitly excluded.

| Scope | Description | Example | V1? |
|-------|-------------|---------|-----|
| `global` | Applies to the entire user/system | "Default autonomy mode is manual" | YES |
| `project` | Applies to a specific project | "Project X uses pytest" | YES |
| `system` | System-discovered environment fact | "Git config user.name is 'Alice'" | YES |

**Deferred scopes (not V1):**
- `task` — task-specific context (deferred; task metadata is sufficient for V1)
- `agent` — agent-specific knowledge (deferred to Agent Teams)
- `user` — multi-user tenancy (explicitly excluded; this is a private desktop app)

**Scope rules:**
- `global` memories have no `scope_id`.
- `project` memories require `scope_id` = `project_id`.
- `system` memories have no `scope_id` (or optional `scope_id` for namespacing).
- Memory queries filter by scope. A `global` memory is always visible; a `project` memory is visible only when operating within that project.

---

## 5. Provenance and Trust Model

### 5.1 Provenance Fields

Every Memory entry MUST contain:

| Field | Description | Source |
|-------|-------------|--------|
| `memory_id` | Application-generated primary key | System |
| `category` | Memory category enum | Application |
| `scope` | Scope enum (`global`, `project`, `system`) | Application |
| `content` | The actual knowledge (JSON/dict) | Varies |
| `trust_level` | Trust classification | Application |
| `confidence` | Float 0.0–1.0, application-assessed | Application |
| `source` | Provenance source enum | Application |
| `source_id` | Specific source identifier | Varies |
| `created_at` | UTC timestamp | System |
| `updated_at` | UTC timestamp | System |
| `superseded_by` | Memory ID of the entry that supersedes this one | System |
| `archived_at` | UTC timestamp when archived | System |
| `expires_at` | UTC timestamp when expired | System |
| `deleted_at` | UTC timestamp when deleted (soft delete) | System |

### 5.2 Trust Levels

Trust levels are application-assigned and immutable after creation. They are NOT user-editable.

| Trust Level | Description | Can Be Created By | Can Override Lower Trust? |
|-------------|-------------|-------------------|--------------------------|
| `EXPLICIT_USER` | User explicitly stated this fact | User only | YES |
| `SYSTEM` | System-generated fact | System services | NO (cannot override EXPLICIT_USER) |
| `DERIVED` | Inferred from events, tasks, or data | System services, AI Manager | NO |
| `EXTERNAL` | Imported from external source | System services (with user consent) | NO |
| `AGENT` | Observed by agent during execution | Agents, AI Manager | NO |

**Critical rule:** Lower trust can NEVER silently override higher trust. If an `AGENT` memory conflicts with an `EXPLICIT_USER` memory in the same scope/category, the `EXPLICIT_USER` memory remains authoritative. Retrieval layers (future) must enforce this; the Memory store itself does not resolve conflicts.

### 5.3 Provenance Sources

| Source | Description | Example |
|--------|-------------|---------|
| `user` | User explicitly provided | User typed a preference in UI |
| `system` | System-generated | Auto-detected Ollama path |
| `event` | Derived from an Event | Task failure pattern learned from events |
| `task` | Derived from task outcome | "Tests pass after running pytest" |
| `agent` | Agent-observed | Agent noticed a file pattern |
| `external` | Imported from outside | Imported from config file |

**Rule:** "AI believes this" is NEVER a provenance source. AI-generated content must flow through `agent` or `derived` trust levels, which are lower than `EXPLICIT_USER`.

---

## 6. Memory Lifecycle

### 6.1 States and Transitions

```
ACTIVE → ARCHIVED → EXPIRED → DELETED
   ↓         ↓          ↓
   └─────────┴──────────┘ (via explicit deletion)
   ↓
SUPERSEDED (by another memory)
```

| State | Meaning | Allowed Transitions |
|-------|---------|---------------------|
| `ACTIVE` | Normal, retrievable memory | → ARCHIVED, EXPIRED, SUPERSEDED, DELETED |
| `ARCHIVED` | No longer actively used but retained | → ACTIVE, EXPIRED, DELETED |
| `EXPIRED` | Time-bound memory that reached `expires_at` | → DELETED |
| `SUPERSEDED` | Replaced by a newer memory with `superseded_by` | → DELETED |
| `DELETED` | Hard-deleted (or tombstone for audit) | Terminal |

**V1 simplification:** Only `ACTIVE`, `SUPERSEDED`, and `DELETED` are required. `ARCHIVED` and `EXPIRED` are deferred to V2. V1 memories are either active, superseded, or deleted.

### 6.2 Lifecycle Operations

| Operation | Description | Authorization |
|-----------|-------------|---------------|
| `create` | Create new memory entry | Write authority (see §8) |
| `update` | Modify content of ACTIVE memory | Original author or user |
| `supersede` | Create new memory, mark old as SUPERSEDED | Write authority |
| `archive` | Mark as ARCHIVED | System or user |
| `expire` | Mark as EXPIRED based on `expires_at` | System (time-based) |
| `delete` | Hard delete or soft delete | User can delete any; system can delete own |

### 6.3 Supersession

When a memory is superseded:
1. New memory is created with `superseded_by` pointing to the old memory's ID.
2. Old memory is marked `SUPERSEDED`.
3. Both remain queryable for audit.
4. Retrieval layers (future) exclude SUPERSEDED memories by default.

### 6.4 Deduplication

V1 does NOT implement automatic deduplication. If the same fact is entered twice, both entries exist. Retrieval layers (future) may deduplicate. This is intentional: deduplication logic is complex and may incorrectly merge distinct memories.

---

## 7. Write Authority

### 7.1 Authorized Writers

| Actor | Can Create | Can Update | Can Delete | Can Supersede |
|-------|-----------|------------|------------|--------------|
| **User** | Any category, any scope | Any memory they created | Any memory | Any memory |
| **System services** | `system_knowledge`, `contextual` (system scope) | Own memories | Own memories | Own memories |
| **AI Manager** | `agent_observation`, `decision_rationale`, `contextual` | Own memories | Own memories | Own memories |
| **Agents** | `agent_observation`, `contextual` | Own memories | Own memories | Own memories |

### 7.2 Prohibited Writes

- **No actor** can create `EXPLICIT_USER` memories except the User.
- **No actor** can update or delete `EXPLICIT_USER` memories except the User.
- **AI Manager and agents** cannot create or modify `user_instruction` memories.
- **AI-generated content** must never be written as `EXPLICIT_USER` or `SYSTEM` trust level.

### 7.3 Minimum V1 Authorization Boundary

The Memory service enforces write authority at the service layer. All writes go through `MemoryService`, which:
1. Validates the caller's authority for the requested operation.
2. Assigns trust level based on caller and category.
3. Records provenance (`source`, `source_id`).
4. Emits a memory event for audit.

The Memory service does NOT grant execution authority. Writing a memory does not change task state, permissions, approvals, or verification state.

---

## 8. Storage Design

### 8.1 V1 Storage Model

Use existing SQLite/SQLAlchemy infrastructure. Single table `memories`.

**Why not JSON-only or event-only:**
- Events are append-only and immutable. Memory requires updates, supersession, and deletion.
- A pure JSON document store would lose the ability to query by scope, category, trust, and provenance.
- Normalized relational records enable the query patterns needed for retrieval (future).

**Why not vector database:**
- V1 does not require semantic search. Simple key-value retrieval by scope/category/trust is sufficient.
- Vector databases are deferred until memory volume justifies it.

### 8.2 Memory Table Schema

```sql
CREATE TABLE IF NOT EXISTS memories (
    memory_id VARCHAR(64) PRIMARY KEY,
    category VARCHAR(32) NOT NULL,
    scope VARCHAR(16) NOT NULL,
    scope_id VARCHAR(100),
    content TEXT NOT NULL,
    trust_level VARCHAR(16) NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    source VARCHAR(32) NOT NULL,
    source_id VARCHAR(128),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    archived_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    superseded_by VARCHAR(64),
    deleted_at TIMESTAMP WITH TIME ZONE,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_trust ON memories(trust_level);
CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_deleted_at ON memories(deleted_at);
```

**Field semantics:**
- `memory_id`: Application-generated, e.g., `mem_<uuid>`.
- `category`: One of the V1 category enums.
- `scope`: `global`, `project`, or `system`.
- `scope_id`: For `project` scope, the `project_id`. For others, NULL.
- `content`: JSON-serialized dict. Application-owned structure per category. Bounded to 8KB.
- `trust_level`: One of the trust level enums.
- `confidence`: Float 0.0–1.0. Application-assessed.
- `source`: Provenance source enum.
- `source_id`: Specific source identifier (e.g., task_id, event_id, conversation_id).
- `created_at`, `updated_at`: UTC timestamps.
- `archived_at`, `expires_at`, `deleted_at`: Lifecycle timestamps. NULL if not applicable.
- `superseded_by`: `memory_id` of the superseding entry. NULL if not superseded.
- `metadata`: Optional JSON for category-specific fields. Bounded to 2KB.

### 8.3 Soft Delete

V1 uses soft delete (`deleted_at` timestamp). The row remains in the database for audit but is excluded from all normal queries. Hard delete is a maintenance operation outside V1 scope.

---

## 9. Security Model

### 9.1 Memory is Data, Not Authority

Memory entries are passive data. They:
- Do NOT grant execution authority.
- Do NOT bypass PermissionEngine.
- Do NOT modify Task/Plan/Scheduler state.
- Do NOT authorize tool execution.
- Do NOT affect VerificationGate outcomes.

### 9.2 Injection Protection

- Memory `content` is stored as JSON but is NOT interpreted as executable code.
- Memory entries are never passed to subprocess, eval, exec, or dynamic imports.
- Future retrieval layers must sanitize memory content before incorporating it into prompts.
- The Memory store itself does not build LLM prompts.

### 9.3 Secret Protection

- The Memory service MUST NOT accept entries where `content` contains known secret patterns (API keys, tokens, passwords).
- The service applies the same redaction patterns as `EventSanitizer` before persistence.
- If content cannot be safely redacted, the write is rejected.
- This is a V1 requirement, not deferred.

### 9.4 Unauthorized Modification

- All mutations go through `MemoryService`.
- The service enforces write authority based on caller identity and memory trust level.
- Direct repository access to `memories` table is prohibited outside the service layer.

### 9.5 Scope Isolation

- `project`-scoped memories are only visible when operating within that project.
- `global` and `system` memories are always visible.
- There is NO cross-user isolation (single-user application).

### 9.6 Stale/Conflicting Memories

- Superseded memories are marked but not deleted.
- Conflicting memories at different trust levels coexist; retrieval layers resolve conflicts.
- The Memory store does not resolve conflicts. It returns all matching entries with their trust levels.

---

## 10. Event/History Integration

### 10.1 Memory Events

The following EventTypes are added for Memory audit:

| EventType | Description | Payload |
|-----------|-------------|---------|
| `memory.created` | New memory entry created | `memory_id`, `category`, `scope`, `trust_level`, `source` |
| `memory.updated` | Memory content modified | `memory_id`, `changed_fields` |
| `memory.superseded` | Memory superseded by another | `memory_id`, `superseded_by` |
| `memory.deleted` | Memory soft-deleted | `memory_id` |

### 10.2 Event/Memory Relationship

- **Event/History** is the immutable audit trail. It records *that* a memory operation happened.
- **Memory** is the current durable state. It holds the *current* value of each memory entry.
- Events do NOT duplicate full memory content. They contain only identifiers and metadata.
- This follows the same pattern as Event → Notification: Event is durable source, Notification is mutable derived state.

### 10.3 Replay

- Memory replay is NOT required for V1. Memory entries are created directly by application services.
- Future: if memory entries are ever derived from Events, a replay service can reconstruct them (same pattern as NotificationReplay).

---

## 11. Deletion and Privacy

### 11.1 Deletion Semantics

V1 uses **soft delete**:
- `deleted_at` is set to current UTC time.
- The row remains in the database.
- All normal queries exclude `deleted_at IS NOT NULL`.
- A maintenance operation (outside V1) can hard-delete after a retention period.

### 11.2 User Deletion Rights

- The user can delete ANY memory entry, regardless of trust level or source.
- Deletion is recorded in Events (`memory.deleted`).
- Deletion is NOT blocked by existing references (e.g., `superseded_by`).

### 11.3 Supersession as Update

- Updating a memory in a way that changes its semantic meaning should use supersession, not in-place update.
- In-place update is allowed only for metadata changes (e.g., confidence adjustment).
- Supersession creates a new entry and marks the old one as `SUPERSEDED`.

### 11.4 Expiration

- V1 supports optional `expires_at`. When `expires_at` is reached, a background job marks the memory as `EXPIRED` (deferred to V2).
- In V1, expiration is manual or not used.

---

## 12. Future Retrieval Boundary

### 12.1 Retrieval Contract (Future)

```
MemoryStore (V1)
    ↓
RetrievalService (future)
    ↓ filters by scope, trust, confidence, recency
    ↓ excludes SUPERSEDED, EXPIRED, DELETED
    ↓ respects trust hierarchy (EXPLICIT_USER > SYSTEM > DERIVED > AGENT)
    ↓
ContextAssembly (future)
    ↓ formats memories for prompt inclusion
    ↓ redacts sensitive content
    ↓ limits token count
    ↓
AI Manager / Agent
```

### 12.2 What Retrieval Will Return

- A list of `MemoryEntry` objects with `content`, `trust_level`, `confidence`, `source`, `scope`.
- Retrieval does NOT modify Memory state.
- Retrieval does NOT build prompts. It returns raw memory entries.

### 12.3 What MemoryStore Will NOT Do

- MemoryStore does NOT perform semantic search.
- MemoryStore does NOT rank memories by relevance.
- MemoryStore does NOT assemble prompts.
- MemoryStore does NOT resolve conflicts between memories.
- MemoryStore does NOT filter by AI Manager context.

---

## 13. V1 Contract

### 13.1 Memory Entity

```python
class MemoryEntry(BaseModel):
    memory_id: str
    category: MemoryCategory  # enum: user_instruction, project_knowledge, system_knowledge, agent_observation, decision_rationale, contextual
    scope: MemoryScope  # enum: global, project, system
    scope_id: str | None  # project_id for project scope
    content: dict[str, Any]  # JSON, bounded 8KB
    trust_level: TrustLevel  # enum: EXPLICIT_USER, SYSTEM, DERIVED, EXTERNAL, AGENT
    confidence: float  # 0.0-1.0
    source: ProvenanceSource  # enum: user, system, event, task, agent, external
    source_id: str | None  # specific source identifier
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    expires_at: datetime | None
    superseded_by: str | None
    deleted_at: datetime | None
    metadata: dict[str, Any] | None  # bounded 2KB
```

### 13.2 Memory Scopes

Allowed: `global`, `project`, `system`.

### 13.3 Memory Categories

Allowed: `user_instruction`, `project_knowledge`, `system_knowledge`, `agent_observation`, `decision_rationale`, `contextual`.

### 13.4 Provenance

Required fields: `source`, `source_id`, `created_at`, `trust_level`, `confidence`.

### 13.5 Trust Model

Trust levels: `EXPLICIT_USER`, `SYSTEM`, `DERIVED`, `EXTERNAL`, `AGENT`.
Lower trust cannot override higher trust. Enforcement is in retrieval layer (future), but Memory service records trust correctly.

### 13.6 Lifecycle

V1 states: `ACTIVE`, `SUPERSEDED`, `DELETED`.
Transitions: ACTIVE → SUPERSEDED, ACTIVE → DELETED.

### 13.7 Write Authority

- User: full authority.
- System services: `system_knowledge`, `contextual` (system scope).
- AI Manager/agents: `agent_observation`, `decision_rationale`, `contextual`.
- No one except User can create/modify `user_instruction` or `EXPLICIT_USER` memories.

### 13.8 Deletion

Soft delete with `deleted_at`. Hard delete deferred. User can delete any memory.

### 13.9 Event Integration

Events: `memory.created`, `memory.updated`, `memory.superseded`, `memory.deleted`.
Events contain metadata only, not full memory content.

### 13.10 Retrieval Boundary

MemoryStore returns `MemoryEntry` objects. No prompt building, no semantic search, no conflict resolution.

### 13.11 Security

- No execution authority.
- Secret redaction before persistence.
- Write authority enforced at service layer.
- Scope isolation enforced at query layer.
- Lower trust cannot override higher trust (retrieval layer enforces).

### 13.12 Explicitly Deferred

- Semantic search / embeddings / vector database
- Prompt assembly / context building
- Conflict resolution between memories
- Memory deduplication
- ARCHIVED and EXPIRED states
- AGENT and TASK scopes
- External knowledge import
- Memory-based authorization
- Memory replay from events
- Hard delete maintenance
- Multi-user tenancy
- Memory analytics or learning

---

## 14. Implementation Plan

### 14.2 Memory Persistence/Domain

- Add `MemoryEntry` domain model (Pydantic, frozen).
- Add `MemoryCategory` and `MemoryScope` enums.
- Add `TrustLevel` and `ProvenanceSource` enums.
- Add `MemoryModel` SQLAlchemy ORM model.
- Add `memories` table migration in `init_db()`.
- Add `MemoryRepository` with CRUD operations:
  - `create(memory)` → `MemoryEntry`
  - `get(memory_id)` → `MemoryEntry | None`
  - `list(filters)` → tuple of `MemoryEntry`
  - `update(memory_id, changes)` → `MemoryEntry`
  - `supersede(memory_id, new_memory)` → `MemoryEntry`
  - `delete(memory_id)` → None (soft delete)
- Add unit tests for repository.

**Boundaries:**
- Does NOT integrate with Event system yet.
- Does NOT add REST API yet.
- Does NOT integrate with AI Manager yet.

### 14.3 Memory Service/Authority

- Add `MemoryService` with business logic:
  - `create_memory(caller, category, scope, content, ...)` → validates write authority, assigns trust level, records provenance.
  - `update_memory(caller, memory_id, changes)` → validates authority.
  - `supersede_memory(caller, memory_id, new_content)` → creates new, marks old superseded.
  - `delete_memory(caller, memory_id)` → soft delete.
  - `list_memories(scope, category, trust_level, ...)` → queries with filters.
- Add write authority validation.
- Add secret redaction before persistence.
- Add unit tests for service layer.

**Boundaries:**
- Does NOT emit Events yet.
- Does NOT add REST API yet.
- Does NOT integrate with AI Manager yet.

### 14.4 Event/History Integration

- Add EventType enum values: `memory.created`, `memory.updated`, `memory.superseded`, `memory.deleted`.
- Update `EventEmitter` to support memory events (or emit directly from MemoryService).
- Update `NotificationPolicy` if needed (no memory notifications in V1).
- Add integration tests: memory operation → event emitted → event persisted.
- Verify Event/History remains the audit trail while Memory is current state.

**Boundaries:**
- Does NOT add REST API yet.
- Does NOT integrate with AI Manager yet.

### 14.5 Minimal API/Control Plane (if justified)

- Add `MemoryService` dependency in `dependencies.py`.
- Add `/api/memories` routes:
  - `GET /api/memories` — list with filters
  - `POST /api/memories` — create (user-created memories only)
  - `GET /api/memories/{memory_id}` — get one
  - `PATCH /api/memories/{memory_id}` — update (author or user only)
  - `DELETE /api/memories/{memory_id}` — soft delete (user or author)
- Add Pydantic request/response schemas.
- Add API tests.

**Note:** If the desktop app does not need memory management in V1, the API can be deferred. Memory can be populated internally by system services first.

### 14.6 Hostile Audit

- Audit Memory service for injection vectors (JSON content, metadata).
- Audit write authority enforcement.
- Audit secret redaction.
- Audit trust level enforcement.
- Verify Memory cannot modify execution state, permissions, approvals, or verification.
- Verify Memory cannot bypass any security boundary.
- Full regression test: 2196+ passed, 0 failed.

### 14.7 Final Acceptance

- Verify all 14.x tests pass.
- Verify full project regression.
- Verify no scope creep.
- Write Stage 14 final acceptance report.
- Create checkpoint.

---

## 15. Explicitly Deferred Functionality

- Semantic search / embeddings / vector database
- Prompt assembly / context building for AI Manager
- Conflict resolution between memories at retrieval time
- Automatic deduplication
- ARCHIVED and EXPIRED lifecycle states
- AGENT and TASK scopes
- External knowledge import
- Memory-based authorization or execution influence
- Memory replay from events
- Hard delete maintenance jobs
- Multi-user tenancy
- Memory analytics, learning, or personalization algorithms
- Computer use / desktop control
- Agent teams / coordination
- Autonomous workflow composition
- Tool registry expansion
- AI Manager expansion (model routing, multi-step planning)
- Monitoring / observability dashboards
- Evaluation framework

---

## 16. Open Questions

1. **Settings migration:** Some `AppSetting` entries (e.g., `autonomy_mode`, `kilo_auto_approve`) are effectively user preferences. Should they migrate to Memory in V1 or V2? Recommendation: V2. V1 Memory coexists with Settings.

2. **Conversation memory:** Chat conversations are currently ephemeral execution artifacts. Should important conversation facts become Memory entries? Recommendation: Yes, but via `agent_observation` trust level, in a later stage.

3. **Memory capacity limits:** Should there be a global cap on memory entries? Recommendation: Not in V1. SQLite handles thousands of rows efficiently. Add limits if needed in V2.

4. **Memory IDs:** Should `memory_id` be deterministic (e.g., hash of content+scope) or random? Recommendation: Random (like `notif_`, `evt_`). Deterministic IDs create collision complexity.

5. **Content schema:** Should Memory enforce a schema per category, or remain schema-free JSON? Recommendation: Schema-free JSON in V1. Categories provide structure; schemas can be added per category in V2 if needed.

---

`STAGE_14_1_MEMORY_CONTRACT_READY`
