"""
Concrete DevelopmentAgent Implementation.

Thin provider-neutral coding agent that delegates structured code generation
to a CodingProvider and filesystem mutations to a WorkspaceWriter.

This agent owns NO task state, persistence, Git commits, verification,
permissions, or orchestration. It is a pure code-generation loop.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from app.ai.providers.coding_base import (
    CodingConstraints,
    CodingOperation,
    CodingOperationType,
    CodingProvider,
    CodingStepResult,
)
from app.orchestration.agent_interface import DevelopmentAgent
from app.orchestration.agents.workspace_writer import WorkspaceWriter
from app.orchestration.models import DevelopmentAgentContext, DevelopmentResult, DevelopmentResultStatus

logger = logging.getLogger(__name__)


class _UnsupportedOperationError(Exception):
    """Raised when an unknown operation type is encountered."""


class BaseDevelopmentAgent(DevelopmentAgent):
    """
    Minimal concrete DevelopmentAgent.

    Executes a coding loop:
      CodingProvider.plan_next_step() → validate → WorkspaceWriter → repeat
    """

    def __init__(
        self,
        provider: CodingProvider,
        model: str,
        constraints: CodingConstraints | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._constraints = constraints or CodingConstraints()

    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        writer = WorkspaceWriter(
            context.workspace_path,
            allowed_extensions=self._constraints.allowed_extensions,
        )
        history: List[CodingOperation] = []
        snapshot: dict[str, str] = {}

        max_ops = self._constraints.max_operations
        max_writes = self._constraints.max_total_writes
        max_bytes = self._constraints.max_content_bytes

        try:
            while len(history) < max_ops:
                if context.cancel_event is not None and context.cancel_event.is_set():
                    return DevelopmentResult(
                        status=DevelopmentResultStatus.CANCELLED,
                        summary="Cancelled during coding",
                        changed_files=writer.take_changed_files(),
                    )

                if context.abort_event is not None and context.abort_event.is_set():
                    return DevelopmentResult(
                        status=DevelopmentResultStatus.ERROR,
                        error_message="Aborted during coding",
                        changed_files=writer.take_changed_files(),
                    )

                step = await self._provider.plan_next_step(
                    model=self._model,
                    task_description=context.description,
                    workspace_snapshot=snapshot,
                    operation_history=tuple(history),
                    constraints=self._constraints,
                )

                if not step.operations:
                    return DevelopmentResult(
                        status=DevelopmentResultStatus.FAILURE,
                        error_message="Coding provider returned empty operations",
                        changed_files=writer.take_changed_files(),
                    )

                for op in step.operations:
                    if len(history) >= max_ops:
                        return DevelopmentResult(
                            status=DevelopmentResultStatus.FAILURE,
                            error_message=f"Operation limit of {max_ops} exceeded without finish",
                            changed_files=writer.take_changed_files(),
                        )

                    if op.type == CodingOperationType.FINISH:
                        return DevelopmentResult(
                            status=DevelopmentResultStatus.SUCCESS,
                            summary=step.thought,
                            changed_files=writer.take_changed_files(),
                            details={"operations_performed": len(history)},
                        )

                    if op.type == CodingOperationType.READ:
                        content = await writer.read_file(op.path, max_content_bytes=max_bytes)
                        snapshot[op.path] = content
                        history.append(op)
                        continue

                    if op.type == CodingOperationType.WRITE:
                        if len(writer._changed_files) >= max_writes:
                            return DevelopmentResult(
                                status=DevelopmentResultStatus.FAILURE,
                                error_message=f"Write limit of {max_writes} exceeded",
                                changed_files=writer.take_changed_files(),
                            )
                        await writer.write_file(op.path, op.content, max_content_bytes=max_bytes)
                        snapshot[op.path] = op.content
                        history.append(op)
                        continue

                    if op.type == CodingOperationType.EDIT:
                        if len(writer._changed_files) >= max_writes:
                            return DevelopmentResult(
                                status=DevelopmentResultStatus.FAILURE,
                                error_message=f"Write limit of {max_writes} exceeded",
                                changed_files=writer.take_changed_files(),
                            )
                        await writer.edit_file(op.path, op.old_string, op.new_string)
                        # Read updated file through WorkspaceWriter to ensure accurate in-memory snapshot
                        snapshot[op.path] = await writer.read_file(op.path, max_content_bytes=max_bytes)
                        history.append(op)
                        continue

                    raise _UnsupportedOperationError(f"Unknown operation type: {op.type}")

        except _UnsupportedOperationError as exc:
            return DevelopmentResult(
                status=DevelopmentResultStatus.FAILURE,
                error_message=str(exc),
                changed_files=writer.take_changed_files(),
            )
        except ValueError as exc:
            return DevelopmentResult(
                status=DevelopmentResultStatus.FAILURE,
                error_message=str(exc),
                changed_files=writer.take_changed_files(),
            )
        except Exception as exc:
            logger.error("DevelopmentAgent raised exception: %s", exc)
            return DevelopmentResult(
                status=DevelopmentResultStatus.ERROR,
                error_message=str(exc),
                changed_files=writer.take_changed_files(),
            )

        return DevelopmentResult(
            status=DevelopmentResultStatus.FAILURE,
            error_message=f"Operation limit of {max_ops} exceeded without finish",
            changed_files=writer.take_changed_files(),
        )

