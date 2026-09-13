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

    # 5. Check and execute optional Ollama autostart
    try:
        from app.services.settings_service import SettingsService
        from app.db.database import AsyncSessionLocal
        from app.ai.services.ollama_process_manager import process_manager

        async with AsyncSessionLocal() as session:
            svc = SettingsService(session)
            autostart_val = await svc.get_one("ai_ollama_autostart")
            ollama_url = await svc.get_one("ai_ollama_url") or "http://127.0.0.1:11434"

        if autostart_val and autostart_val.lower() == "true":
            logger.info("Ollama autostart is enabled. Checking service status...", url=ollama_url)
            if await process_manager.is_running(base_url=ollama_url):
                logger.info("Ollama service is already running. Autostart skipped.")
            else:
                logger.info("Starting Ollama background process via autostart...")
                start_res = await process_manager.start(base_url=ollama_url)
                if start_res.get("success"):
                    logger.info("Ollama autostart completed successfully", pid=start_res.get("managed_pid"))
                else:
                    logger.warning("Ollama autostart failed to complete", result=start_res)
        else:
            logger.info("Ollama autostart is disabled.")
    except Exception as exc:
        logger.warning("Error during Ollama autostart check", error=str(exc))

    # 6. Register builtin tool definitions and implementations
    try:
        from app.tools.registry import tool_registry
        from app.tools.implementation_registry import tool_implementation_registry
        from app.tools.implementations.system_time import SystemGetTime
        from app.tools.exceptions import ToolAlreadyRegisteredError

        system_time_impl = SystemGetTime()
        if not tool_registry.exists(system_time_impl.tool_id):
            tool_registry.register(system_time_impl.definition)
            tool_implementation_registry.register(system_time_impl)
            logger.info("Builtin tools registered", count=1, tool_id=system_time_impl.tool_id)
        else:
            logger.debug("Builtin tool already registered", tool_id=system_time_impl.tool_id)
    except Exception as exc:
        logger.error("Failed to register builtin tools", error=str(exc))
        raise

    logger.info("AI Operator Core is ready", host=settings.host, port=settings.port)

    # --- yield: application runs here ---
    yield

    # --- shutdown ---
    logger.info("Shutting down AI Operator Core")

    # Clean up background executions safely
    try:
        from app.services.background_execution_manager import background_execution_manager
        await background_execution_manager.shutdown(timeout_seconds=10.0)
    except Exception as exc:
        logger.warning("Error during BackgroundExecutionManager shutdown", error=str(exc))

    # Clean up managed Ollama process if one was started by AI Operator
    try:
        from app.ai.services.ollama_process_manager import process_manager
        if process_manager.managed_pid is not None:
            logger.info("Cleaning up managed Ollama process on shutdown", pid=process_manager.managed_pid)
            stop_res = await process_manager.stop()
            logger.info("Managed Ollama process shutdown complete", result=stop_res)
        else:
            logger.info("No managed Ollama process to clean up during shutdown")
    except Exception as exc:
        logger.warning("Error during Ollama process shutdown cleanup", error=str(exc))

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

