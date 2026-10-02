# Stage 17.2: Tool Contract Hardening

## Objective
Finalize the typed execution context contract and registry invariants that downstream Stage 17.3 filesystem tools will consume.

## Typed Context Models

### `WorkspaceContext`
- `workspace_path: str` — absolute path to the authorized workspace root
- `conversation_id: str | None` — conversation correlation ID

### `ToolExecutionContext`
- `request_id: str | None`
- `actor_id: str | None`
- `workspace: WorkspaceContext | None`
- `tool_call_id: str | None`
- `approval_context: Any` — server-side approval verification token

## Interface Changes

### `BaseTool.execute`
```python
async def execute(
    self,
    arguments: dict[str, Any],
    context: ToolExecutionContext | None = None,
) -> Any
```
Implementations must treat `context` as typed. All existing implementations were updated.

### `ToolExecutor.execute`
```python
async def execute(
    self,
    tool_call: ToolCall,
    context: ToolExecutionContext | None = None,
) -> ToolExecutionResult
```
The executor no longer builds ad-hoc dict context. It passes typed context directly to `impl.execute()`.

### `ToolExecutionService.execute_tool_call`
```python
async def execute_tool_call(
    self,
    tool_call: ToolCall,
    *,
    action: str | None = None,
    autonomy_mode: AutonomyMode,
    request_id: str | None = None,
    context: dict[str, Any] | None = None,      # backward-compatible
    approval_context: Any = None,
    actor_id: str | None = None,
    workspace_path: str | None = None,           # explicit typed path
) -> ToolExecutionResult
```
- Public callers may still pass the legacy `context` dict.
- Internally, `_build_execution_context()` normalizes dict + explicit params into a `ToolExecutionContext` before invoking `ToolExecutor.execute()`.
- `workspace_path` is resolved from the explicit param first, then from `context["workspace_path"]` if absent.

## Invariants Preserved

1. **Permission before execution**: `PermissionEngine.evaluate()` is always called before `ToolExecutor.execute()`.
2. **DENY / ASK_USER never reach executor**: Hard-coded in `ToolExecutionService.execute_tool_call`.
3. **CRITICAL cannot be upgraded via approval**: `ApprovalService.approve()` re-enters `ToolExecutionService` which re-evaluates permissions; CRITICAL still DENYs in AUTONOMOUS mode.
4. **Audit is fail-soft**: `_safe_audit()` catches all exceptions and never alters the permission decision.
5. **No dynamic imports / shell execution**: `ToolExecutor` and implementations use only static imports.
6. **Duplicate tool registration rejected**: `ToolRegistry` and `ToolImplementationRegistry` enforce uniqueness.
7. **Disabled tools rejected**: `ToolRegistry.is_enabled()` checked before execution.
8. **Result schema validation is non-blocking**: Logs warning but does not alter result.

## Files Modified

| File | Change |
|------|--------|
| `app/tools/models.py` | Added `WorkspaceContext`, `ToolExecutionContext` |
| `app/tools/base.py` | `execute()` now accepts `ToolExecutionContext \| None` |
| `app/tools/executor.py` | `execute()` accepts typed context; no dict merging |
| `app/tools/tool_execution_service.py` | Added `_build_execution_context()`; added `workspace_path` param |
| `app/tools/implementations/system_time.py` | Updated `execute()` signature |
| `app/tools/implementations/dev_test.py` | Reads `context.workspace.workspace_path` |
| `tests/test_tool_execution_service.py` | Added typed context propagation tests |
| `tests/test_tool_executor.py` | Updated `DummyTool`, `BrokenTool` signatures |
| `tests/test_security_e2e.py` | Updated `SpyTool`, `FailingTool` signatures |
| `tests/test_controlled_execution.py` | Updated `SlowTool` signature |
| `tests/test_approval_flow.py` | No signature changes required (dict backward-compat) |

## Test Results

- `tests/test_tool_execution_service.py` — 72 passed
- `tests/test_tool_executor.py` — included in 72
- `tests/test_approval_flow.py` — included in 72
- `tests/test_controlled_execution.py` — 70 passed
- `tests/test_security_e2e.py` — included in 70

## Next Step
Stage 17.3 — implement filesystem tools (`files.read_file`, `files.write_file`, etc.) consuming the typed `WorkspaceContext`.
