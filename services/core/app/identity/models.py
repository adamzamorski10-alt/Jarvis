"""
Identity Foundation — Domain Models.

Defines the application-owned identity types:
  - ActorType: who is performing an action
  - UserIdentity: the single local human user
  - ServiceIdentity: internal named services
  - AgentIdentity: AI assistant acting on behalf of user
  - Session: server-issued authentication token
  - RequestContext: per-request authenticated context
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class ActorType(str, enum.Enum):
    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"
    SERVICE = "service"


@dataclass(frozen=True)
class UserIdentity:
    """The single local human user, derived from OS context."""
    user_id: str
    display_name: str
    os_username: str
    machine_id: str


@dataclass(frozen=True)
class ServiceIdentity:
    """An internal service that acts without a human user."""
    service_id: str
    display_name: str


@dataclass(frozen=True)
class AgentIdentity:
    """The AI assistant acting on behalf of a user."""
    agent_id: str = "ai_assistant"
    user_id: str | None = None


@dataclass
class Session:
    """Server-issued session representing an authenticated desktop app."""
    session_id: str
    user_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_expired_absolute(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at

    def is_expired_idle(self, now: datetime | None = None, idle_timeout_seconds: float = 86400.0) -> bool:
        now = now or datetime.now(timezone.utc)
        idle_seconds = (now - self.last_accessed).total_seconds()
        return idle_seconds > idle_timeout_seconds

    def is_expired(self, now: datetime | None = None, idle_timeout_seconds: float = 86400.0) -> bool:
        return self.is_expired_absolute(now) or self.is_expired_idle(now, idle_timeout_seconds)


@dataclass
class RequestContext:
    """
    Authoritative per-request context.

    All fields are set by Core. None are accepted from HTTP input.
    """
    user_id: str | None
    actor_type: ActorType
    actor_id: str
    session_id: str | None
    service_name: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None and self.session_id is not None

    def to_memory_actor(self, caller_type: Any) -> Any:
        """Build a MemoryCaller from this context."""
        from app.memory.service import CallerType, MemoryCaller
        if caller_type == CallerType.USER:
            if self.user_id is None:
                raise ValueError("User identity required for USER caller type")
            return MemoryCaller(CallerType.USER, self.user_id)
        elif caller_type == CallerType.SYSTEM:
            service_id = self.service_name or "unknown_system"
            return MemoryCaller(CallerType.SYSTEM, service_id)
        elif caller_type == CallerType.AGENT:
            if self.user_id is None:
                raise ValueError("User identity required for AGENT caller type")
            return MemoryCaller(CallerType.AGENT, self.user_id)
        else:
            raise ValueError(f"Unknown caller type: {caller_type}")


class AnonymousRequestContext:
    """Sentinel for unauthenticated requests."""
    user_id: str | None = None
    actor_type: ActorType = ActorType.SERVICE
    actor_id: str = "anonymous"
    session_id: str | None = None
    service_name: str | None = "anonymous"

    @property
    def is_authenticated(self) -> bool:
        return False


ANONYMOUS = AnonymousRequestContext()


__all__ = [
    "ActorType",
    "UserIdentity",
    "ServiceIdentity",
    "AgentIdentity",
    "Session",
    "RequestContext",
    "AnonymousRequestContext",
    "ANONYMOUS",
]
