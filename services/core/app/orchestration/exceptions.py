"""
Autonomous Task Orchestrator — Domain Exception Hierarchy.
"""


class OrchestrationError(Exception):
    """Base exception for all orchestration errors."""
    pass


class TaskNotStartableError(OrchestrationError):
    """Raised when a task cannot be started (e.g. already completed, running, or cancelled)."""
    def __init__(self, task_id: str, status: str, reason: str | None = None) -> None:
        self.task_id = task_id
        self.status = status
        self.reason = reason
        msg = f"Task '{task_id}' in status '{status}' cannot be started"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class MaxAttemptsExceededError(OrchestrationError):
    """Raised when task attempt_count reaches or exceeds max_attempts."""
    def __init__(self, task_id: str, attempt_count: int, max_attempts: int) -> None:
        self.task_id = task_id
        self.attempt_count = attempt_count
        self.max_attempts = max_attempts
        super().__init__(
            f"Task '{task_id}' has reached maximum attempt limit "
            f"({attempt_count}/{max_attempts})"
        )


class WorkspaceOrchestrationError(OrchestrationError):
    """Raised when autonomous workspace creation or validation fails."""
    def __init__(self, task_id: str, reason: str) -> None:
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"Workspace setup failed for task '{task_id}': {reason}")


class VerificationOrchestrationError(OrchestrationError):
    """Raised when verification gate setup or execution fails critically."""
    def __init__(self, task_id: str, reason: str) -> None:
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"Verification gate execution failed for task '{task_id}': {reason}")


class CheckpointOrchestrationError(OrchestrationError):
    """Raised when Git checkpoint creation fails after passing verification."""
    def __init__(self, task_id: str, reason: str) -> None:
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"Git checkpoint creation failed for task '{task_id}': {reason}")


class StaleTaskStateError(OrchestrationError):
    """Raised when a state transition fails due to concurrent modification."""
    def __init__(self, task_id: str, expected_status: str, actual_status: str) -> None:
        self.task_id = task_id
        self.expected_status = expected_status
        self.actual_status = actual_status
        super().__init__(
            f"Concurrent task modification detected for '{task_id}': "
            f"expected '{expected_status}', got '{actual_status}'"
        )


class DevelopmentManagerError(OrchestrationError):
    """Raised when DevelopmentManager prompt compilation fails."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Development manager error: {reason}")
