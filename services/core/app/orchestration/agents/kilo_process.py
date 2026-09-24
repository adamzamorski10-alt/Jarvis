"""
Kilo Process Helper — narrow subprocess lifecycle for KiloDevelopmentAgent.

SECURITY:
- Accepts only application-owned parameters.
- Does NOT accept arbitrary commands.
- Does NOT invoke shell=True.
- Does NOT perform Git, permission, approval, or verification operations.
- Bounded stdout/stderr capture.
- Process tree termination on Windows via taskkill /T /F.
- Orphan detection for previously-tracked Kilo PIDs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_OUTPUT_BYTES = 1_048_576  # 1 MiB

# Track PIDs launched by this process for orphan detection.
# This is module-level state; in a multi-process deployment,
# persistent storage would be required.
_ACTIVE_KILO_PIDS: set[int] = set()
_KILO_PID_LOCK = asyncio.Lock()


class KiloProcessError(Exception):
    """Base exception for Kilo process errors."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Kilo process error: {reason}")


class KiloLifecycleState:
    """Lifecycle states for Kilo subprocess execution."""

    NOT_STARTED = "NOT_STARTED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"


class KiloProcessResult:
    """Outcome of a Kilo subprocess execution."""

    __slots__ = (
        "returncode",
        "stdout_text",
        "stderr_text",
        "cancelled",
        "termination_confirmed",
        "lifecycle_state",
        "started_at",
        "terminated_at",
        "duration_seconds",
    )

    def __init__(
        self,
        *,
        returncode: int | None,
        stdout_text: str = "",
        stderr_text: str = "",
        cancelled: bool = False,
        termination_confirmed: bool = False,
        lifecycle_state: str = KiloLifecycleState.TERMINATED,
        started_at: float | None = None,
        terminated_at: float | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout_text = stdout_text
        self.stderr_text = stderr_text
        self.cancelled = cancelled
        self.termination_confirmed = termination_confirmed
        self.lifecycle_state = lifecycle_state
        self.started_at = started_at
        self.terminated_at = terminated_at
        self.duration_seconds = duration_seconds


async def _read_stream(stream: asyncio.StreamReader | None, max_bytes: int) -> bytes:
    """Read from stream up to max_bytes, return captured bytes."""
    if stream is None:
        return b""
    chunks = []
    total = 0
    while True:
        try:
            chunk = await stream.read(65536)
        except (ConnectionResetError, BrokenPipeError, OSError):
            break
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    data = b"".join(chunks)
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data


async def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running (Windows-compatible)."""
    if pid <= 0:
        return False
    try:
        if platform.system() == "Windows":
            result = await asyncio.to_thread(
                subprocess.run,
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout.strip()
            if not output or "INFO: No tasks" in output:
                return False
            return str(pid) in output
        else:
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, OSError, FileNotFoundError):
        return False
    except Exception:
        return False


async def _is_process_alive_via_tasklist(pid: int) -> bool:
    """Check process existence via tasklist (more reliable on Windows after termination)."""
    if pid <= 0:
        return False
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        return await _is_process_alive(pid)


async def _terminate_process_tree(pid: int, kill_timeout: float = 15.0) -> bool:
    """
    Terminate a process and its descendants.

    On Windows, uses taskkill /T /F for process tree termination.
    Falls back to proc.kill() on non-Windows platforms.
    Returns True if termination is confirmed.
    """
    if platform.system() == "Windows":
        try:
            taskkill_proc = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(asyncio.shield(taskkill_proc.wait()), timeout=kill_timeout)
            except asyncio.TimeoutError:
                pass

            # Poll for actual termination with a short delay.
            # Windows may report a PID as alive briefly after termination
            # before the OS reaps it. Use tasklist for more reliable checks.
            alive_check = _is_process_alive_via_tasklist if platform.system() == "Windows" else _is_process_alive
            for _ in range(10):
                if not await alive_check(pid):
                    return True
                await asyncio.sleep(0.3)
            return not await alive_check(pid)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 9)
            await asyncio.sleep(0.1)
            return not await _is_process_alive(pid)
        except (ProcessLookupError, OSError):
            return True
        except Exception:
            return False


async def _register_kilo_pid(pid: int | None) -> None:
    """Register a Kilo PID for orphan tracking."""
    if pid is None or pid <= 0:
        return
    if not await _is_process_alive(pid):
        return
    async with _KILO_PID_LOCK:
        _ACTIVE_KILO_PIDS.add(pid)


async def _terminate_orphaned_kilos(kill_timeout: float = 15.0) -> None:
    """Terminate any orphaned Kilo processes from previous runs."""
    async with _KILO_PID_LOCK:
        pids_to_kill = list(_ACTIVE_KILO_PIDS)
        _ACTIVE_KILO_PIDS.clear()

    for pid in pids_to_kill:
        if await _is_process_alive(pid):
            logger.warning("Terminating orphaned Kilo process PID %s", pid)
            await _terminate_process_tree(pid, kill_timeout=kill_timeout)


def build_kilo_args(
    *,
    executable: str,
    workspace_path: str,
    task_description: str,
    pure: bool = True,
    json_format: bool = True,
    model: str | None = None,
    auto: bool = False,
) -> list[str]:
    """Build Kilo CLI arguments from trusted application parameters."""
    if not executable or not isinstance(executable, str):
        raise KiloProcessError("executable must be a non-empty string")
    if not workspace_path or not isinstance(workspace_path, str):
        raise KiloProcessError("workspace_path must be a non-empty string")
    if not task_description or not isinstance(task_description, str):
        raise KiloProcessError("task_description must be a non-empty string")

    resolved_workspace = Path(workspace_path).resolve()
    if not resolved_workspace.is_dir():
        raise KiloProcessError(
            f"workspace_path must be an existing directory: {workspace_path}"
        )

    args = [executable, "run"]
    if json_format:
        args.extend(["--format", "json"])
    if pure:
        args.append("--pure")
    if auto:
        args.append("--auto")
    args.extend(["--dir", str(resolved_workspace)])
    if model:
        args.extend(["-m", model])
    args.append(task_description)
    return args


async def start_kilo_process(args: list[str]) -> asyncio.subprocess.Process:
    """Start Kilo subprocess. Returns process object without waiting."""
    if not args:
        raise KiloProcessError("args must not be empty")

    # Clean up any previously tracked orphaned Kilo processes before starting.
    await _terminate_orphaned_kilos()

    logger.debug("Starting Kilo process: %s", " ".join(args))

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise KiloProcessError(f"Kilo executable not found: {exc}") from exc
    except OSError as exc:
        raise KiloProcessError(f"Failed to start Kilo process: {exc}") from exc

    await _register_kilo_pid(getattr(proc, "pid", None))

    return proc


async def wait_kilo_process(
    proc: asyncio.subprocess.Process,
    *,
    cancel_event: asyncio.Event | None = None,
    kill_timeout: float = 15.0,
) -> KiloProcessResult:
    """
    Wait for Kilo process to completion, respecting cancellation.

    Lifecycle: STARTING -> RUNNING -> TERMINATING -> TERMINATED

    Polls cancel_event; if set, kills the process.
    If process cannot be terminated within kill_timeout, returns with
    termination_confirmed=False.
    """
    started_at = time.monotonic()
    lifecycle_state = KiloLifecycleState.STARTING

    if proc.returncode is not None:
        # Process already completed before we started waiting.
        lifecycle_state = KiloLifecycleState.TERMINATED
        stdout = await _read_stream(proc.stdout, _MAX_OUTPUT_BYTES)
        stderr = await _read_stream(proc.stderr, _MAX_OUTPUT_BYTES)
        return KiloProcessResult(
            returncode=proc.returncode,
            stdout_text=stdout.decode("utf-8", errors="replace"),
            stderr_text=stderr.decode("utf-8", errors="replace"),
            termination_confirmed=True,
            lifecycle_state=lifecycle_state,
            started_at=started_at,
            terminated_at=started_at,
            duration_seconds=0.0,
        )

    lifecycle_state = KiloLifecycleState.RUNNING
    cancelled = False
    termination_confirmed = False

    try:
        while proc.returncode is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                lifecycle_state = KiloLifecycleState.TERMINATING
                proc.kill()
                try:
                    await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=kill_timeout)
                    termination_confirmed = True
                except asyncio.TimeoutError:
                    termination_confirmed = False
                break

            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=0.5)
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        if proc.returncode is None:
            lifecycle_state = KiloLifecycleState.TERMINATING
            proc.kill()
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=kill_timeout)
                termination_confirmed = True
            except (asyncio.TimeoutError, asyncio.CancelledError):
                termination_confirmed = False
        raise

    terminated_at = time.monotonic()
    duration = terminated_at - started_at

    lifecycle_state = KiloLifecycleState.TERMINATED
    # If termination was requested but not confirmed via proc.wait(),
    # try process-tree termination as fallback (Windows taskkill).
    if (cancelled or lifecycle_state == KiloLifecycleState.TERMINATING) and not termination_confirmed:
        pid = getattr(proc, "pid", None)
        if pid and pid > 0:
            termination_confirmed = await _terminate_process_tree(pid, kill_timeout=kill_timeout)
            if termination_confirmed:
                proc.returncode = -9

    stdout = await _read_stream(proc.stdout, _MAX_OUTPUT_BYTES)
    stderr = await _read_stream(proc.stderr, _MAX_OUTPUT_BYTES)

    return KiloProcessResult(
        returncode=proc.returncode,
        stdout_text=stdout.decode("utf-8", errors="replace"),
        stderr_text=stderr.decode("utf-8", errors="replace"),
        cancelled=cancelled,
        termination_confirmed=termination_confirmed,
        lifecycle_state=lifecycle_state,
        started_at=started_at,
        terminated_at=terminated_at,
        duration_seconds=duration,
    )
