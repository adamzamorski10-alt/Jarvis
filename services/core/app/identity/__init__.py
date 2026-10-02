"""
AI Operator Core — Identity & Session Foundation.

Provides:
  - User identity derived from OS context
  - Server-issued session tokens with in-memory validation
  - Authentication middleware for FastAPI
  - Request context for downstream services
"""
from app.identity.auth import AuthMiddleware, get_request_context, require_authenticated
from app.identity.bootstrap import BootstrapSecret, BootstrapSecretError, BootstrapSecretNotFoundError
from app.identity.models import (
    ANONYMOUS,
    ActorType,
    AgentIdentity,
    AnonymousRequestContext,
    RequestContext,
    ServiceIdentity,
    Session,
    UserIdentity,
)
from app.identity.provider import IdentityProvider, UnixIdentityProvider, WindowsIdentityProvider, get_identity_provider
from app.identity.session import SessionStore

__all__ = [
    "ActorType",
    "AgentIdentity",
    "ANONYMOUS",
    "AnonymousRequestContext",
    "AuthMiddleware",
    "BootstrapSecret",
    "BootstrapSecretError",
    "BootstrapSecretNotFoundError",
    "IdentityProvider",
    "RequestContext",
    "ServiceIdentity",
    "Session",
    "SessionStore",
    "UserIdentity",
    "UnixIdentityProvider",
    "WindowsIdentityProvider",
    "get_identity_provider",
    "get_request_context",
    "require_authenticated",
]
