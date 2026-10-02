"""
Authentication Middleware and Request Context dependency.

Provides:
  - AuthMiddleware: FastAPI middleware for session-based authentication
  - get_request_context: FastAPI dependency to access the authenticated context
  - require_authenticated: FastAPI dependency that raises 401 if not authenticated
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.identity.bootstrap import BootstrapSecret
from app.identity.exceptions import (
    AuthenticationError,
    InvalidSessionError,
    MissingCredentialsError,
)
from app.identity.models import (
    ANONYMOUS,
    ActorType,
    RequestContext,
    Session,
)
from app.identity.session import SessionStore

logger = logging.getLogger(__name__)

# Pattern: ses_ followed by exactly 64 hex characters
SESSION_TOKEN_PATTERN = re.compile(r"^ses_[0-9a-f]{64}$")

# Endpoints that do not require authentication
PUBLIC_ENDPOINTS = {
    ("GET", "/api/health"),
    ("GET", "/api/system/status"),
    ("GET", "/api/ai/providers"),
    ("POST", "/api/auth/session"),
}


class AuthMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for session-based authentication.

    For public endpoints: passes through without authentication.
    For authenticated endpoints: validates the bearer session token and
    attaches a RequestContext to request.state.
    """

    def __init__(
        self,
        app: Any,
    ) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Always attach a context; default to ANONYMOUS for public endpoints
        context = ANONYMOUS

        method = request.method.upper()
        path = request.url.path

        # Check if this is a public endpoint
        if (method, path) in PUBLIC_ENDPOINTS:
            request.state.context = context
            return await call_next(request)

        # Get session store from app state
        session_store = getattr(request.app.state, "session_store", None)
        if session_store is None:
            logger.warning("Session store not available", extra={"path": path})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication not configured",
            )

        # For all other endpoints, require authentication
        auth_header = request.headers.get("authorization", "")
        if not auth_header:
            logger.warning("Missing authorization header", extra={"path": path, "method": method})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Extract bearer token
        parts = auth_header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            logger.warning("Invalid authorization header format", extra={"path": path})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = parts[1].strip()
        if not SESSION_TOKEN_PATTERN.match(token):
            logger.warning("Invalid session token format", extra={"path": path})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Validate session
        session = await session_store.validate(token)
        if session is None:
            logger.warning("Invalid or expired session token", extra={"path": path})
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired session",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Build authenticated request context
        context = RequestContext(
            user_id=session.user_id,
            actor_type=ActorType.USER,
            actor_id=session.user_id,
            session_id=session.session_id,
            service_name=None,
        )

        request.state.context = context
        return await call_next(request)


def get_request_context(request: Request) -> RequestContext:
    """
    FastAPI dependency to retrieve the authenticated RequestContext.

    Returns ANONYMOUS for unauthenticated requests (should not happen for
    protected routes, but provides a safe default).
    """
    context = getattr(request.state, "context", ANONYMOUS)
    if not isinstance(context, RequestContext):
        return ANONYMOUS
    return context


def require_authenticated(request: Request) -> RequestContext:
    """
    FastAPI dependency that guarantees an authenticated context.

    Raises 401 if the request is not authenticated.
    """
    context = get_request_context(request)
    if not context.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return context


__all__ = [
    "AuthMiddleware",
    "get_request_context",
    "require_authenticated",
    "PUBLIC_ENDPOINTS",
]


def get_session_store_from_app(app: Any) -> SessionStore:
    """Get the session store from the FastAPI app state."""
    store = getattr(app.state, "session_store", None)
    if store is None:
        raise RuntimeError("Session store not available on app state")
    return store


def get_bootstrap_secret_from_app(app: Any) -> BootstrapSecret:
    """Get the bootstrap secret from the FastAPI app state."""
    secret = getattr(app.state, "bootstrap_secret", None)
    if secret is None:
        raise RuntimeError("Bootstrap secret not available on app state")
    return secret
