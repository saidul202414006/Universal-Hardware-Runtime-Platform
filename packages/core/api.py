"""
FastAPI Application — API Gateway Layer.

Responsibilities:
- Application factory with lifespan (startup/shutdown)
- CORS middleware
- Request ID injection middleware
- Global exception handlers returning BaseResponse format
- Route registration
- Static file serving for dashboard (in production)

Architecture rule: This module is ONLY the HTTP gateway.
Business logic lives in routers and core modules.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from packages.core.config import RuntimeConfig, load_config
from packages.core.database import initialize_database
from packages.core.event_bus import initialize_event_bus
from packages.core.plugin_loader import PluginLoader
from packages.core.routers import devices, operations, plugins, serial, system, tasks
from packages.core.routers.websockets import router as ws_router
from packages.core.runtime_state import RuntimeState, set_runtime_state
from packages.logger import configure_logging, get_logger
from packages.types.base import BaseResponse, ErrorDetail

logger = get_logger("api")


# =============================================================================
# Application Factory
# =============================================================================


def create_app(config: RuntimeConfig | None = None) -> FastAPI:
    """
    FastAPI application factory.

    Creates and configures the full application with:
    - Lifespan management (startup/shutdown)
    - Middleware (CORS, request ID)
    - Exception handlers
    - All routers

    Args:
        config: Optional config override (useful for testing).

    Returns:
        Configured FastAPI application instance.
    """
    cfg = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """
        Application lifespan — runs startup and shutdown logic.

        Startup order:
        1. Configure logging
        2. Initialize Event Bus
        3. Initialize Database
        4. Initialize Plugin Loader
        5. Set RuntimeState singleton
        6. Load plugins

        Shutdown order (reverse):
        1. Unload all plugins
        2. Stop Event Bus
        3. Close Database
        """
        # ── STARTUP ────────────────────────────────────────────────────────
        configure_logging(
            level=cfg.logging.level,
            fmt=cfg.logging.format,
            output=cfg.logging.output,
            log_file=cfg.logging.file_path,
            max_file_size_mb=cfg.logging.max_file_size_mb,
            backup_count=cfg.logging.backup_count,
        )

        logger.info(
            "Universal Hardware Runtime starting",
            version=cfg.version,
            env=cfg.env,
            host=cfg.server.host,
            port=cfg.server.port,
        )

        # Event Bus
        bus = initialize_event_bus(queue_size=1000)
        await bus.start()
        logger.info("Event Bus started")

        # Database
        db = initialize_database(cfg.database.url, echo=cfg.database.echo)
        await db.initialize()
        logger.info("Database initialized")

        # Plugin Loader
        plugin_loader = PluginLoader(
            plugins_dir=cfg.plugins.directory,
            event_bus=bus,
        )

        # Runtime State (central dependency — must be set before routers are used)
        state = RuntimeState(
            config=cfg,
            event_bus=bus,
            database=db,
            plugin_loader=plugin_loader,
        )
        set_runtime_state(state)

        # Load plugins
        await plugin_loader.load_all(disabled=cfg.plugins.disabled)
        logger.info(
            "Plugins loaded",
            count=len(plugin_loader.list_plugins()),
        )

        # Print API key to console if in development mode
        if cfg.is_development:
            logger.info(
                "API Key (development mode — set UHR_API_KEY env var for production)",
                api_key=cfg.security.api_key,
            )

        logger.info(
            "Universal Hardware Runtime READY",
            version=cfg.version,
            dashboard_url=f"http://{cfg.server.host}:{cfg.server.port}",
            api_docs_url=f"http://{cfg.server.host}:{cfg.server.port}/docs",
        )

        yield  # Application is running

        # ── SHUTDOWN ───────────────────────────────────────────────────────
        logger.info("Universal Hardware Runtime shutting down")

        await plugin_loader.unload_all()
        logger.info("Plugins unloaded")

        await bus.stop()
        logger.info("Event Bus stopped")

        await db.close()
        logger.info("Database closed")

        logger.info("Shutdown complete")

    # ── App creation ─────────────────────────────────────────────────────────
    app = FastAPI(
        title="Universal Hardware Runtime Platform",
        description=(
            "Control any hardware board (ESP32, Arduino, STM32, Raspberry Pi) "
            "through a single unified API. Accessible via MCP for AI Agents."
        ),
        version=cfg.version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def inject_request_id(request: Request, call_next: Any) -> Any:
        """Inject a unique request ID into every request for tracing."""
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        start_time = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start_time) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        return response

    # ── Exception Handlers ────────────────────────────────────────────────────

    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """
        Handle all HTTPExceptions uniformly.
        Router-raised exceptions have structured dict details.
        Framework 404/405s have plain string details.
        """
        if isinstance(exc.detail, dict):
            # Our routers provide structured detail dicts
            return JSONResponse(
                status_code=exc.status_code,
                content={"success": False, "detail": exc.detail, "message": exc.detail.get("message", "")},
            )
        # Generic framework error (e.g., route not found, method not allowed)
        return JSONResponse(
            status_code=exc.status_code,
            content=BaseResponse.fail(
                message=str(exc.detail),
                errors=[ErrorDetail(
                    error_code=f"HTTP_{exc.status_code}",
                    message=str(exc.detail),
                    root_cause="HTTP error",
                    suggested_fix="Check the API documentation at /docs",
                )],
            ).model_dump(mode="json"),
        )

    @app.exception_handler(500)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled internal server error",
            path=request.url.path,
            method=request.method,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=BaseResponse.from_exception(exc, "INTERNAL_SERVER_ERROR").model_dump(mode="json"),
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(system.router)
    app.include_router(devices.router)
    app.include_router(tasks.router)
    app.include_router(operations.router)
    app.include_router(plugins.router)
    app.include_router(serial.router)
    app.include_router(ws_router)

    # Root endpoint
    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": "Universal Hardware Runtime Platform",
            "version": cfg.version,
            "docs": "/docs",
            "health": "/api/v1/system/health",
        }

    return app


# ── Entry point for uvicorn ───────────────────────────────────────────────────

def start() -> None:
    """Start the runtime server (CLI entry point)."""
    import uvicorn

    cfg = load_config()
    app = create_app(cfg)

    uvicorn.run(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        workers=cfg.server.workers,
        reload=cfg.server.reload and cfg.is_development,
        log_level=cfg.logging.level.lower(),
    )


if __name__ == "__main__":
    start()
