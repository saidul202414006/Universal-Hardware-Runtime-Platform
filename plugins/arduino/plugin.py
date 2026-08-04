"""
Arduino Hardware Plugin.
"""

from __future__ import annotations

from typing import Any

from packages.core.plugin_loader import PluginBase
from packages.logger import get_logger
from packages.types.plugin import PluginManifest

logger = get_logger("plugin_arduino")


class ArduinoPlugin(PluginBase):
    def __init__(self, manifest: PluginManifest) -> None:
        super().__init__(manifest)
        self._event_bus = None

    async def initialize(self, event_bus: Any) -> None:
        self._event_bus = event_bus
        logger.info(f"Initialized {self.plugin_name} v{self.plugin_version}")

    async def shutdown(self) -> None:
        logger.info(f"Shutting down {self.plugin_name}")

    def get_capabilities(self) -> list[str]:
        return self.manifest.capabilities
