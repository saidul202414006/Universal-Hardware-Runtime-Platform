"""
Universal Hardware Runtime Platform — Shared Types Package.

All Pydantic models used across the entire platform are defined here.
No other package should define its own models — import from here.
"""

from packages.types.base import BaseResponse, ErrorDetail, PaginatedResponse
from packages.types.device import (
    Capability,
    Device,
    DeviceInfo,
    DeviceState,
    DeviceType,
)
from packages.types.event import Event, EventCategory, EventPriority
from packages.types.plugin import AdapterConfig, PluginManifest, PluginState
from packages.types.task import Task, TaskState, TaskType
from packages.types.health import ComponentHealth, HealthStatus, HealthLevel

__all__ = [
    # Base
    "BaseResponse",
    "ErrorDetail",
    "PaginatedResponse",
    # Device
    "Capability",
    "Device",
    "DeviceInfo",
    "DeviceState",
    "DeviceType",
    # Event
    "Event",
    "EventCategory",
    "EventPriority",
    # Plugin
    "AdapterConfig",
    "PluginManifest",
    "PluginState",
    # Task
    "Task",
    "TaskState",
    "TaskType",
    # Health
    "ComponentHealth",
    "HealthStatus",
    "HealthLevel",
]
