# Architecture

```text
Tauri desktop shell
        |
React + TypeScript UI
        |
Typed local HTTP client
        |
Python FastAPI Core
        |
Services and repositories
        |
SQLite (aiosqlite)
```

The desktop application owns presentation, navigation, user interaction, and connection state. The Core owns business rules, validation, persistence, lifecycle logging, and API contracts. The UI never reads SQLite directly.

## Phase 1 boundaries

- `app/api`: HTTP routes and Pydantic request/response contracts.
- `app/services`: business operations for settings and activity.
- `app/repositories`: database access.
- `app/db`: SQLAlchemy models and async sessions.
- `app/core`: configuration, logging, and startup/shutdown lifecycle.
- `apps/desktop/src/services`: the only frontend boundary to the Core API.

## Desktop lifecycle

In development, Tauri starts `python -m uvicorn` from `services/core`. In a release build, the Tauri Shell plugin starts the PyInstaller executable bundled as `ai-operator-core-<target-triple>.exe`. Rust retains the process handle and terminates it on application exit. React polls `/api/system/status`, which performs a real SQLite query; process spawn alone is never treated as ready.

The packaged Core receives `AI_OPERATOR_DB_PATH` pointing to the Tauri application data directory. This keeps user data outside the project directory and avoids relying on `PYTHONPATH` or the development working directory.