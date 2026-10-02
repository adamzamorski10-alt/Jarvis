# Stage 16.1 — Identity & Session Contract

**Date:** 2026-09-24
**Scope:** Identity & Session foundation contract for AI Operator
**Status:** DISCOVERY COMPLETE — SECURITY CORRECTED (Stage 16.1.1)

---

## 1. Executive Summary

This document defines the contract for the smallest correct identity/session foundation for the AI Operator. It is based on inspection of the actual codebase, not previous reports.

**Key finding:** The current architecture has **no authentication, no user identity, and no session management**. Every API endpoint is unauthenticated. `MemoryCaller` is a manually constructed object with an arbitrary `caller_id` string — it is not tied to any real identity. The only session-like concept is `KiloSession`, which tracks Kilo Code CLI execution sessions, not user sessions.

**V1 boundary:** Single local human user, local-only deployment, no remote clients, no multi-user accounts, no OAuth, no passwords. Identity is derived from the local OS/user context.

**Security correction (Stage 16.1.1):** The original Stage 16.1 proposal for client-generated bearer tokens without server state was fundamentally flawed — it provided zero authentication. The corrected design uses a server-issued session token backed by an in-memory server-side session store, with a local file-based bootstrap secret.

**Proposed approach:** A lightweight local identity layer that:
- Derives user identity from OS context (Windows user SID / username)
- Authenticates via a local bootstrap secret file, then issues server-verified session tokens
- Maps identity to existing `MemoryCaller`, event `actor`, and permission `context`
- Does NOT become a second authorization system
- Preserves all existing security boundaries

---

## 2. Current Authentication State

### 2.1 Actual Current Behavior (from code inspection)

**API Authentication:**
- **None.** All endpoints are accessible without any authentication token, cookie, or identity header.
- CORS is configured with `allow_origins=["*"]` in `main.py` — there is effectively no CORS restriction. The `config.py` defines a restrictive localhost list but it is never applied.
- The desktop app makes plain HTTP calls with no auth headers.

**MemoryCaller:**
- `MemoryCaller` is a `@dataclass(frozen=True)` with `caller_type: CallerType` (USER, SYSTEM, AGENT) and `caller_id: str | None`.
- `caller_id` is an arbitrary string provided by the caller — it is not validated, authenticated, or mapped to any identity store.
- Example: `MemoryCaller(CallerType.USER, "user_1")` — "user_1" is a magic string, not an authenticated identity.

**Memory ownership:**
- When `source_id` is set, ownership checks compare `existing.source_id != caller.caller_id`.
- When `source_id` is `None`, ownership falls back to `source` type match (e.g., any AGENT can update AGENT-sourced memories).
- This is a **V1 weakness** — arbitrary `caller_id` values can claim ownership of memories with `source_id=None`.

**KiloSession:**
- `KiloSession` tracks Kilo Code CLI execution sessions for recovery.
- `app_session_id` is application-owned.
- `kilo_session_id` is untrusted external metadata from Kilo.
- **Not a user session** — this is a process/execution session.

**Event actor:**
- `Event.actor` is set manually per emission: `actor=caller.caller_id if caller else None`.
- For scheduler events: `actor` is not set (None).
- For autonomous plan events: `actor="system"`.
- No identity verification.

**Task ownership:**
- `DevelopmentTask` has `project_id` but no `owner_id` or `created_by`.
- In practice, `project_id` is always `"default"`.
- No user association.

**Conversation ownership:**
- `Conversation` has no `user_id` or `owner_id`.
- Conversations are global — any client can read any conversation.

**Settings/secrets:**
- AI provider API keys are encrypted with Windows DPAPI (`CredentialService`).
- No user-specific settings — all settings are global key-value pairs.

**Desktop client:**
- Tauri app running on localhost.
- No user identity stored in settings.
- No authentication tokens.
- Communicates with core via plain HTTP.

### 2.2 Current Trust Boundaries

```
Desktop App (Tauri)
  ↓ HTTP (localhost only)
FastAPI (no auth)
  ↓
All services (MemoryService, ToolExecutionService, etc.)
  ↓
Database (SQLite file)
```

**Trust assumption:** The desktop app is trusted because it runs on localhost. Any process on the machine can call the API.

---

## 3. Identity Model

### 3.1 Definitions

**A. OS Identity / User Identification:**
The single human operator of this AI Operator instance, identified by their local OS account. This is an identifier, not authentication evidence.

**B. Request Authentication:**
The process by which Core verifies that a request originates from the legitimate local desktop app. In V1, this uses a server-issued session token validated against an in-memory server-side session store.

**C. Session Authentication:**
The process by which Core verifies that a bearer token represents an active, non-expired session. The server is the sole authority for session validity.

**D. Authorization:**
The process by which Core decides whether an authenticated request may perform a specific action. Handled exclusively by `PermissionEngine` + `ApprovalService`.

**E. Permission:**
Tool-level access control (allow/deny/ask). Handled by `PermissionEngine` based on `tool_id`, `action`, `risk_level`, `autonomy_mode`, and grants.

**F. Approval:**
Human-in-the-loop consent for risky tool calls. Handled by `ApprovalService`.

**Identity MUST NOT become a second authorization system.** Identity only answers "who is asking" — it does not decide "what they can do".

### 3.2 Identity Hierarchy

```
OS Identity (identifier)
  ↓
Request Authentication (server-verified)
  ↓
Authenticated Identity
  ↓
Authenticated Request Context
  ↓
Caller / Actor
  ↓
Authorization Decision (PermissionEngine)
```

### 3.3 User Model

V1 user:
- Derived from OS context (Windows: `USERNAME` environment variable + machine SID)
- Single user per installation
- No password, no profile, no preferences (yet)
- Immutable identifier (does not change when username changes — use SID or machine+username hash)

```python
class UserIdentity:
    user_id: str          # e.g., "local_user_<machine_sid_hash>"
    display_name: str     # e.g., "adamz"
    os_username: str      # e.g., "adamz"
    machine_id: str       # e.g., hash of machine SID
```

**Non-Goals:**
- No user registration
- No user profiles
- No user preferences (settings are global)
- No user roles (single user = no roles needed)
- No external identity providers

---

## 4. Session Model

### 4.1 Session Definition

A session represents a continuous authenticated period for the desktop app. Sessions are:
- **Ephemeral** — not durable across Core restarts
- **Server-issued** — generated and validated by Core
- **Time-bounded** — absolute timeout (24 hours) and idle timeout (24 hours)
- **Revocable** — can be invalidated by Core

### 4.2 Session Bootstrap: Local Secret

**Mechanism:** Core generates a cryptographically random `local_secret` on first startup and stores it in a local file protected by OS file ACLs. The desktop app reads this file and uses it to request a session.

**File location:** `%APPDATA%/AI-Operator/.local_secret` (Windows). Future: platform-specific app data directory.

**File format:** 32-byte cryptographically random hex string (64 hex characters).

**File protection:** On Windows, `%APPDATA%` is user-specific. Files created there are accessible only to the current Windows user by default. No additional ACL configuration is required for V1.

**Bootstrap flow:**
1. Core starts.
2. If `.local_secret` does not exist, Core generates a new 32-byte random secret and writes it to the file.
3. Core loads the secret into memory (not from disk on each request).
4. Desktop app reads `.local_secret` on startup.
5. If the file is missing or unreadable, the desktop app fails to start with an error.

**Security property:** The `local_secret` is not a session token. It is a bootstrap credential used exactly once to obtain a session. It is not sent in subsequent API requests.

### 4.3 Session Creation

1. Desktop app reads `local_secret` from file.
2. Desktop app sends `POST /api/auth/session` with header `X-Local-Secret: <secret>`.
3. Core validates the secret against the in-memory copy.
4. Core generates `session_id = ses_<random_hex>` (cryptographically random, 32 bytes hex).
5. Core creates a `Session` record in the in-memory session store:
   - `session_id`
   - `user_id` (derived from OS context at Core startup)
   - `created_at` (UTC)
   - `expires_at` (created_at + 24 hours absolute)
   - `last_accessed` (created_at)
6. Core returns `{ "session_id": "...", "expires_at": "..." }`.
7. Desktop app stores `session_id` and sends it as `Authorization: Bearer <session_id>` in all subsequent requests.

**Session authority:** Core is the sole authority for session creation and validation. The desktop app cannot create or modify sessions — it can only request them using the bootstrap secret.

### 4.4 Session Identifier

Format: `ses_<random_hex>` (e.g., `ses_a1b2c3d4e5f6...`)

- Generated by Core using a cryptographically secure random number generator.
- 32 bytes (256 bits) of randomness.
- Server-issued — the desktop app cannot choose the value.

### 4.5 Session Verification

For each request with `Authorization: Bearer <token>`:

1. AuthMiddleware extracts the token.
2. Validates format: must start with `ses_` followed by 64 hex characters.
3. Looks up the session in the in-memory session store by `session_id`.
4. If not found → `401 Unauthorized`.
5. Checks `expires_at` (absolute timeout, 24 hours from creation).
6. Checks idle timeout: `last_accessed` must be within 24 hours of the current time.
7. If expired → `401 Unauthorized` + remove from store.
8. Updates `last_accessed` to current UTC time.
9. Builds `RequestContext` from session data.

### 4.6 Session Expiration

| Timeout | Mechanism | Default |
|---------|-----------|---------|
| Absolute | `expires_at = created_at + 24h` | 24 hours |
| Idle | `last_accessed + 24h < now` | 24 hours |

Either condition triggers expiry. Expired sessions are removed from the store on the next access attempt.

### 4.7 Session Revocation

- Core can remove any session from the in-memory store.
- Desktop app can discard its session_id locally, but this is not a server-side revocation.
- No explicit revocation API in V1.
- Revocation is implicit: Core restart clears all sessions; expiry clears individual sessions.

### 4.8 Persistence Requirements

**V1: In-memory only.** Sessions are stored in a `dict[str, Session]` in Core memory. No database table.

**Rationale:**
- Single user, single device, local-only
- Session loss on restart is acceptable (desktop app re-bootstraps)
- No cross-device sync needed
- In-memory store is sufficient for a single active session

**Future:** Durable sessions require a `sessions` table with expiry.

### 4.9 Recovery/Restart Behavior

| Event | Behavior |
|-------|----------|
| Desktop app restart | Reads `.local_secret`, requests new session from Core |
| Core restart | In-memory sessions lost. Desktop app re-bootstraps using `.local_secret` |
| Session expiry | Desktop app receives 401, re-bootstraps |
| `.local_secret` file deleted | Desktop app cannot start. User must re-pair or restore the file |

---

## 5. Actor / Caller Model

### 5.1 Actor Types

| Actor Type | Description | Example |
|------------|-------------|---------|
| `user` | Authenticated human user | `actor="user"`, `actor_id="local_user_abc123"` |
| `system` | Internal system processes | `actor="system"`, `actor_id="scheduler"` |
| `agent` | AI assistant acting for user | `actor="agent"`, `actor_id="local_user_abc123"` |
| `service` | Internal services | `actor="service"`, `actor_id="notification_service"` |

### 5.2 Actor Derivation Rules

**For HTTP requests (authenticated):**
- `actor_type` is set to `"user"` by `AuthMiddleware`.
- `actor_id` is set to `user_id` from the session.
- The client MUST NOT supply `actor_type`, `actor_id`, or `user_id` via HTTP input.

**For internal system calls:**
- `actor_type`, `actor_id`, and `service_name` are set by the calling code.
- These are never derived from HTTP input.

**For agent loop:**
- `actor_type` is set to `"agent"`.
- `actor_id` is set to `user_id` (agent acts for user).
- The agent has no independent authority.

### 5.3 Request Context

```python
class RequestContext:
    user_id: str | None          # Authoritative: from session (HTTP) or None (system)
    actor_type: str              # Authoritative: set by AuthMiddleware or internal caller
    actor_id: str                # Authoritative: derived from actor_type + user_id/service_id
    session_id: str | None       # Authoritative: from session (HTTP) or None (system)
    service_name: str | None     # Authoritative: set by internal caller, never from HTTP
```

**Authoritative fields:** `user_id`, `actor_type`, `actor_id`, `session_id`, and `service_name` are set by Core. None are accepted from HTTP request JSON, query parameters, or headers (except `session_id` which is extracted from the `Authorization` bearer token by `AuthMiddleware`).

---

## 6. Memory Integration

### 6.1 Current Weakness

The current `MemoryCaller` uses arbitrary `caller_id` strings. When `source_id=None`, ownership falls back to source-type match, allowing any AGENT to update AGENT-sourced memories.

### 6.2 V1 Mapping

| Memory Field | Current | V1 with Identity |
|--------------|---------|------------------|
| `MemoryCaller.caller_type` | Manual (USER/SYSTEM/AGENT) | Derived from actor type |
| `MemoryCaller.caller_id` | Arbitrary string | Authenticated `user_id` or `service_id` |
| `source_id` | Optional caller-provided | Defaults to `user_id` for USER, `service_id` for SYSTEM |
| `trust_level` | Caller-provided or default | Unchanged — trust is still category-based |
| `source` | Caller-provided or default | Unchanged — provenance source |

### 6.3 Ownership Rules with Identity

1. **USER memories** (`source=USER`): `source_id` MUST be the authenticated `user_id`. Only the user (or system on their behalf) can modify.
2. **SYSTEM memories** (`source=SYSTEM`): `source_id` MUST be a known service identity. Only that service (or user) can modify.
3. **AGENT memories** (`source=AGENT`): `source_id` MUST be the authenticated `user_id` (agent acts for user). Only the user (or agent on their behalf) can modify.
4. **EVENT/TASK/EXTERNAL memories**: `source_id` is set to the originating event/task/external ID.

**Rule:** `source_id` is NEVER `None` for USER, SYSTEM, or AGENT memories. It is only `None` for EVENT/TASK/EXTERNAL sources where the origin is not a user/service/agent.

### 6.4 MemoryCaller Construction

```python
def memory_caller_from_request(
    ctx: RequestContext,
    caller_type: CallerType,
) -> MemoryCaller:
    if caller_type == CallerType.USER:
        if ctx.user_id is None:
            raise MemoryForbidden("User identity required for USER caller type")
        return MemoryCaller(CallerType.USER, ctx.user_id)
    elif caller_type == CallerType.SYSTEM:
        service_id = ctx.service_name or "unknown_system"
        return MemoryCaller(CallerType.SYSTEM, service_id)
    elif caller_type == CallerType.AGENT:
        if ctx.user_id is None:
            raise MemoryForbidden("User identity required for AGENT caller type")
        return MemoryCaller(CallerType.AGENT, ctx.user_id)
    else:
        raise ValueError(f"Unknown caller type: {caller_type}")
```

---

## 7. Event / Audit Integration

### 7.1 Event.actor

The `Event.actor` field stores the immediate actor identifier. V1 mapping:

| Event Source | Actor Value | Actor ID |
|--------------|-------------|----------|
| User-initiated API call | `user` | `user_id` |
| Agent loop (AIService) | `agent` | `user_id` (agent acts for user) |
| Scheduler worker | `system` | `scheduler` |
| Recovery manager | `system` | `recovery_manager` |
| Background execution | `system` | `background_executor` |
| Notification policy | `service` | `notification_service` |

### 7.2 Correlation Context

- **Memory events:** `correlation_id = memory_id` (unchanged)
- **Task events:** `correlation_id = task_id` (unchanged)
- **Plan events:** `correlation_id = plan_id` (unchanged)
- **Session-scoped events:** Future — separate correlation mechanism, not overloaded with user identity

### 7.3 Causation Context

- Memory events do not set `causation_id` (V1 limitation).
- Task/plan events may set `causation_id` to the triggering event's `event_id`.
- Identity does not affect causation.

### 7.4 Audit Actor

- `AuditLogService` records `actor_id` for permission/tool events.
- V1: `actor_id = user_id` for user-initiated actions, `service_id` for system actions.
- No secrets in audit logs.

---

## 8. Permission / Approval Integration

### 8.1 Authorization Flow

```
Request with Session
  ↓
AuthMiddleware (validate session, build RequestContext)
  ↓
RequestContext (user_id, actor_type, actor_id)
  ↓
PermissionEngine.evaluate(request)
  ↓
  ├─→ ALLOW → ToolExecutionService.execute()
  ├─→ ASK_USER → ApprovalService.create_pending_approval()
  └─→ DENY → Audit log + error response
```

### 8.2 Identity in PermissionEngine

`PermissionEvaluationRequest` gains an optional `actor_id` field:

```python
class PermissionEvaluationRequest(BaseModel):
    tool_id: str
    risk_level: RiskLevel
    action: str
    autonomy_mode: AutonomyMode
    arguments: dict[str, Any]
    context: dict[str, Any]
    approval_context: Any
    actor_id: str | None = None  # Informational/audit context only
```

**Rule:** `PermissionEngine` does NOT authenticate. It receives an already-authenticated `actor_id`. `actor_id` is used for audit logging only. Authorization decisions are based on `tool_id`, `action`, `risk_level`, `autonomy_mode`, and grants — NOT on identity.

### 8.3 ApprovalService Integration

- `ApprovalService` receives `actor_id` from the request context.
- Audit logs include `actor_id`.
- Approval tokens are bound to the `actor_id` that requested them.
- No identity-based approval rules in V1 (all users are equivalent).
- `ApprovalService` does not trust actor identity supplied directly by clients — it uses the server-side `RequestContext`.

---

## 9. API Security

### 9.1 Endpoint Classification

| Endpoint | Classification | Rationale |
|----------|---------------|-----------|
| `POST /api/auth/session` | PUBLIC (LOCAL BOOTSTRAP) | Protected by `X-Local-Secret` header. Requires local file access. |
| `GET /api/health` | PUBLIC/LOCAL HEALTH | No sensitive data. Health check. |
| `GET /api/system/status` | PUBLIC/LOCAL HEALTH | Read-only system status. |
| `GET /api/system/diagnostics` | AUTHENTICATED USER | May expose internal state. |
| `GET /api/settings` | AUTHENTICATED USER | Read access to settings (includes encrypted API keys). |
| `PUT /api/settings` | AUTHENTICATED USER | Modifies settings. |
| `GET /api/activity` | AUTHENTICATED USER | User activity history. |
| `POST /api/activity` | AUTHENTICATED USER | Creates activity events. |
| `GET /api/ai/providers` | PUBLIC/LOCAL HEALTH | Read-only provider metadata. |
| `GET /api/ai/config` | AUTHENTICATED USER | Read access to AI config (may include API keys). |
| `PUT /api/ai/config` | AUTHENTICATED USER | Modifies AI config. |
| `POST /api/ai/chat` | AUTHENTICATED USER | Modifies conversations. |
| `POST /api/ai/approvals/{id}/approve` | AUTHENTICATED USER | Modifies approval state. |
| `POST /api/ai/approvals/{id}/reject` | AUTHENTICATED USER | Modifies approval state. |
| `POST /api/plans/{plan_id}/execute` | AUTHENTICATED USER | Starts background execution. |
| `POST /api/plans/{plan_id}/cancel` | AUTHENTICATED USER | Cancels execution. |
| `GET /api/history/*` | AUTHENTICATED USER | Execution history (may contain sensitive details). |
| `POST /api/scheduler/jobs` | AUTHENTICATED USER | Creates scheduled jobs. |
| `PATCH /api/scheduler/jobs/{id}` | AUTHENTICATED USER | Modifies scheduled jobs. |
| `DELETE /api/scheduler/jobs/{id}` | AUTHENTICATED USER | Deletes scheduled jobs. |
| `POST /api/scheduler/jobs/{id}/enable` | AUTHENTICATED USER | Modifies scheduled jobs. |
| `POST /api/scheduler/jobs/{id}/disable` | AUTHENTICATED USER | Modifies scheduled jobs. |
| `GET /api/scheduler/jobs` | AUTHENTICATED USER | Read access to scheduled jobs. |
| `GET /api/scheduler/jobs/{id}` | AUTHENTICATED USER | Read access to scheduled jobs. |
| `GET /api/scheduler/jobs/{id}/history` | AUTHENTICATED USER | Read access to job history. |
| All other `/api/ai/*` write endpoints | AUTHENTICATED USER | Model management, Ollama control, etc. |

### 9.2 Authentication Layer

A new `AuthMiddleware` is proposed:

```python
class AuthMiddleware:
    async def __call__(self, request: Request, call_next):
        # 1. Determine if endpoint requires auth
        # 2. For public endpoints: pass through
        # 3. For authenticated endpoints:
        #    a. Extract session from Authorization: Bearer <token>
        #    b. Validate format (ses_ + 64 hex chars)
        #    c. Look up session in in-memory store
        #    d. Check expiry (absolute + idle)
        #    e. Update last_accessed
        #    f. Build RequestContext
        #    g. Attach to request.state
        # 4. Call next
```

**Insertion point:** The middleware is inserted in `main.py` without modifying any route logic. Routes access identity via `Request.state.context`.

### 9.3 Localhost Assumption

The current architecture relies on `127.0.0.1` binding. The CORS configuration in `main.py` uses `allow_origins=["*"]` — this must be corrected to use the `config.py` `cors_origins` list. The auth layer adds defense-in-depth but is not a replacement for network-level access control.

### 9.4 Bootstrap Endpoint Security

`POST /api/auth/session` is the only unauthenticated endpoint. It is protected by:

1. `X-Local-Secret` header must match the Core's in-memory secret.
2. The secret is stored in a file protected by OS ACLs (Windows user-only).
3. The secret is never transmitted except during bootstrap.

An attacker who can read the `.local_secret` file can obtain a session. This is equivalent to having access to the user's Windows account.

---

## 10. Threat Model

### 10.1 Evaluated Session Designs

| Design | Authentication Strength | Complexity | Restart Behavior | Revocation | Replay | Local-Process Threat | V1 Fit | Future Compat |
|--------|------------------------|-----------|------------------|------------|--------|----------------------|--------|---------------|
| **A. Client-generated random bearer token without server state** | **NONE** — any client can forge a token with valid format | Low | Stateless | Impossible | None | Not protected | Does NOT fit | Poor |
| **B. Server-issued random session token + in-memory server-side session store** | **Strong** — server is sole authority | Medium | Sessions lost on restart | Possible (remove from store) | Good | Protected against different OS users; not against same-user processes | **YES** | Good |
| **C. Server-issued token + durable session store** | Strong | Higher | Survives restarts | Possible | Good | Same as B | Overkill for V1 | Good |
| **D. Signed/stateless server-issued token** | Strong (cryptographic) | Medium | Survives restarts if secret persists | Hard (stateless) | Good | Same as B | Possible | Good |
| **E. OS-local IPC/bootstrap + server-issued session** | Very strong | High | Depends on implementation | Possible | Good | Strong | Overkill for V1 | Good |

**Selected V1 design: B — Server-issued random session token + in-memory server-side session store.**

Rationale:
- Server is the authority for session validity.
- Simple to implement (in-memory dict).
- Fits single-user local V1.
- Supports revocation, expiry, and idle timeout.
- Future-compatible (can migrate to durable store or signed tokens).

### 10.2 Localhost Threat Model

**Threat 1: Unauthorized remote machine accesses Core.**
- **Protected by network boundary.** Core binds to `127.0.0.1`. Remote machines cannot connect.
- Auth layer provides defense-in-depth but is not the primary control.
- If Core is reconfigured to bind to `0.0.0.0`, this threat becomes active.

**Threat 2: Another local process under the same Windows user accesses Core.**
- **Partially protected.** The `.local_secret` file is in `%APPDATA%`, which is user-specific. Another process running as the same user CAN read the file.
- If an attacker has a process running as the same user, they can read the secret, request a session, and call the API.
- **This is a fundamental limitation of localhost-only systems.** If another process of the same user is malicious, the machine is already compromised.
- **Acceptable for V1.** The OS user session is the trust boundary.

**Threat 3: Another local Windows user accesses Core.**
- **Protected.** The `.local_secret` file is protected by NTFS ACLs — only the file owner (the Windows user) can read it.
- Another Windows user cannot read the secret, cannot obtain a session.
- Core binds to `127.0.0.1`, which is accessible to all local users, but without a valid session, all authenticated endpoints return `401`.

**Threat 4: Malware fully controlling the user's OS session.**
- **Not protected.** Malware running as the user can read the `.local_secret` file, request sessions, and call APIs.
- This is a fundamental limitation. No local-only system can protect against a compromised OS.
- **Documented as residual risk.**

### 10.3 Threat-Specific Mitigations

| Threat | Mitigation | Residual Risk |
|--------|-----------|---------------|
| Remote access | `127.0.0.1` binding + CORS restriction | None if binding is not changed |
| Same-user local process | `.local_secret` file ACLs (partial) | Full access if attacker controls user session |
| Different local user | `.local_secret` file ACLs + session auth | None |
| Malware/compromised OS | None possible | Full access |

### 10.4 Security Findings

- **Protected by design:** forged sessions (server-issued), forged caller identity (derived from session), forged agent identity (agent acts as user), memory ownership bypass (source_id enforcement), event actor spoofing (server-set), approval spoofing (server-side reconstruction).
- **Requires future control:** session theft (bearer token on localhost), same-user process access (OS-level limitation), unauthenticated API (V1 auth middleware fixes this).
- **Not relevant to V1:** cross-user data access, multi-user threats, remote authentication.

### 10.5 Residual Risks

1. **Localhost is the primary access control.** Any process on the machine running as the same user can call authenticated endpoints if it obtains a session (by reading the `.local_secret` file).
2. **Session tokens are bearer tokens.** No binding to client process ID or machine ID in V1.
3. **No audit of configuration changes.** Settings changes are not logged to the event store.
4. **CORS misconfiguration.** `main.py` uses `allow_origins=["*"]`. This must be corrected.

---

## 11. Windows Identity

### 11.1 Evaluation of Proposed Derivation

**Username (`USERNAME` environment variable):**
- **Not stable enough.** Windows users can rename their accounts. `USERNAME` reflects the current name, not a permanent identifier.
- **Acceptable for V1 display purposes only.** Should not be used as a stable identifier.

**Machine SID:**
- **Stable.** The machine SID does not change unless the machine is reinstalled.
- **Appropriate** as part of a composite identifier.

**Hashing machine SID:**
- **Does not provide meaningful security.** Hashing is one-way but the input is not secret. An attacker with access to the machine can compute the same hash.
- **Provides namespace isolation.** The hash ensures that machine SIDs from different machines don't collide in the user_id namespace.

**Composite identifier: `hash(machine_sid + user_sid)`:**
- **Recommended.** Machine SID + user SID together form a stable, unique identifier for the local user.
- User SID remains stable even if the username changes.

### 11.2 OS Identity vs Authentication Evidence

- **OS identity is an identifier, not authentication evidence.**
- The fact that a request comes from `user_id=local_user_abc123` does not prove the request is from the legitimate user.
- Authentication is provided by the session token, which is server-verified.
- OS identity is used for data ownership, audit trails, and memory provenance — not for access control.

### 11.3 How Core Should Obtain Identity

Core runs as the local Windows user. It can obtain identity at startup:

```python
def derive_user_identity() -> UserIdentity:
    import os
    import hashlib
    import ctypes
    from ctypes import wintypes
    
    # Machine SID
    machine_sid = _get_machine_sid()  # From registry or Win32 API
    
    # User SID (stable even if username changes)
    user_sid = _get_user_sid()  # From process token
    
    # Composite identifier
    composite = f"{machine_sid}:{user_sid}"
    user_id_hash = hashlib.sha256(composite.encode()).hexdigest()[:16]
    
    os_username = os.environ.get("USERNAME", "unknown")
    
    return UserIdentity(
        user_id=f"local_user_{user_id_hash}",
        display_name=os_username,
        os_username=os_username,
        machine_id=hashlib.sha256(machine_sid.encode()).hexdigest()[:16],
    )
```

**Timing:** Derived once at Core startup and cached in memory. Not per-request.

### 11.4 Non-Windows Future Compatibility

```python
class UserIdentityProvider:
    def derive(self) -> UserIdentity:
        raise NotImplementedError

class WindowsUserIdentityProvider(UserIdentityProvider):
    def derive(self) -> UserIdentity:
        # Windows: machine SID + user SID
        ...

class UnixUserIdentityProvider(UserIdentityProvider):
    def derive(self) -> UserIdentity:
        # Unix: machine-id + UID
        ...
```

V1 implements Windows only. The abstraction allows future platforms.

---

## 12. Session Lifecycle

### 12.1 Session Creation Authority

- **Who creates:** Core (server-side).
- **Who requests:** Desktop app (client-side), using the bootstrap secret.
- **Where state lives:** Core in-memory `dict[str, Session]`.
- **Authoritative:** Core is the sole authority. The desktop app cannot create, modify, or forge sessions.

### 12.2 Session Verification Authority

- **Who verifies:** Core `AuthMiddleware`.
- **Mechanism:** Lookup in in-memory session store + expiry checks.
- **Authoritative:** Core is the sole authority. If a session is not in the store, it is invalid.

### 12.3 Session Expiration

| Timeout | Field | Default | Check |
|---------|-------|---------|-------|
| Absolute | `expires_at` | 24 hours from `created_at` | `now >= expires_at` |
| Idle | `last_accessed` | 24 hours from last request | `now - last_accessed > 24h` |

Both are checked on each request. Either condition triggers expiry.

### 12.4 Session Revocation

- Core can remove a session from the in-memory store at any time.
- Desktop app can discard its local copy (no server-side effect).
- No explicit revocation endpoint in V1.

### 12.5 Core Restart

- All in-memory sessions are lost.
- Desktop app must re-bootstrap using `.local_secret`.
- No session migration.

### 12.6 Desktop Restart

- Desktop app reads `.local_secret` and requests a new session.
- If Core is still running from before, the old sessions are still valid until expired.
- Desktop app can attempt to reuse the old session first; if rejected, re-bootstrap.

### 12.7 Concurrent Desktop Requests

- Multiple requests with the same `session_id` are handled concurrently.
- `last_accessed` updates are idempotent (last write wins).
- No race conditions in V1 (single user, sequential requests from one desktop app).

### 12.8 Invalid Token

- Format invalid (`ses_` prefix missing, non-hex characters, wrong length) → `401 Unauthorized`.
- Not found in store → `401 Unauthorized`.
- Response body: `{"error": "Invalid or expired session"}`.

### 12.9 Expired Token

- `401 Unauthorized`.
- Response body: `{"error": "Session expired"}`.
- Desktop app should re-bootstrap.

### 12.10 Logout

- Desktop app discards `session_id` locally.
- No server-side action (session will expire naturally).
- Future: explicit logout endpoint that removes session from store.

### 12.11 Token Rotation

- Not implemented in V1.
- Future: periodic rotation with grace period (old and new tokens valid during transition).

---

## 13. Request Context

### 13.1 Authoritative Fields

```python
class UserIdentity:
    user_id: str           # Authoritative: derived once by Core at startup
    display_name: str      # Authoritative: from OS
    os_username: str       # Authoritative: from OS
    machine_id: str        # Authoritative: from OS

class Session:
    session_id: str        # Authoritative: server-generated
    user_id: str           # Authoritative: set by server
    created_at: datetime   # Authoritative: set by server
    expires_at: datetime   # Authoritative: set by server
    last_accessed: datetime # Authoritative: set by server

class RequestContext:
    user_id: str | None    # Authoritative: from session (HTTP) or None (system)
    actor_type: str        # Authoritative: set by AuthMiddleware or internal caller
    actor_id: str          # Authoritative: derived from actor_type + user_id/service_id
    session_id: str | None # Authoritative: from session (HTTP) or None (system)
    service_name: str | None # Authoritative: set by internal caller, never from HTTP
```

### 13.2 Derived Fields

- `actor_id` for `actor_type="user"` is derived as `user_id`.
- `actor_id` for `actor_type="agent"` is derived as `user_id` (agent acts for user).
- `actor_id` for `actor_type="system"` is derived as `service_name`.
- `actor_id` for `actor_type="service"` is derived as `service_name`.

### 13.3 Untrusted Inputs

The following MUST NOT be accepted from HTTP input:

- `user_id` — not from request JSON, query params, or headers
- `actor_type` — not from HTTP input for authenticated requests
- `actor_id` — not from HTTP input for authenticated requests
- `service_name` — not from HTTP input (set by internal callers only)
- `caller_id` — not from HTTP input (derived from `RequestContext`)

---

## 14. Data Model

### 14.1 User Identity (in-memory, derived at startup)

```python
class UserIdentity:
    user_id: str
    display_name: str
    os_username: str
    machine_id: str
```

**No database table in V1.** Derived once at Core startup.

### 14.2 Bootstrap Secret (local file, protected by OS ACLs)

**File:** `%APPDATA%/AI-Operator/.local_secret`
**Format:** 64 hex characters (32 bytes random)
**Generated:** Once at Core first startup
**Loaded:** Into Core memory at startup

**No database table.**

### 14.3 Session (in-memory, ephemeral)

```python
class Session:
    session_id: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    last_accessed: datetime
```

**Storage:** `dict[str, Session]` in Core memory.
**No database table in V1.**

### 14.4 Request Context (per-request)

```python
class RequestContext:
    user_id: str | None
    actor_type: str
    actor_id: str
    session_id: str | None
    service_name: str | None
```

**No persistence.** Built per-request by `AuthMiddleware` or internal callers.

### 14.5 Modified Existing Models

**MemoryCaller** (behavior change, no schema change):
- `caller_id` MUST be a valid `user_id` or `service_id` when `caller_type` is USER, SYSTEM, or AGENT.
- `source_id` defaults to `caller_id` when not explicitly provided.

**Event.actor** (no schema change):
- Must be set from authenticated context for user-facing events.
- System events use hardcoded service identifiers.

**PermissionEvaluationRequest** (optional field):
- Add `actor_id: str | None` for audit logging.

---

## 15. Proposed Request Flow

### 15.1 Bootstrap Flow

```
Desktop App starts
  ↓
Read %APPDATA%/AI-Operator/.local_secret
  ↓
POST /api/auth/session
  Header: X-Local-Secret: <secret>
  ↓
Core validates secret
  ↓
Core creates Session in memory
  ↓
Response: { session_id, expires_at }
  ↓
Desktop app stores session_id
```

### 15.2 Authenticated Request Flow

```
Desktop App
  ↓ HTTP + Authorization: Bearer ses_<random>
AuthMiddleware
  ↓ 1. Extract session token
   2. Validate format
   3. Look up session in store
   4. Check absolute expiry
   5. Check idle timeout
   6. Update last_accessed
   7. Build RequestContext
  ↓
Route Handler
  ↓ request.state.context
Service Layer
  ↓ caller_from_context(context)
MemoryService / ToolExecutionService / etc.
```

### 15.3 System-to-System Request

```
SchedulerWorker
  ↓ No session (internal)
RequestContext(
    actor_type="system",
    actor_id="scheduler",
    user_id=None,
    session_id=None,
    service_name="scheduler"
)
```

### 15.4 Agent Loop Request

```
AIService.chat()
  ↓ RequestContext(
      actor_type="agent",
      actor_id=user_id,  # agent acts for user
      user_id=user_id,
      session_id=session_id
    )
ToolExecutionService
  ↓ PermissionEngine.evaluate(actor_id=user_id)
```

---

## 16. Acceptance Criteria

1. **Server-issued sessions:** Core can distinguish an actually issued session from an arbitrary client-generated token.
2. **Session store:** Session validity is determined by an authoritative in-memory server-side store, not by token format alone.
3. **Session expiry:** Absolute timeout (24h) and idle timeout (24h) are enforced with authoritative server-side timestamps.
4. **Caller identity:** `caller_id` is derived from authenticated `RequestContext`, not from HTTP input.
5. **Actor identity:** `actor_type` and `actor_id` are set by Core for authenticated requests, not supplied by the client.
6. **User identity:** `user_id` is derived from OS context at Core startup, not accepted from request JSON.
7. **Service identity:** `service_name` is set by internal callers only, never from HTTP input.
8. **PermissionEngine:** Remains the authorization authority. `actor_id` is informational/audit only.
9. **ApprovalService:** Uses server-side `RequestContext`, not client-provided actor identity.
10. **Memory ownership:** `source_id` is enforced for USER/SYSTEM/AGENT memories, eliminating `None` fallback weakness.
11. **Localhost limitations:** Explicitly documented as the primary network boundary.
12. **Design size:** Remains small enough for single-user local V1 (no passwords, no OAuth, no multi-user, no durable store).

---

## 17. Open Questions

1. **Session persistence:** Should V1 use in-memory only, or a simple file-based session store?
2. **Machine ID derivation:** Windows SID is reliable, but what about non-Windows?
3. **Agent identity:** Should the agent have its own identity in V1, or always act as the user?
4. **Settings ownership:** Should settings changes be attributed to `user_id` in V1?
5. **Conversation ownership:** Should conversations be scoped to `user_id` in V1?
6. **Task ownership:** Should `project_id` evolve into `user_id/project_id`?
7. **Event replay:** Should events include `user_id` for filtering, or rely on `actor`?
8. **CORS correction:** Should `main.py` be updated to use `config.py`'s `cors_origins`?

---

## 18. Final Contract

**STAGE_16_1_DISCOVERY_COMPLETE — STAGE_16_1_1_SECURITY_CORRECTED**

### Current Authentication State
- **None.** All API endpoints are unauthenticated.
- `MemoryCaller` uses arbitrary strings, not real identities.
- `KiloSession` tracks Kilo CLI executions, not user sessions.
- Events use manually-set `actor` fields.
- Desktop app makes plain HTTP calls with no auth headers.
- CORS is misconfigured (`allow_origins=["*"]`).

### Proposed V1 Identity Model
- **Single local user** derived from OS context (Windows user SID + machine SID hash).
- **OS identity is an identifier, not authentication evidence.**
- No passwords, no OAuth, no multi-user, no remote auth.
- Identity answers "who is asking" only — it does NOT authorize.

### Proposed Session Model
- **Server-issued, in-memory, 24-hour absolute + idle expiry.**
- Bootstrap via local file secret (`%APPDATA%/AI-Operator/.local_secret`).
- Desktop app requests session using `X-Local-Secret` header.
- Core validates secret, creates session, returns `session_id`.
- All subsequent requests use `Authorization: Bearer <session_id>`.
- Core is the sole authority for session validity.

### Authentication Strength
- **Genuine server-verifiable authentication.** Core maintains authoritative session store.
- Client cannot forge a session without reading the local secret file.
- Local secret file is protected by OS ACLs (Windows user-only).

### Memory Integration
- `MemoryCaller.caller_id` is derived from authenticated identity, not caller input.
- `source_id` defaults to `user_id` for USER, `service_id` for SYSTEM, `user_id` for AGENT.
- Eliminates V1 weakness where `source_id=None` allows ownership bypass.

### Event Integration
- `Event.actor` populated from authenticated context.
- System events use hardcoded service identifiers.
- No secrets in events.

### Permission Integration
- `PermissionEngine` receives `actor_id` for audit only.
- Authorization decisions unchanged — still based on tool_id, action, risk, grants.
- Identity is NOT a second authorization system.
- `ApprovalService` uses server-side `RequestContext`, not client-provided actor identity.

### API Security
- Add `AuthMiddleware` that validates bearer sessions.
- Protect settings, AI, plans, history, scheduler endpoints.
- `POST /api/auth/session` is the only unauthenticated endpoint, protected by `X-Local-Secret`.
- Health and provider list endpoints remain open.
- Localhost-only remains the primary network boundary.
- **CORS must be corrected** to use `config.py`'s `cors_origins`.

### Security Findings
- **Protected by design:** forged sessions (server-issued), forged caller identity (derived from session), forged agent identity, memory ownership bypass (with V1 fix), event actor spoofing (server-set), approval spoofing (server-side reconstruction).
- **Protected against:** different local Windows users (file ACLs + session auth).
- **Requires future control:** session theft (bearer token on localhost), same-user process access (OS-level limitation), unauthenticated API (V1 middleware fixes this).
- **Not protected:** malware with full OS control (fundamental limitation of local-only systems).

### Unresolved Questions
1. Session persistence strategy (in-memory vs file-based)
2. Machine ID derivation for non-Windows platforms
3. Whether agent should have independent identity in V1
4. Settings/audit attribution to user
5. Conversation and task ownership scoping
6. CORS correction in `main.py`

---

*This document is the sole output of Stage 16.1 and 16.1.1. No production code was modified. No implementation was started. This is a contract document only.*
