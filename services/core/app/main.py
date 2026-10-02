"""
AI Operator Core — Main FastAPI Application
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.lifecycle import lifespan
from app.identity.auth import AuthMiddleware
from app.identity.bootstrap import BootstrapSecret
from app.identity.session import SessionStore
from app.api.routes import health as health_routes
from app.api.routes import system as system_routes
from app.api.routes import settings as settings_routes
from app.api.routes import activity as activity_routes
from app.api.routes import ai as ai_routes
from app.api.routes import approvals as approvals_routes
from app.api.routes import plans as plans_routes
from app.api.routes import history as history_routes
from app.api.routes import scheduler as scheduler_routes

app_settings = get_settings()

# Global singletons (initialized in lifespan)
_session_store: SessionStore | None = None
_bootstrap_secret: BootstrapSecret | None = None


def get_session_store() -> SessionStore:
    if _session_store is None:
        raise RuntimeError("Session store not initialized")
    return _session_store


def get_bootstrap_secret() -> BootstrapSecret:
    if _bootstrap_secret is None:
        raise RuntimeError("Bootstrap secret not initialized")
    return _bootstrap_secret


def create_app() -> FastAPI:
    """Application factory — creates and configures the FastAPI instance."""
    global _session_store, _bootstrap_secret

    app = FastAPI(
        title="AI Operator Core",
        description="Local Core Service for AI Operator desktop application",
        version=app_settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if app_settings.debug else None,
        redoc_url="/redoc" if app_settings.debug else None,
    )

    # --- CORS ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # --- Authentication ---
    _session_store = SessionStore(
        absolute_timeout_seconds=app_settings.session_absolute_timeout_seconds,
        idle_timeout_seconds=app_settings.session_idle_timeout_seconds,
    )
    _bootstrap_secret = BootstrapSecret()
    app.state.session_store = _session_store
    app.state.bootstrap_secret = _bootstrap_secret
    app.add_middleware(
        AuthMiddleware,
    )

    # --- Global exception handler ---
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        from app.core.logging import get_logger
        logger = get_logger(__name__)
        logger.error(
            "Unhandled exception",
            path=str(request.url),
            method=request.method,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "detail": str(exc) if app_settings.debug else "An unexpected error occurred.",
            },
        )

    # --- Routers ---
    prefix = "/api"
    app.include_router(health_routes.router, prefix=prefix, tags=["Health"])
    app.include_router(system_routes.router, prefix=prefix, tags=["System"])
    app.include_router(settings_routes.router, prefix=prefix, tags=["Settings"])
    app.include_router(activity_routes.router, prefix=prefix, tags=["Activity"])
    app.include_router(ai_routes.router, prefix=prefix, tags=["AI"])
    app.include_router(approvals_routes.router, prefix=prefix, tags=["Approvals"])
    app.include_router(plans_routes.router, prefix=prefix, tags=["Plans Execution"])
    app.include_router(history_routes.router, prefix=prefix, tags=["History"])
    app.include_router(scheduler_routes.router, prefix=prefix, tags=["Scheduler"])

    from app.api.routes import auth as auth_routes
    app.include_router(auth_routes.router, prefix=prefix, tags=["Auth"])

    # Stage 20 — Tasks and Files API
    from app.api.routes import tasks as tasks_routes
    from app.api.routes import files as files_routes
    app.include_router(tasks_routes.router, prefix=prefix, tags=["Tasks"])
    app.include_router(files_routes.router, prefix=prefix, tags=["Files"])

    return app


app = create_app()
