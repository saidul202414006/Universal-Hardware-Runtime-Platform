"""
Adapter Manager — registry and selection of hardware adapters.
"""

from __future__ import annotations

from packages.core.adapter_base import BaseAdapter
from packages.core.config import AdaptersConfig
from packages.logger import get_logger
from packages.types.device import Device, DeviceType
from packages.types.plugin import AdapterConfig

logger = get_logger("adapter_manager")


class AdapterManager:
    """Manages the lifecycle and routing of adapters."""

    def __init__(self, config: AdaptersConfig) -> None:
        self._config = config
        self._adapters: dict[str, BaseAdapter] = {}
        self._device_type_map: dict[DeviceType, str] = {}

    async def initialize(self) -> None:
        """Load and verify all configured adapters."""

        # Load esptool
        try:
            from adapters.esptool import ESPToolAdapter

            cfg = AdapterConfig(
                adapter_id="esptool",
                name="Espressif esptool",
                tool_name="esptool.py",
                tool_path=self._config.esptool_path,
            )
            adapter = ESPToolAdapter(cfg)
            if await adapter.initialize():
                self._adapters["esptool"] = adapter
                self._device_type_map[DeviceType.ESP32] = "esptool"
                self._device_type_map[DeviceType.ESP8266] = "esptool"
        except Exception as exc:
            logger.error("Failed to load esptool adapter", error=str(exc))

        # Load arduino-cli
        try:
            from adapters.arduino_cli import ArduinoCLIAdapter

            cfg = AdapterConfig(
                adapter_id="arduino-cli",
                name="Arduino CLI",
                tool_name="arduino-cli",
                tool_path=self._config.arduino_cli_path,
            )
            adapter = ArduinoCLIAdapter(cfg)
            if await adapter.initialize():
                self._adapters["arduino-cli"] = adapter
                self._device_type_map[DeviceType.ARDUINO] = "arduino-cli"
                self._device_type_map[DeviceType.SAMD] = "arduino-cli"
        except Exception as exc:
            logger.error("Failed to load arduino-cli adapter", error=str(exc))

    def get_adapter(self, adapter_id: str) -> BaseAdapter | None:
        return self._adapters.get(adapter_id)

    def get_adapter_for_device(self, device: Device) -> BaseAdapter | None:
        """Find the most appropriate adapter for a given device."""
        if device.adapter_id and device.adapter_id in self._adapters:
            return self._adapters[device.adapter_id]

        adapter_id = self._device_type_map.get(device.device_type)
        if adapter_id:
            return self._adapters.get(adapter_id)

        return None
