"""Authentication routes."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette import status

from app.core.config import get_settings
from app.identity.auth import get_bootstrap_secret_from_app, get_session_store_from_app
from app.identity.bootstrap import BootstrapSecret, BootstrapSecretNotFoundError
from app.identity.models import Session
from app.identity.session import SessionStore

router = APIRouter()


class SessionResponse(BaseModel):
    session_id: str
    expires_at: str


class LogoutResponse(BaseModel):
    message: str


@router.post("/auth/session", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(request: Request):
    """
    Bootstrap endpoint: exchange local bootstrap secret for a server-issued session.

    Protected by X-Local-Secret header.
    Never returns the bootstrap secret or internal session details.
    """
    settings = get_settings()
    bootstrap_secret = get_bootstrap_secret_from_app(request.app)
    session_store = get_session_store_from_app(request.app)

    # Extract bootstrap secret from header
    local_secret = request.headers.get("x-local-secret")
    if not local_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bootstrap credential",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate against the stored secret (constant-time comparison)
    if not bootstrap_secret.verify(local_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bootstrap credential",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Derive user identity from OS context
    from app.identity.provider import get_identity_provider
    provider = get_identity_provider()
    try:
        user_identity = provider.derive()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to derive user identity",
        ) from exc

    # Create session
    now = None  # Use server time
    session = await session_store.create(user_id=user_identity.user_id, now=now)

    return SessionResponse(
        session_id=session.session_id,
        expires_at=session.expires_at.isoformat() if session.expires_at else "",
    )


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout(request: Request):
    """
    Logout endpoint: revoke the current session.

    Requires authentication.
    """
    from app.identity.auth import get_request_context
    context = get_request_context(request)

    if not context.is_authenticated or context.session_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    session_store = get_session_store_from_app(request.app)
    revoked = await session_store.revoke(context.session_id)

    return LogoutResponse(
        message="Session revoked" if revoked else "Session already invalid"
    )
