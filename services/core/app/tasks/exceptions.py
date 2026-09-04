"""
Domain Exceptions for Task State Machine & Task Lifecycle.
"""

class TaskStateError(Exception):
    """Base exception for task domain errors."""
    pass


class TaskNotFoundError(TaskStateError):
    """Raised when the specified task_id does not exist."""
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task '{task_id}' was not found.")


class InvalidStateTransitionError(TaskStateError):
    """Raised when attempting an illegal status transition."""
    def __init__(self, task_id: str, current_status: str, attempted_status: str) -> None:
        self.task_id = task_id
        self.current_status = current_status
        self.attempted_status = attempted_status
        super().__init__(
            f"Invalid status transition for task '{task_id}': "
            f"cannot transition from '{current_status}' to '{attempted_status}'."
        )


class StaleStateTransitionError(TaskStateError):
    """Raised when an atomic CAS update fails because current DB status does not match expected_status."""
    def __init__(self, task_id: str, expected_status: str, actual_status: str | None = None) -> None:
        self.task_id = task_id
        self.expected_status = expected_status
        self.actual_status = actual_status
        msg = (
            f"Stale state transition for task '{task_id}': expected status '{expected_status}', "
            f"but current status in DB is '{actual_status or 'different'}'."
        )
        super().__init__(msg)
