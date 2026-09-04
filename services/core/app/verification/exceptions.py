"""
Verification Gate Foundation — Domain Exceptions.

All exceptions are typed so callers can handle specific failure modes
without parsing error messages.

SCOPE NOTE (Stage 6.5):
  These exceptions are raised by the VerificationGate, CheckRegistry, and
  CheckExecutor layers.  They must NOT propagate into ToolExecutor, permission
  engines, or approval services — verification is completely separate from
  execution authorization.
"""


class VerificationError(Exception):
    """Base exception for all verification gate errors."""


class DuplicateCheckError(VerificationError):
    """
    A check with this check_id is already registered.
    Silent overwrite is refused; duplicate registration must fail explicitly.
    """

    def __init__(self, check_id: str) -> None:
        self.check_id = check_id
        super().__init__(
            f"Verification check {check_id!r} is already registered. "
            "Duplicate check IDs are not allowed."
        )


class CheckNotFoundError(VerificationError):
    """No check with the given check_id exists in the registry."""

    def __init__(self, check_id: str) -> None:
        self.check_id = check_id
        super().__init__(f"Verification check {check_id!r} is not registered.")


class RegistryLockedError(VerificationError):
    """
    Attempted to register a check while the registry is locked for execution.
    Registration after execution has started is forbidden.
    """

    def __init__(self, check_id: str) -> None:
        self.check_id = check_id
        super().__init__(
            f"Cannot register check {check_id!r}: registry is locked for execution. "
            "All checks must be registered before gate.run() is called."
        )


class InvalidContextError(VerificationError):
    """
    The supplied VerificationContext is invalid or inconsistent.

    Examples:
      - task_id is empty
      - workspace_path is not absolute
      - expected_base_commit is malformed
      - workspace_path targets the primary worktree
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Invalid verification context: {reason}")


class PrimaryWorktreeVerificationError(VerificationError):
    """
    Refused: a verification operation would target the primary (user) worktree.

    Verification must always operate against the autonomous worktree only.
    Accidentally targeting the user's working tree is a safety violation.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Primary worktree protection violated during verification: {reason}")


class WorkspaceIdentityError(VerificationError):
    """
    The autonomous worktree failed identity validation.

    Examples:
      - The path is not a Git-registered worktree.
      - The common git dir does not match the declared repository root.
      - The expected base commit is not an ancestor of the worktree HEAD.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Workspace identity validation failed: {reason}")


class CheckExecutionError(VerificationError):
    """
    Internal error from the CheckExecutor layer.

    This is distinct from a check returning FAIL/ERROR status — it represents
    a structural problem in the executor itself (e.g. invalid argv, bad cwd).
    Check exceptions that occur during .execute() are caught and become
    CheckResult(status=ERROR), they do NOT propagate as CheckExecutionError.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Check execution infrastructure error: {reason}")
