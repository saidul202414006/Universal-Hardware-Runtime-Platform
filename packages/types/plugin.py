"""
Plugin and Adapter configuration models.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PluginState(str, Enum):
    """Plugin lifecycle states."""

    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"


class RequiredTool(BaseModel):
    """External tool dependency declared by a plugin."""

    name: str = Field(description="Tool name, e.g. 'esptool'")
    min_version: str = Field(description="Minimum required version, e.g. '4.0.0'")
    max_version: str | None = Field(
        default=None, description="Maximum compatible version (optional)"
    )
    install_hint: str | None = Field(
        default=None,
        description="How to install this tool if not found",
    )

    model_config = {"frozen": True}


class PluginManifest(BaseModel):
    """
    Plugin metadata — loaded from manifest.yaml in the plugin directory.

    This is validated against a schema before any plugin code is loaded.
    Invalid manifests are rejected before any Python code runs.
    """

    id: str = Field(
        description="Reverse-domain plugin ID, e.g. 'com.espressif.esp32'"
    )
    name: str = Field(description="Human-readable plugin name")
    version: str = Field(description="Semantic version, e.g. '1.0.0'")
    author: str = Field(description="Author name or organization")
    license: str = Field(description="SPDX license identifier, e.g. 'MIT'")
    description: str = Field(default="", description="Brief plugin description")

    # Compatibility requirements
    runtime_version: str = Field(
        description="Required runtime version constraint, e.g. '>=2.0.0'"
    )
    plugin_api_version: str = Field(
        description="Plugin API version this plugin targets"
    )

    # Hardware details
    vendor: str = Field(description="Hardware vendor name, e.g. 'Espressif'")
    family: str = Field(description="Hardware family, e.g. 'ESP32'")
    supported_boards: list[str] = Field(
        description="List of board IDs this plugin supports"
    )
    supported_platforms: list[str] = Field(
        description="OS platforms: linux, windows, macos, android"
    )

    # Dependencies
    required_tools: list[RequiredTool] = Field(
        default_factory=list, description="External tools required by this plugin"
    )
    required_python_packages: list[str] = Field(
        default_factory=list,
        description="Python package requirements, e.g. ['esptool>=4.0']",
    )

    # Capabilities
    capabilities: list[str] = Field(
        description="Capabilities provided: flash, erase, read_flash, serial, reset, etc."
    )

    # Entry point
    entry_point: str = Field(
        description="Python import path to plugin class, e.g. 'plugin.main:ESP32Plugin'"
    )

    # Optional metadata
    homepage: str | None = None
    repository: str | None = None
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(
        default_factory=dict, description="Plugin-specific extra configuration"
    )

    model_config = {"frozen": True}


class AdapterConfig(BaseModel):
    """
    Adapter configuration — how an adapter connects to its underlying tool.
    """

    adapter_id: str = Field(description="Unique adapter identifier")
    name: str = Field(description="Human-readable adapter name")
    tool_name: str = Field(description="Name of the underlying CLI tool")
    tool_path: str = Field(
        default="", description="Explicit path to tool binary (empty = auto-detect)"
    )
    tool_version: str = Field(
        default="", description="Detected tool version (populated at runtime)"
    )
    enabled: bool = Field(default=True)
    timeout_seconds: int = Field(
        default=60, description="Default operation timeout in seconds"
    )
    extra: dict[str, Any] = Field(
        default_factory=dict, description="Adapter-specific extra settings"
    )

    model_config = {"frozen": False}
