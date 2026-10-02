# STAGE 11.3 — HISTORY QUERY LAYER IMPLEMENTATION REPORT

## 1. Implementation Summary

Implemented the minimal History read/query layer over the durable Event Store.

**Files created:**
- `app/events/history.py` — `HistoryEntry`, `HistoryPage`, `HistoryService`
- `tests/test_history_repository.py` — repository-level tests
- `tests/test_history_service.py` — service-level tests
- `tests/test_history_integration.py` — integration tests

**Files modified:**
- `app/events/__init__.py` — added exports for new public API
- `app/events/repository.py` — added `recent_first` parameter to `list()` and new `list_recent()` method
- `app/repositories/task_repository.py` — fixed missing `plan_id` persistence in `create_task()`

---

## 2. HistoryEntry Design

`HistoryEntry` is a typed Pydantic read model over `Event`. It preserves all durable Event fields:

- `event_id`, `event_type`, `occurred_at`, `recorded_at`, `source`
- `event_version`, `correlation_id`, `causation_id`
- `task_id`, `plan_id`, `run_id`, `execution_id`
- `actor`, `payload`, `metadata`, `sensitivity`

`HistoryEntry` is immutable (`ConfigDict(frozen=True)`). It is constructed via `from_event()` classmethod, which copies all fields from the source `Event`. The Event Store remains the sole source of truth; `HistoryEntry` is a view, not a second source of truth.

---

## 3. HistoryPage Design

`HistoryPage` is an immutable pagination container:

- `entries`: tuple of `HistoryEntry`
- `total`: total matching event count
- `limit`: page size
- `offset`: pagination offset
- `has_more`: whether additional pages exist

Strict validation:
- `limit` must be in [1, 500]
- `offset` must be in [0, 100000]
- Invalid values raise `ValueError` deterministically

---

## 4. HistoryService Methods

Implemented 6 methods on `HistoryService`:

| Method | Filter | Ordering |
|--------|--------|----------|
| `get_task_history(task_id, limit, offset)` | `task_id = ?` | `occurred_at ASC, event_id ASC` |
| `get_plan_history(plan_id, limit, offset)` | `plan_id = ?` | `occurred_at ASC, event_id ASC` |
| `get_execution_history(execution_id, limit, offset)` | `execution_id = ?` | `occurred_at ASC, event_id ASC` |
| `get_correlation_history(correlation_id, limit, offset)` | `correlation_id = ?` | `occurred_at ASC, event_id ASC` |
| `get_history_between(start, end, limit, offset, event_type)` | `occurred_at BETWEEN ? AND ?` | `occurred_at ASC, event_id ASC` |
| `get_recent_history(limit)` | none | `recorded_at DESC, event_id DESC` |

All methods delegate to `EventRepository` for data access.

---

## 5. Pagination Behavior

- **Default limit**: 50
- **Maximum limit**: 500
- **Maximum offset**: 100,000
- **has_more**: `offset + len(entries) < total`

`HistoryService` validates inputs before passing to `EventRepository`. `EventRepository` also clamps as a safety net. `get_recent_history` uses `list_recent(limit=limit)` with hard cap.

---

## 6. Ordering Semantics

- **Entity-scoped queries**: `occurred_at ASC, event_id ASC` (deterministic, not causal)
- **Recent history**: `recorded_at DESC, event_id DESC` (most recently persisted first)
- **Causation**: `causation_id` remains explicit data; ordering does NOT imply causality

---

## 7. Empty/Failure Semantics

| Scenario | Behavior |
|----------|----------|
| Valid query, no matching events | Returns `HistoryPage(entries=(), total=0, has_more=False)` |
| Invalid time range (`start > end`) | Raises `ValueError` |
| Naive datetime | Raises `ValueError` |
| Database/persistence failure | Propagates repository exception |
| Event deserialization failure | Logs warning, returns `HistoryEntry` with `payload=None` or `metadata=None` |

Storage failures are NEVER converted to empty history.

---

## 8. Authorization Boundary

- `get_task_history`: Verifies task existence via `TaskRepository.get_task()` before returning events
- `get_plan_history`: Verifies plan existence via `TaskRepository.get_tasks_by_plan()` before returning events
- `get_execution_history`, `get_correlation_history`: No entity ownership check
- `get_history_between`, `get_recent_history`: Administrative queries, no task-level authorization

**HistoryService does NOT invent a new authorization framework. It does NOT grant access or bypass PermissionEngine. API-level authorization is OUT OF SCOPE.**

---

## 9. Security Verification

- No secret rehydration: payloads/metadata are already sanitized at emission by `EventEmitter`
- No raw database access: HistoryService uses `EventRepository` exclusively
- No event execution: payloads are returned as inert dict data
- No permission grants: HistoryService is read-only observational
- `HistoryEntry` and `HistoryPage` are frozen models, preventing mutation
- Tests verify persisted redaction remains intact in `HistoryEntry`

---

## 10. Index/Query Behavior

All queries use existing indexes. No new indexes were added.

| Query | Index Used |
|-------|------------|
| `get_task_history` | `ix_events_task_id` |
| `get_plan_history` | `ix_events_plan_id` |
| `get_execution_history` | `ix_events_execution_id` |
| `get_correlation_history` | `ix_events_correlation_id` |
| `get_history_between` | `ix_events_occurred_at` |
| `get_recent_history` | bounded full scan (limit ≤ 500) |

---

## 11. Test Counts

| File | Tests |
|------|-------|
| `tests/test_history_repository.py` | 17 |
| `tests/test_history_service.py` | 24 |
| `tests/test_history_integration.py` | 5 |
| **Total new tests** | **46** |

---

## 12. Full Regression Count

- **Event Foundation tests**: 39/39 passed
- **Task Repository tests**: 13/13 passed
- **New History tests**: 46/46 passed
- **Total regression**: 98/98 passed

---

## 13. Defects Found and Fixed

1. **TaskRepository.create_task missing plan_id persistence** (pre-existing)
   - `create_task()` did not pass `plan_id` to `DevelopmentTaskModel`
   - Fixed by adding `plan_id=(task.plan_id or "").strip() or None`
   - This bug prevented `get_tasks_by_plan()` from finding tasks, breaking plan history

2. **Test expectation error in deterministic ordering**
   - `test_deterministic_ordering` expected wrong order when two events shared `occurred_at`
   - Fixed expected value to match actual `event_id ASC` tiebreaker behavior

3. **HistoryService parameter name mismatch in tests**
   - Tests passed `task_repo=None` but parameter is `task_repository`
   - Fixed test code to use correct parameter name

---

## 14. Known Limitations

- `run_id` and `execution_id` are not yet populated by Tier 1 emitters; `get_execution_history` and `get_recent_history` will have limited data until future stages populate these fields
- `get_recent_history` total count uses `EventRepository.count()` (exact count, efficient for SQLite)
- `get_plan_history` returns empty if no tasks exist for the plan, even if plan-level events exist
- No REST endpoints in this stage (explicitly out of scope)

---

## 15. Explicit Non-Goals

- No event projections or materialized views
- No analytics or aggregations
- No dashboards or UI
- No notification triggers
- No scheduler integration
- No memory/context injection
- No event streaming or WebSockets
- No external API exposure
- No search engine integration
- No archival or retention redesign
- No new authorization system
- No causal ordering guarantees beyond deterministic chronological ordering

---

## 16. Recommendation for Next Stage

**Stage 11.4: REST History Endpoints**

The minimal next stage is to expose the HistoryService through REST API routes, following the endpoint patterns proposed in the discovery document:

```
GET /history/tasks/{task_id}
GET /history/plans/{plan_id}
GET /history/executions/{execution_id}
GET /history/correlations/{correlation_id}
GET /history/between?start=...&end=...&event_type=...
GET /history/recent?limit=...
```

This requires only route wiring and response serialization; no new service logic.
