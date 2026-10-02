"""
Session Store — In-memory authoritative session store.

Sessions are server-issued, server-validated, and in-memory only.
Core restart invalidates all sessions.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from app.identity.models import Session

logger = logging.getLogger(__name__)

# Default timeouts (overridable via settings)
DEFAULT_ABSOLUTE_TIMEOUT_SECONDS = 86400  # 24 hours
DEFAULT_IDLE_TIMEOUT_SECONDS = 86400  # 24 hours
DEFAULT_SESSION_ID_PREFIX = "ses_"
DEFAULT_SESSION_ID_HEX_LENGTH = 64  # 32 bytes = 64 hex chars


class SessionStoreError(Exception):
    pass


class SessionStore:
    """
    Thread-safe in-memory session store.

    Sessions are indexed by session_id for O(1) lookup.
    """

    def __init__(
        self,
        absolute_timeout_seconds: float = DEFAULT_ABSOLUTE_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._absolute_timeout = absolute_timeout_seconds
        self._idle_timeout = idle_timeout_seconds

    async def create(self, user_id: str, now: datetime | None = None) -> Session:
        """Create a new session for the given user."""
        now = now or datetime.now(timezone.utc)
        session_id = self._generate_session_id()
        expires_at = now + __import__("datetime").timedelta(seconds=self._absolute_timeout)

        session = Session(
            session_id=session_id,
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
            last_accessed=now,
        )

        async with self._lock:
            self._sessions[session_id] = session

        logger.debug("Session created", session_id=session_id, user_id=user_id)
        return session

    async def get(self, session_id: str) -> Session | None:
        """Get a session by ID, or None if not found."""
        async with self._lock:
            return self._sessions.get(session_id)

    async def validate(self, session_id: str, now: datetime | None = None) -> Session | None:
        """
        Validate a session.

        Returns the session if valid, None if invalid/expired.
        If expired, the session is removed from the store.
        """
        now = now or datetime.now(timezone.utc)
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.is_expired(now, self._idle_timeout):
                del self._sessions[session_id]
                logger.debug("Session expired and removed", session_id=session_id)
                return None
            # Update last_accessed
            session.last_accessed = now
            return session

    async def revoke(self, session_id: str) -> bool:
        """Revoke a session. Returns True if the session existed."""
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.debug("Session revoked", session_id=session_id)
                return True
            return False

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke all sessions for a user. Returns count of revoked sessions."""
        count = 0
        async with self._lock:
            to_remove = [sid for sid, sess in self._sessions.items() if sess.user_id == user_id]
            for sid in to_remove:
                del self._sessions[sid]
                count += 1
        if count > 0:
            logger.debug("Revoked all sessions for user", user_id=user_id, count=count)
        return count

    async def clear(self) -> None:
        """Clear all sessions (e.g., on Core shutdown)."""
        async with self._lock:
            self._sessions.clear()
        logger.debug("All sessions cleared")

    async def count(self) -> int:
        """Return the number of active sessions."""
        async with self._lock:
            return len(self._sessions)

    def _generate_session_id(self) -> str:
        """Generate a cryptographically random session ID."""
        random_hex = secrets.token_hex(32)  # 32 bytes = 64 hex chars
        return f"{DEFAULT_SESSION_ID_PREFIX}{random_hex}"

    async def cleanup_expired(self) -> int:
        """Remove all expired sessions. Returns count of removed sessions."""
        now = datetime.now(timezone.utc)
        count = 0
        async with self._lock:
            to_remove = [
                sid for sid, sess in self._sessions.items()
                if sess.is_expired(now, self._idle_timeout)
            ]
            for sid in to_remove:
                del self._sessions[sid]
                count += 1
        if count > 0:
            logger.debug("Cleaned up expired sessions", count=count)
        return count


__all__ = ["SessionStore", "SessionStoreError", "DEFAULT_ABSOLUTE_TIMEOUT_SECONDS", "DEFAULT_IDLE_TIMEOUT_SECONDS"]
