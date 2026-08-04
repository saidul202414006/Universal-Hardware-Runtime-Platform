"""Dummy plugin for testing the plugin loader."""

from __future__ import annotations

from typing import Any

from packages.core.plugin_loader import PluginBase
from packages.types.plugin import PluginManifest


class DummyPlugin(PluginBase):
    initialized_called = False
    shutdown_called = False

    def __init__(self, manifest: PluginManifest) -> None:
        super().__init__(manifest)

    async def initialize(self, event_bus: Any) -> None:
        DummyPlugin.initialized_called = True

    async def shutdown(self) -> None:
        DummyPlugin.shutdown_called = True

    def get_capabilities(self) -> list[str]:
        return ["serial"]
