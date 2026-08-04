"""
Universal Hardware Runtime Platform — Logger Package.

Provides structured logging via structlog with:
- JSON output for production (machine-parseable)
- Pretty console output for development
- Context binding (request_id, component, device_id, task_id)
- Async-safe logging
- File rotation
- Log levels: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL
"""

from packages.logger.logger import (
    UHRLogger,
    configure_logging,
    get_logger,
)

__all__ = [
    "UHRLogger",
    "configure_logging",
    "get_logger",
]
