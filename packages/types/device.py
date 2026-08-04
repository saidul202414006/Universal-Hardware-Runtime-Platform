"""
Device models — hardware device representation and state machine.

A Device goes through well-defined states:
DETECTED → IDENTIFYING → READY → BUSY → OFFLINE

The state machine ensures only valid transitions are permitted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DeviceType(str, Enum):
    """Hardware device families."""

    ESP32 = "esp32"
    ESP8266 = "esp8266"
    ARDUINO = "arduino"
    RP2040 = "rp2040"
    RP2350 = "rp2350"
    STM32 = "stm32"
    NRF = "nrf"
    SAMD = "samd"
    AVR = "avr"
    RASPBERRY_PI = "raspberry_pi"
    UNKNOWN = "unknown"


class DeviceState(str, Enum):
    """
    Device state machine states.

    Valid transitions:
    DETECTED → IDENTIFYING → READY
    READY → BUSY (operation started)
    BUSY → READY (operation complete)
    ANY → OFFLINE (disconnected)
    OFFLINE → DETECTED (reconnected)
    ANY → ERROR (unrecoverable)
    """

    DETECTED = "detected"        # USB plugged in, not yet identified
    IDENTIFYING = "identifying"  # Querying chip/board info
    READY = "ready"              # Identified and ready for operations
    BUSY = "busy"                # Flash/build/erase in progress
    OFFLINE = "offline"          # Disconnected
    ERROR = "error"              # Unrecoverable error state


# Valid state transitions — enforced at runtime
VALID_TRANSITIONS: dict[DeviceState, set[DeviceState]] = {
    DeviceState.DETECTED: {DeviceState.IDENTIFYING, DeviceState.OFFLINE},
    DeviceState.IDENTIFYING: {DeviceState.READY, DeviceState.ERROR, DeviceState.OFFLINE},
    DeviceState.READY: {DeviceState.BUSY, DeviceState.OFFLINE, DeviceState.ERROR},
    DeviceState.BUSY: {DeviceState.READY, DeviceState.ERROR, DeviceState.OFFLINE},
    DeviceState.OFFLINE: {DeviceState.DETECTED},
    DeviceState.ERROR: {DeviceState.OFFLINE, DeviceState.DETECTED},
}


class Capability(str, Enum):
    """Hardware capabilities that adapters/plugins can declare."""

    FLASH = "flash"              # Can write firmware
    ERASE = "erase"              # Can erase flash memory
    READ_FLASH = "read_flash"    # Can read flash content
    VERIFY_FLASH = "verify_flash"  # Can verify after flashing
    SERIAL = "serial"            # Serial communication
    RESET = "reset"              # Hardware reset
    OTA = "ota"                  # Over-the-air update
    BUILD = "build"              # Firmware compilation
    DEBUG = "debug"              # JTAG/SWD debugging
    GPIO = "gpio"                # GPIO control
    I2C = "i2c"                  # I2C communication
    SPI = "spi"                  # SPI communication


class DeviceInfo(BaseModel):
    """
    Detailed device information returned after identification.
    All fields are optional since not every board exposes every detail.
    """

    chip_type: str | None = Field(
        default=None, description="Chip identifier, e.g. 'ESP32-S3', 'ATmega328P'"
    )
    mac_address: str | None = Field(default=None, description="MAC address if available")
    flash_size_bytes: int | None = Field(
        default=None, description="Flash memory size in bytes"
    )
    ram_size_bytes: int | None = Field(
        default=None, description="RAM size in bytes"
    )
    firmware_version: str | None = Field(
        default=None, description="Currently installed firmware version"
    )
    bootloader_version: str | None = Field(
        default=None, description="Bootloader version if readable"
    )
    crystal_frequency_mhz: float | None = Field(
        default=None, description="Crystal frequency in MHz"
    )
    fqbn: str | None = Field(
        default=None,
        description="Arduino Fully Qualified Board Name, e.g. 'arduino:avr:uno'",
    )
    features: list[str] = Field(
        default_factory=list,
        description="Board-specific feature flags (e.g. 'wifi', 'bluetooth')",
    )
    raw_info: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw identification output from the adapter tool",
    )

    model_config = {"frozen": False}


class Device(BaseModel):
    """
    Hardware device representation.

    Each device gets a stable UUID based on its serial number + VID/PID
    so it keeps the same ID across reconnects.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Stable UUID for this device",
    )
    name: str = Field(description="Human-readable device name")
    device_type: DeviceType = Field(description="Hardware family")
    state: DeviceState = Field(
        default=DeviceState.DETECTED, description="Current device state"
    )

    # Connection details
    port: str = Field(description="Serial port path, e.g. '/dev/ttyUSB0', 'COM3'")
    vid: str | None = Field(
        default=None, description="USB Vendor ID as hex string, e.g. '0x10C4'"
    )
    pid: str | None = Field(
        default=None, description="USB Product ID as hex string, e.g. '0xEA60'"
    )
    serial_number: str | None = Field(
        default=None, description="USB serial number string"
    )
    manufacturer: str | None = Field(default=None, description="USB manufacturer string")
    product: str | None = Field(default=None, description="USB product string")

    # Adapter association
    adapter_id: str | None = Field(
        default=None, description="ID of the adapter managing this device"
    )
    plugin_id: str | None = Field(
        default=None, description="ID of the plugin providing support for this device"
    )

    # Capabilities
    capabilities: list[Capability] = Field(
        default_factory=list, description="Operations this device supports"
    )

    # Device information (populated after identification)
    info: DeviceInfo = Field(default_factory=DeviceInfo)

    # Locking (prevent concurrent operations)
    locked_by_task: str | None = Field(
        default=None, description="Task ID currently holding the device lock"
    )

    # Timestamps
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)
    last_state_change: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": False}

    def transition_to(self, new_state: DeviceState) -> None:
        """
        Apply a state transition. Raises ValueError for invalid transitions.
        This enforces the state machine contract at runtime.
        """
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid device state transition: {self.state} → {new_state}. "
                f"Allowed from {self.state}: {[s.value for s in allowed]}"
            )
        self.state = new_state
        self.last_state_change = _utcnow()

    @property
    def is_available(self) -> bool:
        """Device is ready and not locked."""
        return self.state == DeviceState.READY and self.locked_by_task is None

    @property
    def is_locked(self) -> bool:
        """Device is locked by an operation."""
        return self.locked_by_task is not None

    def lock(self, task_id: str) -> None:
        """Lock the device for a specific task. Raises if already locked."""
        if self.locked_by_task is not None:
            raise RuntimeError(
                f"Device {self.id} is already locked by task {self.locked_by_task}. "
                f"Cannot lock for task {task_id}."
            )
        self.locked_by_task = task_id

    def unlock(self, task_id: str) -> None:
        """Release the device lock. Only the locking task can unlock."""
        if self.locked_by_task != task_id:
            raise RuntimeError(
                f"Device {self.id} is locked by task {self.locked_by_task}, "
                f"cannot unlock with task {task_id}."
            )
        self.locked_by_task = None
