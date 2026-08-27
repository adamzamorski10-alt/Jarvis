"""
AI Operator Core — Application Lifecycle (startup/shutdown)
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.database import init_db

START_TIME = datetime.now(timezone.utc)


def get_uptime_seconds() -> float:
    return (datetime.now(timezone.utc) - START_TIME).total_seconds()


def get_start_time_iso() -> str:
    return START_TIME.isoformat()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown sequences."""
    settings = get_settings()

    # 1. Configure logging first
    configure_logging(
        log_level=settings.log_level,
        log_format=settings.log_format,
    )
    logger = get_logger(__name__)

    # 2. Initialize database
    logger.info("Starting AI Operator Core", version=settings.app_version)
    try:
        await init_db()
        logger.info("Database initialized", db_path=str(settings.db_path))
    except Exception as exc:
        logger.error("Database initialization failed", error=str(exc))
        raise

    # 3. Log startup event to activity log (imported here to avoid circular)
    try:
        from app.services.activity_service import ActivityService
        from app.db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            svc = ActivityService(session)
            await svc.create_event(
                level="INFO",
                category="system",
                message="AI Operator Core started",
                details={"version": settings.app_version, "host": settings.host, "port": settings.port},
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Could not write startup activity event", error=str(exc))

    # 4. Seed default settings
    try:
        from app.services.settings_service import SettingsService
        from app.db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            svc = SettingsService(session)
            await svc.ensure_defaults()
            await session.commit()
    except Exception as exc:
        logger.warning("Could not seed default settings", error=str(exc))

    logger.info("AI Operator Core is ready", host=settings.host, port=settings.port)

    # --- yield: application runs here ---
    yield

    # --- shutdown ---
    logger.info("Shutting down AI Operator Core")

    try:
        from app.services.activity_service import ActivityService
        from app.db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            svc = ActivityService(session)
            await svc.create_event(
                level="INFO",
                category="system",
                message="AI Operator Core stopped",
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Could not write shutdown activity event", error=str(exc))
