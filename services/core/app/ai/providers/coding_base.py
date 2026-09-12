"""
Coding Provider — Provider-Neutral Code Generation Contracts.

This module defines the strict input/output contracts for autonomous coding agents.
Model output is treated as untrusted input. All filesystem operations are delegated
to WorkspaceWriter. No provider implementation may perform filesystem mutations,
subprocess execution, Git operations, or permission checks.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Bounded constants ─────────────────────────────────────────────────────────

_MAX_PATH_LENGTH = 4096
_MAX_CONTENT_BYTES = 1_048_576  # 1 MiB
_MAX_EDIT_STRING_BYTES = 65_536  # 64 KiB
_MAX_OPERATIONS = 50
_MAX_TOTAL_WRITES = 100
_MAX_THOUGHT_LENGTH = 4096


# ── Enums ─────────────────────────────────────────────────────────────────────


class CodingOperationType(str, Enum):
    """Allowed structured coding operations."""
    READ = "read"
    WRITE = "write"
    EDIT = "edit"
    FINISH = "finish"


# ── Operation model ───────────────────────────────────────────────────────────


class CodingOperation(BaseModel):
    """
    Single structured coding operation produced by a CodingProvider.

    Provider output is untrusted. Pydantic validation enforces schema
    constraints. WorkspaceWriter enforces filesystem containment.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: CodingOperationType
    path: str = Field(default="", min_length=0, max_length=_MAX_PATH_LENGTH)
    content: str | None = Field(default=None, max_length=_MAX_CONTENT_BYTES)
    old_string: str | None = Field(default=None, max_length=_MAX_EDIT_STRING_BYTES)
    new_string: str | None = Field(default=None, max_length=_MAX_EDIT_STRING_BYTES)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        if v == "":
            return v
        if Path(v).is_absolute() or Path(v).drive != "":
            raise ValueError("Absolute paths or drive specifiers not allowed")
        if any(part == ".." for part in Path(v).parts):
            raise ValueError("Path traversal not allowed")
        return v

    @model_validator(mode="after")
    def _validate_operation_fields(self) -> "CodingOperation":
        if self.type == CodingOperationType.READ:
            if not self.path:
                raise ValueError("read requires a non-empty path")
            if self.content is not None or self.old_string is not None or self.new_string is not None:
                raise ValueError("read does not accept content, old_string, or new_string")

        elif self.type == CodingOperationType.WRITE:
            if not self.path:
                raise ValueError("write requires a non-empty path")
            if self.content is None:
                raise ValueError("write requires content")
            if self.old_string is not None or self.new_string is not None:
                raise ValueError("write does not accept old_string or new_string")

        elif self.type == CodingOperationType.EDIT:
            if not self.path:
                raise ValueError("edit requires a non-empty path")
            if self.old_string is None or self.new_string is None:
                raise ValueError("edit requires old_string and new_string")
            if self.content is not None:
                raise ValueError("edit does not accept content")

        elif self.type == CodingOperationType.FINISH:
            if self.path or self.content is not None or self.old_string is not None or self.new_string is not None:
                raise ValueError("finish does not accept path, content, old_string, or new_string")

        return self


# ── Step result ───────────────────────────────────────────────────────────────


class CodingStepResult(BaseModel):
    """
    One turn of a coding provider response.

    The provider must return at least one operation per turn.
    finished=True signals completion of the coding task.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    operations: Tuple[CodingOperation, ...] = Field(default_factory=tuple)
    thought: str = Field(default="", max_length=_MAX_THOUGHT_LENGTH)
    finished: bool = False

    @model_validator(mode="after")
    def _validate_finished_reconciliation(self) -> "CodingStepResult":
        has_finish_op = any(op.type == CodingOperationType.FINISH for op in self.operations)
        if self.finished and not has_finish_op:
            raise ValueError("finished=True requires a FINISH operation")
        if not self.finished and has_finish_op:
            raise ValueError("FINISH operation requires finished=True")
        return self


# ── Constraints ───────────────────────────────────────────────────────────────


class CodingConstraints(BaseModel):
    """
    Application-controlled limits for coding agent loops.
    """
    model_config = ConfigDict(frozen=True)

    max_operations: int = Field(default=_MAX_OPERATIONS, ge=1, le=200)
    max_content_bytes: int = Field(default=_MAX_CONTENT_BYTES, ge=1024, le=10_485_760)
    allowed_extensions: Tuple[str, ...] | None = Field(default=None)
    max_total_writes: int = Field(default=_MAX_TOTAL_WRITES, ge=1, le=1000)


# ── Provider ABC ──────────────────────────────────────────────────────────────


class CodingProvider(ABC):
    """
    Abstract interface for provider-neutral code generation.

    Implementations must NOT:
      - perform filesystem operations
      - execute shell/subprocess commands
      - carry ToolExecutionService, PermissionEngine, secrets, or credentials
      - make task state transitions
      - perform authoritative verification

    Implementations return structured CodingOperation objects only.
    """

    name: str

    @abstractmethod
    async def plan_next_step(
        self,
        model: str,
        task_description: str,
        workspace_snapshot: dict[str, str],
        operation_history: Tuple[CodingOperation, ...],
        constraints: CodingConstraints | None = None,
    ) -> CodingStepResult:
        """
        Plan the next structured coding operations.

        Args:
            model: Provider/model identifier.
            task_description: Human-readable task description.
            workspace_snapshot: Current workspace file contents keyed by relative path.
            operation_history: Previous operations performed this run.
            constraints: Optional application-controlled limits.

        Returns:
            CodingStepResult containing at least one CodingOperation.
            Set finished=True when the task is complete.
        """
        ...
