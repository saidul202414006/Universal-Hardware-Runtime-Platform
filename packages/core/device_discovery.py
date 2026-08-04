"""
Device Discovery Engine — background USB/serial device scanning.

Responsibilities:
1. Periodic scan every N seconds (configurable)
2. Compare current ports with known ports → detect connect/disconnect
3. Look up VID/PID to determine board type
4. Assign stable UUIDs to devices (based on serial number + VID/PID)
5. Emit device.connected / device.disconnected events
6. Update RuntimeState device registry

Design:
- Runs as an asyncio background task
- Non-blocking: scan happens in a thread pool executor
- Hotplug detection: compares port sets between scans
- Stable device ID: hash(serial_number + vid + pid + port) ensures same
  device gets same UUID across reconnects
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any

from packages.core.transport.usb import USBDevice, USBEnumerator, VIDPIDDatabase
from packages.logger import get_logger
from packages.types.device import Capability, Device, DeviceState, DeviceType
from packages.types.event import Event, EventCategory, EventPriority, EventType

logger = get_logger("device_discovery")

# Map board_hint strings to DeviceType enum
_BOARD_HINT_TO_TYPE: dict[str, DeviceType] = {
    "esp32": DeviceType.ESP32,
    "esp32-s2": DeviceType.ESP32,
    "esp32-s3": DeviceType.ESP32,
    "esp32-c3": DeviceType.ESP32,
    "esp8266": DeviceType.ESP8266,
    "arduino": DeviceType.ARDUINO,
    "arduino-uno": DeviceType.ARDUINO,
    "arduino-mega": DeviceType.ARDUINO,
    "arduino-leonardo": DeviceType.ARDUINO,
    "arduino-micro": DeviceType.ARDUINO,
    "arduino-due": DeviceType.ARDUINO,
    "rp2040": DeviceType.RP2040,
    "rp2350": DeviceType.RP2350,
    "stm32": DeviceType.STM32,
    "adafruit-feather-m0": DeviceType.SAMD,
    "adafruit-trinket-m0": DeviceType.SAMD,
    "adafruit-metro-m0": DeviceType.SAMD,
    "mbed": DeviceType.STM32,
}

# Default capabilities per device type
_TYPE_CAPABILITIES: dict[DeviceType, list[Capability]] = {
    DeviceType.ESP32: [
        Capability.FLASH, Capability.ERASE, Capability.READ_FLASH,
        Capability.VERIFY_FLASH, Capability.SERIAL, Capability.RESET, Capability.OTA,
    ],
    DeviceType.ESP8266: [
        Capability.FLASH, Capability.ERASE, Capability.SERIAL, Capability.RESET,
    ],
    DeviceType.ARDUINO: [
        Capability.FLASH, Capability.SERIAL, Capability.RESET, Capability.BUILD,
    ],
    DeviceType.RP2040: [
        Capability.FLASH, Capability.SERIAL, Capability.RESET,
    ],
    DeviceType.RP2350: [
        Capability.FLASH, Capability.SERIAL, Capability.RESET,
    ],
    DeviceType.STM32: [
        Capability.FLASH, Capability.ERASE, Capability.SERIAL, Capability.RESET, Capability.DEBUG,
    ],
    DeviceType.SAMD: [
        Capability.FLASH, Capability.SERIAL, Capability.RESET,
    ],
    DeviceType.UNKNOWN: [Capability.SERIAL],
}


def _make_device_id(usb: USBDevice) -> str:
    """
    Create a stable device UUID based on physical identity.
    Same device always gets same UUID, even across reconnects.
    """
    # Use serial number + VID/PID as the stable key
    # Fall back to port path if serial number not available
    key_parts = [
        usb.serial_number or "",
        usb.vid or "",
        usb.pid or "",
        usb.manufacturer or "",
    ]
    key = ":".join(filter(None, key_parts))
    if not key:
        key = usb.port  # Last resort — port-based ID (not stable across reconnects)

    digest = hashlib.sha256(key.encode()).hexdigest()
    # Format as UUID v5-style
    return str(uuid.UUID(digest[:32]))


def _usb_to_device(usb: USBDevice) -> Device:
    """Convert a USBDevice (raw hardware info) to a Device (domain model)."""
    board_hint = usb.board_hint or ""
    device_type = _BOARD_HINT_TO_TYPE.get(board_hint, DeviceType.UNKNOWN)

    name_parts = []
    if usb.manufacturer:
        name_parts.append(usb.manufacturer)
    if usb.product:
        name_parts.append(usb.product)
    elif usb.chip_name:
        name_parts.append(usb.chip_name)
    elif board_hint:
        name_parts.append(board_hint.title())
    if not name_parts:
        name_parts.append(f"Unknown Device ({usb.port})")

    name = " ".join(name_parts)
    capabilities = _TYPE_CAPABILITIES.get(device_type, [Capability.SERIAL])

    return Device(
        id=_make_device_id(usb),
        name=name,
        device_type=device_type,
        state=DeviceState.DETECTED,
        port=usb.port,
        vid=usb.vid,
        pid=usb.pid,
        serial_number=usb.serial_number,
        manufacturer=usb.manufacturer,
        product=usb.product,
        capabilities=capabilities,
    )


class DeviceDiscovery:
    """
    Background device discovery service.

    Usage:
        discovery = DeviceDiscovery(event_bus=bus, runtime_state=state)
        await discovery.start()   # Begins periodic scanning
        # ...
        await discovery.stop()    # Cancels background task
    """

    def __init__(
        self,
        event_bus: Any,
        runtime_state: Any,
        scan_interval: float = 5.0,
        vid_pid_db_path: str = "configs/vid_pid_db.json",
    ) -> None:
        self._bus = event_bus
        self._state = runtime_state
        self._scan_interval = scan_interval
        self._enumerator = USBEnumerator(VIDPIDDatabase(vid_pid_db_path))
        self._task: asyncio.Task[None] | None = None
        self._running = False
        # Track previously seen ports to detect connect/disconnect
        self._known_ports: set[str] = set()

    async def start(self) -> None:
        """Start background discovery task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._scan_loop(), name="device_discovery"
        )
        logger.info("Device discovery started", interval=self._scan_interval)

    async def stop(self) -> None:
        """Stop background discovery task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Device discovery stopped")

    async def scan_once(self) -> list[Device]:
        """
        Perform a single synchronous scan.
        Returns list of currently connected devices.
        """
        loop = asyncio.get_event_loop()
        usb_devices = await loop.run_in_executor(None, self._enumerator.enumerate)
        return await self._process_scan(usb_devices)

    async def _scan_loop(self) -> None:
        """Periodic scan loop — runs in background."""
        # Subscribe to manual scan trigger
        self._bus.subscribe(
            "device.scan_requested",
            self._on_scan_requested,
            "device_discovery",
        )

        while self._running:
            try:
                await self.scan_once()
            except Exception as exc:
                logger.error("Device scan error", error=str(exc), exc_info=True)

            await asyncio.sleep(self._scan_interval)

    async def _on_scan_requested(self, event: Event) -> None:
        """Handle manual scan request from API or MCP."""
        logger.debug("Manual scan requested", source=event.source)
        await self.scan_once()

    async def _process_scan(self, usb_devices: list[USBDevice]) -> list[Device]:
        """
        Compare scan results with known devices.
        Emit connect/disconnect events for changes.
        Returns list of current devices.
        """
        current_ports = {d.port for d in usb_devices}
        port_to_usb = {d.port: d for d in usb_devices}

        # Detect newly connected devices
        new_ports = current_ports - self._known_ports
        for port in new_ports:
            usb = port_to_usb[port]
            device = _usb_to_device(usb)

            # Check if we already know this device by ID (reconnect)
            existing = self._state.get_device(device.id)
            if existing:
                existing.state = DeviceState.DETECTED
                existing.port = port
                existing.last_seen = device.first_seen
            else:
                self._state.add_device(device)
                existing = device

            logger.info(
                "Device connected",
                device_id=existing.id,
                name=existing.name,
                port=port,
                type=existing.device_type.value,
                board_hint=usb.board_hint,
            )

            await self._bus.publish(
                Event.create(
                    event_type=EventType.DEVICE_CONNECTED,
                    category=EventCategory.DEVICE,
                    source="device_discovery",
                    priority=EventPriority.HIGH,
                    payload={
                        "device_id": existing.id,
                        "port": port,
                        "name": existing.name,
                        "type": existing.device_type.value,
                        "vid": usb.vid,
                        "pid": usb.pid,
                    },
                )
            )

        # Detect disconnected devices
        lost_ports = self._known_ports - current_ports
        for port in lost_ports:
            # Find device by port
            disconnected = next(
                (d for d in self._state.list_devices() if d.port == port), None
            )
            if disconnected:
                disconnected.transition_to(DeviceState.OFFLINE)
                logger.info(
                    "Device disconnected",
                    device_id=disconnected.id,
                    port=port,
                    name=disconnected.name,
                )
                await self._bus.publish(
                    Event.create(
                        event_type=EventType.DEVICE_DISCONNECTED,
                        category=EventCategory.DEVICE,
                        source="device_discovery",
                        priority=EventPriority.HIGH,
                        payload={
                            "device_id": disconnected.id,
                            "port": port,
                            "name": disconnected.name,
                        },
                    )
                )

        self._known_ports = current_ports
        return self._state.list_devices()
