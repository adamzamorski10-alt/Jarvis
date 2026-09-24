"""
Notification Foundation — Domain Exceptions
"""
from __future__ import annotations


class NotificationError(Exception):
    """Base exception for Notification domain errors."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Notification error: {reason}")


class NotificationValidationError(NotificationError):
    """Raised when notification data fails validation."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


class NotificationNotFoundError(NotificationError):
    """Raised when a notification is not found."""

    def __init__(self, notification_id: str) -> None:
        self.notification_id = notification_id
        super().__init__(f"Notification '{notification_id}' not found")


class NotificationIntegrityConflict(NotificationError):
    """Raised when a notification with the same idempotency key already exists with different content."""

    def __init__(self, source_event_id: str, notification_type: str) -> None:
        self.source_event_id = source_event_id
        self.notification_type = notification_type
        super().__init__(
            f"Notification already exists for event '{source_event_id}' type '{notification_type}'"
        )


__all__ = [
    "NotificationError",
    "NotificationIntegrityConflict",
    "NotificationNotFoundError",
    "NotificationValidationError",
]
