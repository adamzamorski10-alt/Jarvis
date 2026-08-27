# AI Operator

AI Operator is a Windows desktop foundation for a local personal agent system. Phase 1 provides a Tauri + React desktop shell, a Python FastAPI Core, SQLite persistence, settings, activity events, and live Core status.

## Requirements

- Windows 10/11
- Node.js 20+
- Python 3.12+
- Rust toolchain and Tauri prerequisites for the native desktop build

## Development

Start the Core in one terminal:

```powershell
cd services/core
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Start the desktop UI in another terminal:

```powershell
cd apps/desktop
npm install
npm run dev
```

For the actual Tauri desktop window use `npm run tauri dev` from `apps/desktop`. In development Tauri starts Python from `services/core`; in release it starts the bundled `ai-operator-core` PyInstaller sidecar and stops it when the window exits.

## Testing

```powershell
cd services/core
pytest -q

cd ../../apps/desktop
npm test
npm run build
npm run tauri -- build
```

The Core API runs on a dynamic localhost port managed by Tauri. The frontend obtains it through the `get_core_url` command; do not hardcode a fixed Core port. Interactive docs are enabled when `AI_OPERATOR_DEBUG=true`.

The production build requires the Windows Rust/Tauri toolchain and PyInstaller. The Core database is placed in the Tauri application data directory in a packaged build via `AI_OPERATOR_DB_PATH`.

## Scope

Current scope includes the desktop shell, FastAPI Core, SQLite persistence, activity events, Ollama support, and Kimi Cloud provider configuration. Agents, tools, browser automation, shell execution, email, and autonomous actions remain out of scope until later phases. See [ROADMAP.md](ROADMAP.md) for the planned sequence.
