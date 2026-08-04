"""
Event models — the backbone of all internal communication.

Every component communicates via events on the Event Bus.
No direct cross-module imports allowed per architecture rules.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventCategory(str, Enum):
    """High-level event categories for routing and filtering."""

    SYSTEM = "system"      # Runtime lifecycle events
    DEVICE = "device"      # Device connect/disconnect/state changes
    BUILD = "build"        # Firmware compilation events
    FLASH = "flash"        # Flashing operation events
    SERIAL = "serial"      # Serial communication events
    PLUGIN = "plugin"      # Plugin load/unload/error events
    TASK = "task"          # Task scheduler events
    SECURITY = "security"  # Auth, rate limit events
    ADAPTER = "adapter"    # Adapter events
    API = "api"            # API request/response events


class EventPriority(int, Enum):
    """
    Event processing priority.
    Higher number = higher priority.
    CRITICAL events are never dropped, even under load.
    """

    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


# Well-known event type constants — use these instead of magic strings
class EventType:
    """Namespace for well-known event type strings."""

    # System
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"
    SYSTEM_HEALTH_CHECK = "system.health_check"

    # Device
    DEVICE_CONNECTED = "device.connected"
    DEVICE_DISCONNECTED = "device.disconnected"
    DEVICE_IDENTIFIED = "device.identified"
    DEVICE_STATE_CHANGED = "device.state_changed"
    DEVICE_LOCKED = "device.locked"
    DEVICE_UNLOCKED = "device.unlocked"
    DEVICE_ERROR = "device.error"

    # Build
    BUILD_STARTED = "build.started"
    BUILD_PROGRESS = "build.progress"
    BUILD_COMPLETED = "build.completed"
    BUILD_FAILED = "build.failed"
    BUILD_CANCELLED = "build.cancelled"

    # Flash
    FLASH_STARTED = "flash.started"
    FLASH_PROGRESS = "flash.progress"
    FLASH_COMPLETED = "flash.completed"
    FLASH_FAILED = "flash.failed"
    FLASH_CANCELLED = "flash.cancelled"

    # Serial
    SERIAL_OPENED = "serial.opened"
    SERIAL_CLOSED = "serial.closed"
    SERIAL_DATA = "serial.data"
    SERIAL_ERROR = "serial.error"

    # Plugin
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_UNLOADED = "plugin.unloaded"
    PLUGIN_ERROR = "plugin.error"
    PLUGIN_ENABLED = "plugin.enabled"
    PLUGIN_DISABLED = "plugin.disabled"

    # Task
    TASK_CREATED = "task.created"
    TASK_QUEUED = "task.queued"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    TASK_RETRY = "task.retry"

    # Adapter
    ADAPTER_REGISTERED = "adapter.registered"
    ADAPTER_HEALTH_OK = "adapter.health_ok"
    ADAPTER_HEALTH_FAIL = "adapter.health_fail"


class Event(BaseModel):
    """
    Event Bus message format.

    All internal communication passes through events.
    Correlation ID enables tracing a chain of events from a single API request.
    """

    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique event identifier",
    )
    event_type: str = Field(
        description="Dot-notation event type, e.g. 'device.connected'"
    )
    category: EventCategory = Field(description="High-level category for routing")
    priority: EventPriority = Field(
        default=EventPriority.NORMAL, description="Processing priority"
    )
    timestamp: datetime = Field(
        default_factory=_utcnow, description="UTC timestamp when event was created"
    )
    source: str = Field(
        description="Component that emitted this event, e.g. 'device_discovery'"
    )
    target: str | None = Field(
        default=None,
        description="Specific target component (None = broadcast to all subscribers)",
    )
    correlation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Links related events from a single operation/request",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Event-specific data"
    )
    persistent: bool = Field(
        default=False,
        description="If True, event is stored in DB for audit/replay. "
        "Always True for CRITICAL priority.",
    )

    model_config = {"frozen": True}

    def model_post_init(self, __context: Any) -> None:
        """CRITICAL events are always persistent."""
        if self.priority == EventPriority.CRITICAL:
            # Bypass frozen to set persistent=True for critical events
            object.__setattr__(self, "persistent", True)

    @classmethod
    def create(
        cls,
        event_type: str,
        category: EventCategory,
        source: str,
        payload: dict[str, Any] | None = None,
        priority: EventPriority = EventPriority.NORMAL,
        target: str | None = None,
        correlation_id: str | None = None,
    ) -> "Event":
        """Convenience factory for creating events."""
        return cls(
            event_type=event_type,
            category=category,
            source=source,
            payload=payload or {},
            priority=priority,
            target=target,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
