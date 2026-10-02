# STAGE_UI_PHYSICAL_AUDIT_REPORT

**Project:** AI Operator  
**Scope:** Read-only audit of desktop UI (Dashboard, Assistant, Activity, Settings)  
**Date:** 2026-09-27  
**Auditor:** Kilo  

---

## 1. Executive Summary

A read-only audit of the AI Operator desktop UI reveals one **critical** authentication bug that directly explains the reported "New Conversation" failure, plus several high/medium-severity UI inconsistencies where the frontend presents capabilities or data that do not match the actual backend state.

**Key findings:**
- **CRITICAL:** Session token cache invalidation gap between Tauri backend and frontend. After a session expires (24h idle/absolute), authenticated requests enter a permanent 401 loop because the frontend resets only its own JS cache, while Tauri continues serving the expired token. `CoreAuthError` is marked non-retryable, so the user sees a dead-end error with no recovery path except restarting the application.
- **HIGH:** Dashboard `AI Provider` card hardcodes `"Ollama"` as a fallback (`aiStatus?.provider ?? 'Ollama'`), masking backend auth/network failures.
- **HIGH:** Autonomy UI advertises four modes (`manual`, `supervised`, `controlled`, `autonomous`) and a "Phase 3" placeholder message, while the backend `PermissionEngine`, `ApprovalService`, risk levels (SAFE/LOW/MEDIUM/HIGH/CRITICAL), and policy boundaries are already fully implemented. Modes `supervised` and `controlled` do not exist in the backend enum and silently default to `MANUAL`.
- **MEDIUM:** StatusBar hardcodes `"AI Not Configured"` instead of reflecting real AI status.
- **MEDIUM:** Dashboard hardcodes `"Pending Approval: 0"` despite the backend having a full `ApprovalService`/`ApprovalRepository` and `pending_approvals` table.
- **MEDIUM:** `useAIStatus` fetches only once on mount; if the session expires post-mount, the UI holds stale data and cannot detect the failure until the next manual refresh.
- **MEDIUM:** Transient network errors during `useCoreStatus` polling can briefly flash `Core Offline` in the StatusBar/Sidebar due to lack of debouncing or graceful state retention.

---

## 2. New Conversation — Root Cause

### Flow
1. User clicks **New Conversation** in `AssistantView.tsx:18`.
2. `newConversation()` in `useAI.ts:522-535` calls `coreApi.createConversation()`.
3. `coreApi.createConversation()` (`coreApi.ts:225`) calls `apiClient.post('/api/ai/conversations', {})`.
4. `apiClient.request()` (`apiClient.ts:322-456`) calls `getCoreUrl()` (returns cached Tauri URL) and `buildAuthHeaders()` (calls `getCoreToken()`).
5. `getCoreToken()` (`apiClient.ts:132-140`) invokes Tauri command `get_core_token`.
6. Tauri `get_core_token` (`lib.rs:151-171`) returns the **cached** `session_token` from `SharedCoreState` without validating it.
7. Frontend sends `Authorization: Bearer <token>` to `POST /api/ai/conversations`.
8. Backend `create_conversation` (`ai.py:156-167`) calls `require_authenticated(request)`.
9. If token is expired/invalid, backend returns **HTTP 401**.
10. Frontend `apiClient.request` (`apiClient.ts:406-409`) catches 401, calls `resetSessionToken()` (clears **only** JS-side cache) and `resetCoreUrlForRetry()`, then throws `CoreAuthError`.
11. `useAssistant` catches the error and displays it. `CoreAuthError.retryable = false`, so no Retry button is offered.
12. **Next request:** `getCoreToken()` calls Tauri again. Tauri still holds the expired token in `core_state.session_token` and returns it. Backend returns 401 again. **Infinite failure loop.**

### Root Cause
**Frontend–Tauri session token cache invalidation mismatch.**

- `apiClient.ts:86-88` (`resetSessionToken`) clears only the in-memory JS variable `sessionToken`.
- `lib.rs:151-171` (`get_core_token`) caches the token in `SharedCoreState.session_token` and **never** validates or refreshes it after the first bootstrap.
- The frontend **never** calls the Tauri command `reset_core_token` (`lib.rs:174-178`), so Tauri's cache survives 401 responses indefinitely.
- After the 24-hour session timeout, the user is locked out of all authenticated AI endpoints until the desktop app is restarted.

### Evidence
- `apiClient.ts:407` — `resetSessionToken()` only nulls JS variable.
- `lib.rs:154` — Tauri returns cached token if `core_state.session_token.is_some()`.
- No `invoke('reset_core_token')` exists anywhere in the frontend codebase (confirmed via grep).
- `apiClient.ts:69` — `CoreAuthError.retryable = false` prevents automatic/user retry recovery.

### Why "Core Offline" May Flash
The `useCoreStatus` hook polls `/api/system/status` every 5 seconds (`useCoreStatus.ts:29,112`). If the poll happens to coincide with a momentary Core unavailability (e.g., Core handling another long request, SQLite write contention, or brief port oscillation), the poll catches `CoreOfflineError` and briefly sets `isOnline = false`, causing the StatusBar and Sidebar to display `Core Offline` / `CORE OFFLINE` until the next successful poll.

This is independent of the auth bug but can occur concurrently, making it appear causally related to the New Conversation click.

---

## 3. Autonomy / Permission Engine — Current Reality vs UI

### UI Text
`SettingsView.tsx:1272-1286` displays:
> **"Permission Engine — Phase 3"**  
> The full autonomy system with risk levels (GREEN/YELLOW/ORANGE/RED), Approval Center, and Policy Engine will be implemented in Phase 3. Setting your preferred mode now ensures a smooth transition.

### Backend Reality
The backend already contains:
- **`PermissionEngine`** (`permission_engine.py:55-463`) — fully implemented with rule-based evaluation, fail-closed defaults, CRITICAL risk absolute deny, grant evaluation (one-time, time-bound, persistent), and approval override logic.
- **`ApprovalService`** (`approval_service.py:44-278`) — complete approval lifecycle (create, approve, reject, execute via `ToolExecutionService`).
- **`RiskLevel`** enum (`tools/models.py:32-37`) — `SAFE`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.
- **`AutonomyMode`** enum (`tools/models.py:40-43`) — `MANUAL`, `SEMI_AUTONOMOUS`, `AUTONOMOUS`.
- **`ToolExecutionService`** with permission engine integration.
- **`ApprovalRepository`**, **`GrantRepository`**, **`AuditLogService`**.
- Backend routes: `/api/ai/approvals/{id}/approve`, `/api/ai/approvals/{id}/reject`.

### UI Mismatch
1. **Stale/incorrect copy:** The "Phase 3" message is factually wrong. The permission engine is already in the codebase and operational.
2. **Mode mismatch:** UI offers `supervised` and `controlled` (`SettingsView.tsx:14-35`), but backend `AutonomyMode` only supports `manual`, `semi_autonomous`, `autonomous`. Selecting `supervised` or `controlled` saves those strings to settings, and both `ApprovalService._get_autonomy_mode()` (`approval_service.py:73-79`) and `AIService._get_autonomy_mode()` (`ai_service.py:114-119`) silently default any unrecognized value to `MANUAL`.
3. **Missing UI:** There is no Approval Center screen (`/approvals` is a Coming Soon placeholder in `App.tsx:57-61`), even though the backend approval flow is fully implemented and already used by `AIService.chat` when `approval_required` is returned.

---

## 4. AI Provider Dashboard — Source of Truth

### Source
`DashboardView.tsx:150-153`:
```tsx
<StatusCard
  label="AI Provider"
  value={aiStatus?.provider ?? 'Ollama'}
  statusClass={aiStatus?.ai_ready ? 'success' : ['online', 'ready'].includes(aiStatus?.provider_status ?? '') ? 'warning' : 'muted'}
  detail={formatAIStatus(aiStatus)}
/>
```

### Source of Truth
`aiStatus` comes from `useAIStatus()` (`useAI.ts:40-58`), which calls `coreApi.getAIStatus()` → `GET /api/ai/status`.

Backend `AIService.get_status()` (`ai_service.py:264-308`) returns:
```python
{
    "provider": config["provider"],        # from settings table
    "provider_status": health.status,      # from provider health check
    "configured": health.configured,
    ...
}
```

### Staleness Risk
`useAIStatus` fetches **only once on mount** (`useAI.ts:57`). It does **not** poll. If the session expires after mount, or if the user changes the provider in Settings, the Dashboard will continue displaying the stale `aiStatus` until the page is reloaded.

### Hardcoded Fallback Problem
The `?? 'Ollama'` fallback means the Dashboard will always display `Ollama` even when:
- The backend is unreachable (network error).
- The session is invalid (401).
- `useAIStatus` throws and `status` remains `null`.

This masks failures. The `statusClass` falls back to `'muted'` and `detail` shows `'AI Not Configured'`, but the prominent label still says `Ollama`.

---

## 5. Dashboard Findings

| Finding | Severity | Evidence |
|---------|----------|----------|
| AI Provider card hardcodes `"Ollama"` fallback | **HIGH** | `DashboardView.tsx:151` |
| Pending Approvals hardcoded to `"0"` | **MEDIUM** | `DashboardView.tsx:266-268` |
| System Information shows static Phase/Version | **INFO** | `DashboardView.tsx:254-255` |
| Core Service / Database status derived from real `useCoreStatus` poll | OK | `DashboardView.tsx:71,130-147` |

---

## 6. Assistant Findings

| Finding | Severity | Evidence |
|---------|----------|----------|
| **Auth token cache invalidation gap** — after 401, Tauri continues serving expired token; frontend cannot auto-recover | **CRITICAL** | `apiClient.ts:407-408`, `lib.rs:154`, missing `reset_core_token` invocation |
| `CoreAuthError.retryable = false` — user has no recovery UI | **HIGH** | `apiClient.ts:69`, `AssistantView.tsx:56-65` (retry button only shows when `retryable`) |
| `newConversation` does not call `refresh()` from `useAIStatus` after success | **LOW** | `useAI.ts:522-535` |
| `useAIStatus` does not poll; stale status after session expiry | **MEDIUM** | `useAI.ts:57` |
| Empty new conversation renders correctly (fallback text shown) | OK | `AssistantView.tsx:46` |

---

## 7. Activity Findings

| Finding | Severity | Evidence |
|---------|----------|----------|
| Activity log uses real backend `GET /api/activity` with pagination/filtering | OK | `ActivityView.tsx:61-78`, `useActivity.ts:26-48` |
| Filtering by category and level works server-side | OK | `coreApi.ts:195-203`, `ActivityView.tsx:93-115` |
| Refresh button works | OK | `ActivityView.tsx:117` |
| No issues found | — | — |

---

## 8. Settings Findings

| Finding | Severity | Evidence |
|---------|----------|----------|
| Autonomy modes `supervised` / `controlled` do not exist in backend; silently default to `MANUAL` | **HIGH** | `SettingsView.tsx:14-35`, `tools/models.py:40-43`, `approval_service.py:73-79` |
| "Permission Engine — Phase 3" message is stale/misleading; backend engine is complete | **HIGH** | `SettingsView.tsx:1272-1286`, `permission_engine.py:55-463` |
| AI settings (provider, models, Ollama process control) are fully wired to backend | OK | `SettingsView.tsx:93-889`, `useAISettings.ts` |
| General settings (theme, language) persist via `PUT /api/settings` | OK | `SettingsView.tsx:1193-1230`, `useSettings.ts` |
| Diagnostics tab shows live Core/DB/AI status | OK | `SettingsView.tsx:950-1092`, `useDiagnostics.ts` |

---

## 9. Findings Sorted by Severity

### CRITICAL
1. **Session token cache invalidation gap** — Frontend `resetSessionToken()` clears only JS memory; Tauri `SharedCoreState.session_token` is never reset on 401. After session expiry, every authenticated request fails permanently until app restart. (`apiClient.ts:86-88`, `lib.rs:151-171`, missing `reset_core_token` invocation)

### HIGH
2. **Dashboard AI Provider hardcoded `"Ollama"` fallback** — Masks backend failures. Even if `getAIStatus` throws, the Dashboard still shows `Ollama`. (`DashboardView.tsx:151`)
3. **Autonomy UI/Backend mode mismatch** — UI exposes `supervised` and `controlled` which backend silently treats as `MANUAL`. (`SettingsView.tsx:14-35`, `tools/models.py:40-43`)
4. **Stale "Permission Engine — Phase 3" copy** — Backend has a complete `PermissionEngine` with risk levels, grants, approval overrides, and audit logging. (`SettingsView.tsx:1272-1286`, `permission_engine.py`)

### MEDIUM
5. **StatusBar hardcoded `"AI Not Configured"`** — Does not reflect actual AI provider status. (`StatusBar.tsx:27`)
6. **Dashboard hardcoded `"Pending Approval: 0"`** — Backend has full approval infrastructure; no count endpoint is consumed. (`DashboardView.tsx:266-268`)
7. **`useAIStatus` no periodic refresh** — Status becomes stale after session expiry or config change. (`useAI.ts:57`)
8. **Transient Core Offline flash** — `useCoreStatus` poll can briefly set `isOnline = false` on momentary network/process errors. (`useCoreStatus.ts:42-98`)

### LOW
9. **`newConversation` does not refresh AI status** — Minor UX inconsistency. (`useAI.ts:522-535`)
10. **Assistant badge edge case** — If `status` is null, badge renders `badge-error` with `'AI Not Configured'`. Functional but not ideal. (`AssistantView.tsx:34`)

### INFO
11. Static version/phase strings in Dashboard and StatusBar. (`DashboardView.tsx:254-255`, `StatusBar.tsx:37`)

---

## 10. Exact Files/Components/Endpoints Involved

### Frontend
| File | Lines | Role |
|------|-------|------|
| `apps/desktop/src/views/Assistant/AssistantView.tsx` | 1-87 | New Conversation button, error display, badge |
| `apps/desktop/src/hooks/useAI.ts` | 1-613 | `useAssistant`, `useAIStatus`, `useAISettings`, error formatting |
| `apps/desktop/src/services/apiClient.ts` | 1-484 | HTTP client, token/URL caching, `CoreAuthError`, `CoreOfflineError` |
| `apps/desktop/src/services/coreApi.ts` | 1-268 | Typed API wrappers including `createConversation` |
| `apps/desktop/src/hooks/useCoreStatus.ts` | 1-125 | Core status polling, `isOnline` state |
| `apps/desktop/src/components/Layout/StatusBar.tsx` | 1-41 | Hardcoded `"AI Not Configured"`, `"Core Offline"` |
| `apps/desktop/src/components/Layout/Sidebar.tsx` | 1-104 | Uses `useCoreStatus` and `useAIStatus` |
| `apps/desktop/src/views/Dashboard/DashboardView.tsx` | 1-276 | AI Provider fallback, hardcoded Pending Approvals |
| `apps/desktop/src/views/Settings/SettingsView.tsx` | 1-1297 | Autonomy modes, Phase 3 copy, AI settings |
| `apps/desktop/src/views/Activity/ActivityView.tsx` | 1-174 | Activity log with filters |

### Backend
| File | Lines | Role |
|------|-------|------|
| `services/core/app/api/routes/ai.py` | 156-167 | `POST /api/ai/conversations` — `create_conversation` |
| `services/core/app/api/routes/auth.py` | 1-95 | `POST /api/auth/session` — bootstrap endpoint |
| `services/core/app/identity/auth.py` | 1-186 | `AuthMiddleware`, `PUBLIC_ENDPOINTS`, `require_authenticated` |
| `services/core/app/identity/session.py` | 1-146 | `SessionStore` — in-memory, 24h timeout |
| `services/core/app/identity/models.py` | 1-134 | `Session`, `RequestContext` |
| `services/core/app/tools/permission_engine.py` | 1-464 | Complete `PermissionEngine` implementation |
| `services/core/app/tools/approval_service.py` | 1-278 | Complete `ApprovalService` implementation |
| `services/core/app/tools/models.py` | 1-309 | `AutonomyMode`, `RiskLevel`, `PermissionDecision`, `GrantType` |
| `services/core/app/ai/services/ai_service.py` | 264-308 | `get_status()` — source of `AIStatusResponse` |
| `services/core/app/ai/services/provider_manager.py` | 1-161 | `ProviderManager.get_config()` |
| `services/core/app/services/settings_service.py` | 1-49 | `SettingsService` — stores `autonomy_mode` |
| `services/core/app/api/routes/system.py` | 1-133 | `GET /api/system/status` — public endpoint |
| `services/core/app/api/schemas/system.py` | 1-39 | `SystemStatusResponse`, `AIComponentStatus` |
| `services/core/app/main.py` | 1-115 | App factory, router registration |

### Tauri (Rust)
| File | Lines | Role |
|------|-------|------|
| `apps/desktop/src-tauri/src/lib.rs` | 1-341 | `get_core_url`, `get_core_diagnostics`, `get_core_token`, `reset_core_token`, `start_python_core` |

---

## 11. Recommended Next Actions

### Immediate (no implementation required, just planning)

1. **Fix session token cache invalidation (CRITICAL)**
   - On 401 responses, the frontend must invoke Tauri's `reset_core_token` to clear the backend-side cache before retrying with a fresh bootstrap.
   - Alternatively, Tauri should validate the cached token before returning it (e.g., lightweight HEAD request or backend introspection).
   - `CoreAuthError` should be marked `retryable = true` and the Assistant should auto-retry once after a token reset, or at minimum show a "Re-authenticate" action.

2. **Remove hardcoded `"Ollama"` fallback in Dashboard (HIGH)**
   - Replace `aiStatus?.provider ?? 'Ollama'` with an explicit "Unknown / Unavailable" state.
   - If `useAIStatus` errors, the Dashboard should surface the error rather than masking it.

3. **Fix Autonomy mode mapping (HIGH)**
   - Either remove `supervised` and `controlled` from the UI and align with backend `AutonomyMode` enum (`manual`, `semi_autonomous`, `autonomous`), or add corresponding backend modes.
   - Update the "Phase 3" copy to reflect that `PermissionEngine`, `ApprovalService`, and risk levels are already implemented.

4. **Wire Pending Approvals count (MEDIUM)**
   - Add a backend endpoint (e.g., `GET /api/ai/approvals/count`) that returns the count of pending approvals.
   - Consume it in the Dashboard instead of hardcoding `"0"`.

5. **Fix StatusBar AI indicator (MEDIUM)**
   - Replace hardcoded `"AI Not Configured"` with real status derived from `useAIStatus` or `useCoreStatus`.

6. **Add periodic refresh to `useAIStatus` (MEDIUM)**
   - Poll `/api/ai/status` every 30-60 seconds so the UI detects auth expiry, provider changes, or model changes without a full page reload.

7. **Graceful Core Offline handling (MEDIUM)**
   - In `useCoreStatus`, retain the last known good state for a few polls before declaring offline, or add a minimum offline duration before updating UI, to prevent flicker from momentary blips.

---

**End of Report**
