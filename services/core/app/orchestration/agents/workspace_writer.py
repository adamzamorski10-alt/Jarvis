"""
Workspace Writer — Safe Filesystem Authority for DevelopmentAgent.

WorkspaceWriter is the SOLE component responsible for filesystem mutations
inside an isolated workspace path. It enforces strict containment and
rejects any operation that could escape the workspace boundary.

This module performs NO subprocess execution, Git operations, permission
checks, or orchestration. It is a pure filesystem abstraction.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Tuple


class WorkspaceWriter:
    """
    Safe filesystem writer scoped to a single workspace path.

    All paths are relative to the workspace root. Absolute paths and
    traversal sequences are rejected. Symlink escapes are detected by
    resolving the final path and verifying containment.
    """

    def __init__(
        self,
        workspace_path: str,
        allowed_extensions: Tuple[str, ...] | None = None,
    ) -> None:
        self._workspace_path = Path(workspace_path).resolve()
        self._changed_files: list[str] = []
        self._allowed_extensions: tuple[str, ...] | None = (
            tuple(ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in allowed_extensions)
            if allowed_extensions is not None
            else None
        )

    async def read_file(self, relative_path: str, max_content_bytes: int = 1_048_576) -> str:
        """
        Read a file inside the workspace.

        Args:
            relative_path: Path relative to workspace root.
            max_content_bytes: Maximum allowed content size.

        Returns:
            File content as string.

        Raises:
            ValueError: If path is unsafe or file cannot be read.
        """
        target = self._validate_path(relative_path)
        if not target.is_file():
            raise ValueError(f"File not found: {relative_path}")
        if target.stat().st_size > max_content_bytes:
            raise ValueError(
                f"File size {target.stat().st_size} exceeds max_content_bytes limit of {max_content_bytes}"
            )
        try:
            return target.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Failed to read {relative_path}: {exc}") from exc

    async def write_file(self, relative_path: str, content: str, max_content_bytes: int = 1_048_576) -> None:
        """
        Create or overwrite a file inside the workspace using atomic replace.

        Args:
            relative_path: Path relative to workspace root.
            content: File content to write.
            max_content_bytes: Maximum allowed content size.

        Raises:
            ValueError: If path is unsafe, content is too large, or write fails.
        """
        if len(content) > max_content_bytes:
            raise ValueError(
                f"Content exceeds max_content_bytes limit of {max_content_bytes}"
            )
        if len(content.encode("utf-8")) > max_content_bytes:
            raise ValueError(
                f"Content exceeds max_content_bytes limit of {max_content_bytes}"
            )

        target = self._validate_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        dir_name = target.parent
        fd, temp_path = tempfile.mkstemp(dir=str(dir_name), prefix=".ai_op_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(temp_path, target)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

        normalized = Path(relative_path).as_posix()
        self._changed_files.append(normalized)

    async def edit_file(self, relative_path: str, old_string: str, new_string: str) -> None:
        """
        Edit a file by replacing an exact string match.

        Args:
            relative_path: Path relative to workspace root.
            old_string: Exact string to replace.
            new_string: Replacement string.

        Raises:
            ValueError: If path is unsafe, old_string not found, or write fails.
        """
        if not old_string:
            raise ValueError("old_string must not be empty")

        target = self._validate_path(relative_path)
        if not target.is_file():
            raise ValueError(f"File not found: {relative_path}")

        try:
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Failed to read {relative_path}: {exc}") from exc

        if old_string not in content:
            raise ValueError(f"old_string not found in {relative_path}")

        updated = content.replace(old_string, new_string, 1)
        await self.write_file(relative_path, updated)

    def take_changed_files(self) -> Tuple[str, ...]:
        """
        Return and reset the list of successfully changed files.

        Paths are deduplicated while preserving insertion order.

        Returns:
            Tuple of relative paths that were written or edited.
        """
        changed = tuple(dict.fromkeys(self._changed_files))
        self._changed_files.clear()
        return changed

    def _validate_path(self, relative_path: str) -> Path:
        """
        Resolve and verify a relative path is inside the workspace.

        Args:
            relative_path: Path relative to workspace root.

        Returns:
            Resolved absolute Path.

        Raises:
            ValueError: If path is unsafe or escapes the workspace.
        """
        if (
            Path(relative_path).is_absolute()
            or Path(relative_path).drive != ""
            or re.match(r"^[A-Za-z]:", relative_path)
        ):
            raise ValueError(f"Absolute paths or drive specifiers not allowed: {relative_path}")

        if any(part == ".." for part in Path(relative_path).parts):
            raise ValueError(f"Path traversal not allowed: {relative_path}")

        if self._allowed_extensions is not None:
            ext = Path(relative_path).suffix.lower()
            if ext not in self._allowed_extensions:
                raise ValueError(
                    f"File extension '{ext}' is not allowed by constraints: {relative_path}"
                )

        raw_target = self._workspace_path / relative_path

        # Never follow symlinks supplied by an untrusted coding agent, even
        # when the symlink target itself remains inside the workspace.
        current = self._workspace_path
        for part in Path(relative_path).parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError(f"Symlink path component not allowed: {relative_path}")

        target = raw_target.resolve()

        if not str(target).startswith(str(self._workspace_path) + os.sep) and target != self._workspace_path:
            raise ValueError(
                f"Path escapes workspace: {relative_path} resolves to {target}"
            )

        return target
