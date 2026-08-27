# Security Notes

Phase 1 is a local-only foundation.

- The Core binds to `127.0.0.1` by default.
- CORS is limited to local development origins.
- No secrets, tokens, passwords, or external integrations are implemented.
- The frontend communicates through typed API functions and never accesses the database directly.
- Future tools must pass through explicit risk and permission checks before execution.
- Activity events provide the initial audit trail, but are not yet a complete security audit system.