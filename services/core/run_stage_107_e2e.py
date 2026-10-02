"""
Stage 10.7 — Kilo + OpenRouter Real Autonomous Coding E2E

Disposable isolated test. Does NOT modify the production database.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

KILO_EXECUTABLE = r"C:\Users\adamz\.vscode\extensions\kilocode.kilo-code-7.7.2-win32-x64\bin\kilo.exe"
OPENROUTER_MODEL = "openrouter/cohere/north-mini-code:free"
TASK_DESCRIPTION = (
    "Create a Python project with:\n"
    "1. calculator.py containing: def add(a: int, b: int) -> int: return a + b\n"
    "2. tests/ directory with test_calculator.py containing pytest tests:\n"
    "   - test_add_positive: assert add(2, 3) == 5\n"
    "   - test_add_negative: assert add(-1, 1) == 0\n"
    "Make sure pytest passes."
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def init_schema(conn: sqlite3.Connection) -> None:
    """Initialize minimal schema for the E2E test."""
    # Read schema from the actual migration or create minimal tables
    # For the test, we'll use the actual SQLAlchemy models
    pass


async def run_e2e() -> None:
    # Create temp directories
    temp_dir = Path(tempfile.mkdtemp(prefix="stage_107_e2e_"))
    repo_root = temp_dir
    db_path = temp_dir / "test.db"

    print(f"[E2E] Temp directory: {temp_dir}")
    print(f"[E2E] Repo root: {repo_root}")
    print(f"[E2E] Database: {db_path}")

    # Initialize Git repo
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True, capture_output=True)
    (repo_root / ".gitignore").write_text("test.db\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True, capture_output=True)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True
    ).stdout.strip()
    print(f"[E2E] Base commit: {base_commit}")

    # Initialize database with SQLAlchemy
    sys.path.insert(0, str(Path(__file__).parent))
    os.chdir(Path(__file__).parent)

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
    from app.db.database import Base
    from app.services.settings_service import SettingsService
    from app.repositories.task_repository import TaskRepository
    from app.tasks.models import DevelopmentTask, TaskStatus
    from app.tasks.state_machine import TaskStateMachine
    from app.workspace.workspace_manager import WorkspaceManager
    from app.verification.check_registry import CheckRegistry
    from app.verification.checks.pytest_check import PytestCheck
    from app.verification.gate import VerificationGate
    from app.orchestration.agent_factory import DevelopmentAgentFactory
    from app.ai.services.provider_manager import ProviderManager
    from app.orchestration.orchestrator import AutonomousTaskOrchestrator
    from app.orchestration.run_controller import AutonomousRunController
    from app.services.plan_execution_service import PlanExecutionService

    # Create async engine
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, connect_args={"timeout": 30.0})

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Configure settings in a separate session
    async with session_factory() as settings_session:
        settings_svc = SettingsService(settings_session)
        await settings_svc.ensure_defaults()
        await settings_svc.update({
            "ai_provider": "kilo",
            "kilo_executable_path": KILO_EXECUTABLE,
            "ai_selected_model": OPENROUTER_MODEL,
            "kilo_auto_approve": "true",
        })
        await settings_session.commit()
        config = await settings_svc.get_all()
        print(f"[E2E] Provider: {config.get('ai_provider')}")
        print(f"[E2E] Kilo path: {config.get('kilo_executable_path')}")
        print(f"[E2E] Model: {config.get('ai_selected_model')}")

    # Create task in a fresh session
    async with session_factory() as session:
        task_repo = TaskRepository(session)
        task = DevelopmentTask(
            task_id="task_calc_001",
            plan_id="plan_calc_001",
            task_key="calc_add",
            title="Add calculator with tests",
            description=TASK_DESCRIPTION,
            status=TaskStatus.PENDING,
            attempt_count=0,
            max_attempts=3,
            project_id="default",
        )
        await task_repo.create_tasks_idempotent(
            plan_id="plan_calc_001",
            tasks=[task],
            dependencies=[],
            plan_fingerprint="fp_calc_001",
        )
        await session.commit()
        print(f"[E2E] Created task: task_calc_001")

    # Set up components
    async with session_factory() as session:
        task_repo = TaskRepository(session)
        state_machine = TaskStateMachine(task_repo)
        ws_manager = WorkspaceManager(repo_root=repo_root)

        # Register verification checks
        check_registry = CheckRegistry()
        check_registry.register(PytestCheck())

        gate = VerificationGate(
            registry=check_registry,
            primary_worktree_path=repo_root,
        )

        # Create agent factory
        settings_svc = SettingsService(session)
        provider_mgr = ProviderManager(settings_svc)
        factory = DevelopmentAgentFactory(provider_manager=provider_mgr)

        # Create orchestrator
        orchestrator = AutonomousTaskOrchestrator(
            task_repo=task_repo,
            state_machine=state_machine,
            workspace_manager=ws_manager,
            verification_gate=gate,
            agent_factory=factory,
        )

        # Create run controller
        run_ctrl = AutonomousRunController(
            task_repo=task_repo,
            state_machine=state_machine,
            workspace_manager=ws_manager,
            verification_gate=gate,
            orchestrator=orchestrator,
            default_timeout_seconds=300.0,
        )

        print("[E2E] Starting autonomous run...")
        start_time = time.monotonic()

        try:
            result = await run_ctrl.start_run(
                task_id="task_calc_001",
                timeout_seconds=300.0,
            )
            duration = time.monotonic() - start_time
            print(f"[E2E] Run completed in {duration:.1f}s")
            print(f"[E2E] Run status: {result.status}")
            print(f"[E2E] Task status: {result.task_status}")
            print(f"[E2E] Verification passed: {result.verification_passed}")
            print(f"[E2E] Commit SHA: {result.commit_sha}")
            print(f"[E2E] Failure reason: {result.failure_reason}")

            # Verify results
            if result.status.value == "SUCCEEDED":
                print("[E2E] [OK] Run SUCCEEDED")

                # Check files were created
                task = await task_repo.get_task("task_calc_001")
                if task and task.workspace_path:
                    ws_path = Path(task.workspace_path)
                    calc_file = ws_path / "calculator.py"
                    test_file = ws_path / "tests" / "test_calculator.py"

                    if calc_file.exists() and test_file.exists():
                        print("[E2E] [OK] calculator.py exists")
                        print("[E2E] [OK] tests/test_calculator.py exists")
                        print("[E2E] calculator.py content:")
                        print(calc_file.read_text())
                        print("[E2E] test_calculator.py content:")
                        print(test_file.read_text())
                    else:
                        print("[E2E] [FAIL] Expected files not found")
            else:
                print(f"[E2E] [FAIL] Run did not succeed: {result.status}")

        except Exception as exc:
            print(f"[E2E] [FAIL] Exception: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            await session.commit()

    await engine.dispose()
    print(f"[E2E] Temp directory preserved for inspection: {temp_dir}")


if __name__ == "__main__":
    asyncio.run(run_e2e())
