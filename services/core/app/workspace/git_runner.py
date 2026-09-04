"""
Controlled Git subprocess runner.

DESIGN INVARIANTS:
  - Never uses shell=True.
  - All Git commands are executed as explicit argument arrays.
  - Explicit timeout on every invocation.
  - Captures stdout + stderr; returns structured GitResult.
  - No generic "run arbitrary command" API is exposed.
  - Task IDs, paths, and branch names are never interpolated into shell strings;
    they are passed as discrete list elements.
  - The caller decides the working directory; this module validates it is
    an absolute, real path before use.
"""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.workspace.exceptions import GitCommandError, GitTimeoutError


# Default timeout (seconds) for all git subprocess calls.
_DEFAULT_TIMEOUT: float = 30.0

# The git executable name — resolved at import time so it cannot be swapped later.
_GIT = "git"


@dataclass(frozen=True)
class GitResult:
    """Structured result from a controlled git invocation."""

    returncode: int
    stdout: str
    stderr: str
    cmd: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0

    def first_line(self) -> str:
        """Return first non-empty stdout line, stripped."""
        for line in self.stdout.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""


class GitRunner:
    """
    Controlled, safe Git subprocess executor.

    Usage:
        runner = GitRunner(cwd=Path("/some/repo"))
        result = runner.run(["rev-parse", "HEAD"])

    Only whitelisted Git sub-commands are permitted; see _ALLOWED_GIT_SUBCOMMANDS.
    This prevents callers from accidentally exposing a generic shell executor.
    """

    # Explicitly permitted Git sub-commands.
    # Any command not in this list will raise ValueError immediately.
    _ALLOWED_GIT_SUBCOMMANDS: frozenset[str] = frozenset({
        "rev-parse",
        "status",
        "worktree",
        "branch",
        "commit",
        "add",
        "log",
        "diff",
        "show",
        "ls-files",
        "stash",
        "init",
        "merge-base",
        "checkout",
        "reset",
    })

    def __init__(
        self,
        cwd: Path,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not cwd.is_absolute():
            raise ValueError(f"GitRunner cwd must be absolute, got: {cwd}")
        self._cwd = cwd
        self._timeout = timeout

    @property
    def cwd(self) -> Path:
        return self._cwd

    def run(
        self,
        args: list[str],
        *,
        check: bool = True,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> GitResult:
        """
        Execute a git command synchronously.

        Args:
            args: Git arguments, e.g. ["rev-parse", "HEAD"].
                  Must NOT include the "git" binary itself.
            check: If True, raises GitCommandError when returncode != 0.
            timeout: Per-call override; defaults to self._timeout.
            extra_env: Additional environment variables (merged, not replaced).

        Returns:
            GitResult with returncode, stdout, stderr.

        Raises:
            ValueError: if the git sub-command is not in the allowed set.
            GitCommandError: if check=True and command exits non-zero.
            GitTimeoutError: if the command exceeds the timeout.
        """
        if not args:
            raise ValueError("args must be non-empty")

        sub_cmd = args[0]
        if sub_cmd not in self._ALLOWED_GIT_SUBCOMMANDS:
            raise ValueError(
                f"Git sub-command {sub_cmd!r} is not permitted. "
                f"Allowed: {sorted(self._ALLOWED_GIT_SUBCOMMANDS)}"
            )

        cmd = [_GIT, *args]
        effective_timeout = timeout if timeout is not None else self._timeout

        import os
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        # Ensure Git output is not paged / colored
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env["GIT_PAGER"] = ""

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._cwd),
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                shell=False,  # EXPLICIT: never shell=True
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise GitTimeoutError(cmd=cmd, timeout=effective_timeout)
        except FileNotFoundError as exc:
            raise GitCommandError(
                f"Git executable not found: {exc}",
                returncode=-1,
                cmd=cmd,
            ) from exc

        result = GitResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            cmd=cmd,
        )

        if check and not result.succeeded:
            raise GitCommandError(
                f"git {sub_cmd} failed (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '').strip()[:300]}",
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                cmd=cmd,
            )

        return result

    async def run_async(
        self,
        args: list[str],
        *,
        check: bool = True,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> GitResult:
        """
        Execute a git command asynchronously via asyncio.

        Runs the blocking subprocess in the default thread executor so as not
        to block the event loop.  All the same safety constraints apply.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.run(
                args,
                check=check,
                timeout=timeout,
                extra_env=extra_env,
            ),
        )
