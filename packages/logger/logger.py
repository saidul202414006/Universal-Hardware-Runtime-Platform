"""
Structured logging implementation using structlog.

Design decisions:
- structlog for structured, context-rich logging
- JSON format in production for log aggregation tools (ELK, Loki, etc.)
- Console format in development with color and alignment
- Context binding allows adding request_id, device_id, task_id to all log lines
  in a given context without passing them explicitly everywhere
- Logging is async-safe — no locks, no blocking I/O in the hot path
- File output uses a rotating handler to prevent unbounded disk growth
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import structlog
from structlog.types import EventDict, Processor

# Add TRACE level (below DEBUG) for very verbose hardware communication logging
TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")


def trace(self: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
    """Custom TRACE level logging method."""
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, message, args, **kwargs)


logging.Logger.trace = trace  # type: ignore[attr-defined]


def _add_log_level_upper(
    logger: Any, method_name: str, event_dict: EventDict  # noqa: ARG001
) -> EventDict:
    """Add uppercase log level to event dict."""
    if method_name == "warn":
        method_name = "warning"
    elif method_name == "trace":
        method_name = "trace"
    event_dict["level"] = method_name.upper()
    return event_dict


def _add_component(
    logger: Any, method_name: str, event_dict: EventDict  # noqa: ARG001
) -> EventDict:
    """Add component name from logger name if not already present."""
    if "component" not in event_dict and hasattr(logger, "name"):
        event_dict["component"] = logger.name
    return event_dict


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    output: str = "both",
    log_file: str = "logs/runtime.log",
    max_file_size_mb: int = 50,
    backup_count: int = 5,
) -> None:
    """
    Configure the global logging system.

    Must be called once at application startup before any logging occurs.

    Args:
        level: Log level name: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL
        fmt: Output format: 'json' or 'console'
        output: Where to log: 'console', 'file', or 'both'
        log_file: Path to log file (used when output is 'file' or 'both')
        max_file_size_mb: Maximum log file size before rotation
        backup_count: Number of rotated log files to keep
    """
    log_level_name = level.upper()
    log_level = TRACE_LEVEL if log_level_name == "TRACE" else getattr(
        logging, log_level_name, logging.INFO
    )

    # -------------------------------------------------------------------------
    # Shared structlog processors (run for every log event)
    # -------------------------------------------------------------------------
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,       # Merge bound context vars
        _add_component,
        _add_log_level_upper,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    # -------------------------------------------------------------------------
    # Configure structlog
    # -------------------------------------------------------------------------
    if fmt == "json":
        # Production: machine-parseable JSON
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.dict_tracebacks,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Development: human-readable colored console output
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.ExceptionRenderer(),
                structlog.dev.ConsoleRenderer(colors=True, exception_formatter=structlog.dev.plain_traceback),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )

    # -------------------------------------------------------------------------
    # Standard library logging — captures logs from libraries (uvicorn, etc.)
    # -------------------------------------------------------------------------
    handlers: list[logging.Handler] = []

    if output in ("console", "both"):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        handlers.append(console_handler)

    if output in ("file", "both"):
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_file_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        handlers.append(file_handler)

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=handlers,
        force=True,  # Override any existing configuration
    )

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchdog").setLevel(logging.WARNING)


class UHRLogger:
    """
    Component logger with context binding.

    Usage:
        logger = UHRLogger("device_discovery")
        logger.info("Device found", port="/dev/ttyUSB0", vid="0x10C4")

        # Bind context for a request scope
        with logger.bind(request_id="abc-123", device_id="dev-456"):
            logger.info("Processing")   # request_id and device_id auto-included
    """

    def __init__(self, component: str) -> None:
        self._component = component
        self._logger = structlog.get_logger(component)

    def bind(self, **kwargs: Any) -> structlog.BoundLogger:
        """Return a new logger with additional context bound."""
        return self._logger.bind(component=self._component, **kwargs)

    def trace(self, event: str, **kwargs: Any) -> None:
        self._logger.log(TRACE_LEVEL, event, component=self._component, **kwargs)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(event, component=self._component, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(event, component=self._component, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(event, component=self._component, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(event, component=self._component, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        self._logger.critical(event, component=self._component, **kwargs)

    def exception(self, event: str, exc_info: bool = True, **kwargs: Any) -> None:
        """Log an exception with traceback."""
        self._logger.exception(event, component=self._component, exc_info=exc_info, **kwargs)


def get_logger(component: str) -> UHRLogger:
    """
    Get a component logger.

    Args:
        component: Component name for log context, e.g. 'device_discovery'

    Returns:
        UHRLogger instance bound to the component name.
    """
    return UHRLogger(component)
