"""
AI Operator Core — Main FastAPI Application
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.lifecycle import lifespan
from app.api.routes import health as health_routes
from app.api.routes import system as system_routes
from app.api.routes import settings as settings_routes
from app.api.routes import activity as activity_routes
from app.api.routes import ai as ai_routes


def create_app() -> FastAPI:
    """Application factory — creates and configures the FastAPI instance."""
    app_settings = get_settings()

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
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
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

    return app


app = create_app()
