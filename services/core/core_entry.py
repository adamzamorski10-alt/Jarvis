"""
PyInstaller entrypoint for the AI Operator Core sidecar.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import threading
import time
import traceback

import uvicorn


def process_exists(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    process_query_limited_information = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
    if handle:
        exit_code = ctypes.c_ulong()
        if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            ctypes.windll.kernel32.CloseHandle(handle)
            return exit_code.value == still_active
        ctypes.windll.kernel32.CloseHandle(handle)
    return False


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(log_dir: Path | None, filename: str, message: str) -> None:
    if not log_dir:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / filename).open("a", encoding="utf-8") as file:
            file.write(f"{timestamp()} {message}\n")
    except Exception:
        pass


def start_parent_watchdog(parent_pid: int | None) -> None:
    if not parent_pid:
        return

    def watch() -> None:
        while True:
            time.sleep(1)
            if not process_exists(parent_pid):
                os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Operator Core sidecar")
    parser.add_argument("--host", default=os.getenv("AI_OPERATOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AI_OPERATOR_PORT", "0")))
    parser.add_argument("--db-path", default=os.getenv("AI_OPERATOR_DB_PATH"))
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--log-dir", default=os.getenv("AI_OPERATOR_LOG_DIR"))
    args = parser.parse_args()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("core.log", "core-error.log", "startup.log"):
            (log_dir / filename).touch(exist_ok=True)

    append_log(
        log_dir,
        "startup.log",
        f"[Core] entrypoint pid={os.getpid()} parent_pid={args.parent_pid} host={args.host} port={args.port} db_path={args.db_path}",
    )

    os.environ["AI_OPERATOR_HOST"] = args.host
    os.environ["AI_OPERATOR_PORT"] = str(args.port)
    if log_dir:
        os.environ["AI_OPERATOR_LOG_DIR"] = str(log_dir)
    if args.db_path:
        os.environ["AI_OPERATOR_DB_PATH"] = args.db_path
    start_parent_watchdog(args.parent_pid)

    try:
        append_log(log_dir, "core.log", f"[Core] starting uvicorn on http://{args.host}:{args.port}")
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            log_level=os.getenv("AI_OPERATOR_LOG_LEVEL", "info").lower(),
            access_log=True,
        )
        append_log(log_dir, "core.log", "[Core] uvicorn stopped cleanly")
    except Exception:
        append_log(log_dir, "core-error.log", traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
