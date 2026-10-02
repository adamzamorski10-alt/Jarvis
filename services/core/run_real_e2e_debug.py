import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KILO_EXECUTABLE = r"C:\Users\adamz\.vscode\extensions\kilocode.kilo-code-7.7.2-win32-x64\bin\kilo.exe"
TASK_DESCRIPTION = (
    "Create a Python module `text_stats.py` containing:\n"
    "```python\n"
    "def count_characters(text: str) -> int:\n"
    '    """Return the number of characters in text."""\n'
    "    if not text:\n"
    "        return 0\n"
    "    return len(text)\n"
    "\n"
    "def count_words(text: str) -> int:\n"
    '    """Return the number of words in text."""\n'
    "    if not text or not text.strip():\n"
    "        return 0\n"
    "    return len(text.split())\n"
    "\n"
    "def count_lines(text: str) -> int:\n"
    '    """Return the number of lines in text."""\n'
    "    if not text:\n"
    "        return 0\n"
    "    return len(text.splitlines())\n"
    "```\n"
    "Add `tests/test_text_stats.py` containing pytest tests proving:\n"
    "- count_characters('') == 0\n"
    "- count_characters('abc') == 3\n"
    "- count_words('') == 0\n"
    "- count_words('hello world') == 2\n"
    "- count_words('  ') == 0\n"
    "- count_lines('') == 0\n"
    "- count_lines('a\\nb\\nc') == 3\n"
    "Make sure pytest passes.\n"
)

ACCEPTANCE_CRITERIA = [
    "Implement count_characters, count_words, count_lines in text_stats.py",
    "Handle empty input correctly",
    "Handle whitespace correctly",
    "Add comprehensive pytest coverage",
    "All tests must pass",
]

def init_repo(repo_root: Path) -> str:
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True, capture_output=True)
    (repo_root / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True, capture_output=True)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return base_commit

async def run_e2e() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="real_e2e_debug_"))
    repo_root = temp_dir / "disposable_repo"
    repo_root.mkdir()
    db_path = temp_dir / "test.db"

    print(f"[E2E] Temp directory: {temp_dir}")
    print(f"[E2E] Repo root: {repo_root}")
    print(f"[E2E] Database: {db_path}")

    base_commit = init_repo(repo_root)
    print(f"[E2E] Base commit: {base_commit}")

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
    from app.orchestration.prompt_compiler import DevelopmentManager
    from app.orchestration.orchestrator import AutonomousTaskOrchestrator
    from app.orchestration.run_controller import AutonomousRunController
    from app.services.plan_execution_service import PlanExecutionService
    from app.services.autonomous_loop import AutonomousPlanExecutor
    from app.manager.models import ManagerPlanResult, ProviderTaskProposal, compute_task_id, compute_plan_fingerprint
    from app.services.plan_service import PlanService
    from app.orchestration.models import OrchestrationOptions

    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, connect_args={"timeout": 30.0})

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as settings_session:
        settings_svc = SettingsService(settings_session)
        await settings_svc.ensure_defaults()
        await settings_svc.update({
            "ai_provider": "kilo",
            "kilo_executable_path": KILO_EXECUTABLE,
            "ai_selected_model": "openrouter/deepseek/deepseek-chat",
            "kilo_auto_approve": "true",
        })
        await settings_session.commit()

    config_summary = {}
    async with session_factory() as s:
        svc = SettingsService(s)
        cfg = await svc.get_all()
        for k in ["ai_provider", "kilo_executable_path", "ai_selected_model", "kilo_auto_approve"]:
            v = cfg.get(k)
            if "key" in k.lower() or "secret" in k.lower() or "password" in k.lower() or "token" in k.lower():
                config_summary[k] = "[REDACTED]" if v else v
            else:
                config_summary[k] = v
    print(f"[E2E] Configuration: {config_summary}")

    plan_id = "plan_text_stats_e2e"
    project_id = "default"
    task_key = "text_stats_impl"
    task_id = compute_task_id(plan_id, task_key)

    proposal = ProviderTaskProposal(
        proposal_key=task_key,
        title="Implement TextStats utility",
        description=TASK_DESCRIPTION,
        priority=1,
        dependencies=[],
    )

    plan_fingerprint = compute_plan_fingerprint(
        plan_id=plan_id,
        project_id=project_id,
        acceptance_criteria=ACCEPTANCE_CRITERIA,
        tasks=[],
        dependencies=[],
    )

    manager_plan_result = ManagerPlanResult(
        plan_id=plan_id,
        tasks=[
            DevelopmentTask(
                task_id=task_id,
                task_key=task_key,
                title="Implement TextStats utility",
                description=TASK_DESCRIPTION,
                status=TaskStatus.PENDING,
                attempt_count=0,
                max_attempts=3,
                project_id=project_id,
                metadata={
                    "acceptance_criteria": ACCEPTANCE_CRITERIA,
                },
            )
        ],
        task_graph={},
        plan_fingerprint=plan_fingerprint,
    )

    async with session_factory() as session:
        task_repo = TaskRepository(session)
        plan_svc = PlanService(task_repo)
        tasks = await plan_svc.persist_manager_plan_result(manager_plan_result)
        await session.commit()
        print(f"[E2E] Created plan {plan_id} with {len(tasks)} task(s)")
        print(f"[E2E] Task ID: {task_id}")

    async with session_factory() as session:
        task_repo = TaskRepository(session)
        state_machine = TaskStateMachine(task_repo)
        ws_manager = WorkspaceManager(repo_root=repo_root)

        check_registry = CheckRegistry()
        check_registry.register(PytestCheck())

        gate = VerificationGate(
            registry=check_registry,
            primary_worktree_path=repo_root,
        )

        settings_svc = SettingsService(session)
        provider_mgr = ProviderManager(settings_svc)
        factory = DevelopmentAgentFactory(provider_manager=provider_mgr)

        dev_manager = DevelopmentManager()

        orch = AutonomousTaskOrchestrator(
            task_repo=task_repo,
            state_machine=state_machine,
            workspace_manager=ws_manager,
            verification_gate=gate,
            agent_factory=factory,
            development_manager=dev_manager,
        )

        run_ctrl = AutonomousRunController(
            task_repo=task_repo,
            state_machine=state_machine,
            workspace_manager=ws_manager,
            verification_gate=gate,
            orchestrator=orch,
        )

        plan_exec_svc = PlanExecutionService(
            task_repository=task_repo,
            run_controller=run_ctrl,
        )

        executor = AutonomousPlanExecutor(
            task_repository=task_repo,
            plan_execution_service=plan_exec_svc,
        )

        print("[E2E] Starting autonomous plan execution...")
        start_time = time.monotonic()

        try:
            result = await executor.execute_plan(
                plan_id=plan_id,
                timeout_seconds=600.0,
            )
            duration = time.monotonic() - start_time
            print(f"[E2E] Plan execution completed in {duration:.1f}s")
            print(f"[E2E] Plan terminal state: {result.terminal_state}")
            print(f"[E2E] Committed tasks: {result.committed_tasks}")
            print(f"[E2E] Failed tasks: {result.failed_tasks}")
            print(f"[E2E] Reason: {result.reason}")

            if result.terminal_state.value == "COMPLETED":
                print("[E2E] [OK] Plan COMPLETED")
                task = await task_repo.get_task(task_id)
                if task and task.workspace_path:
                    ws_path = Path(task.workspace_path)
                    stats_file = ws_path / "text_stats.py"
                    test_file = ws_path / "tests" / "test_text_stats.py"
                    if stats_file.exists() and test_file.exists():
                        print("[E2E] [OK] text_stats.py exists")
                        print("[E2E] [OK] tests/test_text_stats.py exists")
                    else:
                        print("[E2E] [FAIL] Expected files not found")
            else:
                print(f"[E2E] [FAIL] Plan did not complete: {result.terminal_state}")

        except Exception as exc:
            print(f"[E2E] [FAIL] Exception: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            await session.commit()

    await engine.dispose()
    print(f"[E2E] Temp directory preserved for inspection: {temp_dir}")
    print("[E2E] Listing temp dir contents:")
    for p in sorted(temp_dir.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(temp_dir)}")

if __name__ == "__main__":
    asyncio.run(run_e2e())
