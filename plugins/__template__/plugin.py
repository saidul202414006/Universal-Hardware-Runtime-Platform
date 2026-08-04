"""
Template Plugin — copy this file to create a new hardware plugin.

Rename the class and implement the abstract methods.
"""

from __future__ import annotations

from typing import Any

from packages.core.plugin_loader import PluginBase
from packages.types.plugin import PluginManifest


class TemplatePlugin(PluginBase):
    """
    Template plugin — minimal implementation showing required structure.
    """

    def __init__(self, manifest: PluginManifest) -> None:
        super().__init__(manifest)
        self._event_bus: Any = None

    async def initialize(self, event_bus: Any) -> None:
        """Called when the plugin is loaded. Set up subscriptions here."""
        self._event_bus = event_bus
        # Example: subscribe to device events
        # event_bus.subscribe("device.*", self._on_device_event, self.plugin_name)

    async def shutdown(self) -> None:
        """Called when the plugin is unloaded. Release all resources."""
        pass

    def get_capabilities(self) -> list[str]:
        """Return capabilities this plugin provides."""
        return []
