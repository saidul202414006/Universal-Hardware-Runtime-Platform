"""
Runtime State — central holder of all live runtime components.

This is the single source of truth for all runtime state.
It is created once at startup and injected into all API routes
via FastAPI's dependency injection system.

Architecture rule: NO global mutable state outside this class.
Everything goes through RuntimeState.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from packages.core.config import RuntimeConfig
from packages.core.database import DatabaseManager
from packages.core.event_bus import EventBus
from packages.core.plugin_loader import PluginLoader
from packages.logger import UHRLogger, get_logger
from packages.types.device import Device

logger = get_logger("runtime_state")


@dataclass
class RuntimeState:
    """
    Central runtime state container.

    Holds references to all core components.
    Passed to all API route handlers via dependency injection.
    """

    config: RuntimeConfig
    event_bus: EventBus
    database: DatabaseManager
    plugin_loader: PluginLoader
    started_at: float = field(default_factory=time.monotonic)

    # In-memory device registry (populated by device discovery)
    # Key: device_id, Value: Device
    devices: dict[str, Device] = field(default_factory=dict)

    # Active WebSocket connections for event streaming
    # Key: connection_id, Value: WebSocket send coroutine
    event_ws_connections: set[Any] = field(default_factory=set)

    # Active WebSocket connections for serial streaming per device
    # Key: device_id, Value: set of WebSocket connections
    serial_ws_connections: dict[str, set[Any]] = field(default_factory=dict)

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def version(self) -> str:
        return self.config.version

    def add_device(self, device: Device) -> None:
        self.devices[device.id] = device
        logger.info("Device registered in runtime state", device_id=device.id, port=device.port)

    def remove_device(self, device_id: str) -> Device | None:
        device = self.devices.pop(device_id, None)
        if device:
            logger.info("Device removed from runtime state", device_id=device_id)
        return device

    def get_device(self, device_id: str) -> Device | None:
        return self.devices.get(device_id)

    def list_devices(self) -> list[Device]:
        return list(self.devices.values())


# Module-level singleton — set during app startup
_runtime_state: RuntimeState | None = None


def set_runtime_state(state: RuntimeState) -> None:
    global _runtime_state
    _runtime_state = state


def get_runtime_state() -> RuntimeState:
    if _runtime_state is None:
        raise RuntimeError("Runtime state not initialized")
    return _runtime_state
