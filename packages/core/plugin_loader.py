"""
Plugin Loader — dynamic plugin loading with full crash isolation.

Architecture rules enforced:
1. Plugins MUST have a valid manifest.yaml before any code is loaded
2. Plugins MUST implement the Plugin base class interface
3. Plugin crashes MUST NEVER crash the runtime
4. Plugins MUST NOT import runtime internals directly
5. Plugin exceptions are caught, logged, and reported — never propagated

Loading flow:
1. Scan plugins directory
2. Read and validate manifest.yaml (schema check before any code runs)
3. Check platform compatibility
4. Check runtime version compatibility
5. Import plugin module dynamically (inside try/except)
6. Instantiate Plugin class
7. Call plugin.initialize()
8. Register with Event Bus
9. Store in registry
"""

from __future__ import annotations

import importlib
import importlib.util
import platform
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from packaging.version import Version

from packages.logger import get_logger
from packages.types.plugin import PluginManifest, PluginState

logger = get_logger("plugin_loader")

# Runtime version for compatibility checks
RUNTIME_VERSION = "2.0.0"
PLUGIN_API_VERSION = "1.0.0"

# Platform name map
_PLATFORM_MAP = {
    "Linux": "linux",
    "Windows": "windows",
    "Darwin": "macos",
}


def _current_platform() -> str:
    """Get normalized platform name."""
    system = platform.system()
    # Check for Android/Termux
    if system == "Linux" and "android" in platform.version().lower():
        return "android"
    return _PLATFORM_MAP.get(system, system.lower())


class PluginBase(ABC):
    """
    Abstract base class for all plugins.

    Every plugin MUST inherit from this class and implement all abstract methods.
    The runtime calls these methods at specific lifecycle points.

    Plugins communicate with the runtime ONLY via:
    - The event bus (injected as event_bus in initialize())
    - The REST API (HTTP calls to localhost)
    - Return values from abstract methods

    Plugins MUST NOT:
    - Import runtime internal modules directly
    - Call hardware directly (use adapters via the runtime API)
    - Catch exceptions silently (always log or re-raise)
    """

    def __init__(self, manifest: PluginManifest) -> None:
        self.manifest = manifest
        self._initialized = False

    @abstractmethod
    async def initialize(self, event_bus: Any) -> None:
        """
        Called once when the plugin is loaded.
        Set up subscriptions, initialize resources, etc.

        Args:
            event_bus: The runtime EventBus instance for pub/sub.
        """
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """
        Called when the plugin is unloaded or runtime is shutting down.
        Release all resources, cancel any background tasks.
        """
        ...

    @abstractmethod
    def get_capabilities(self) -> list[str]:
        """Return list of capability strings this plugin provides."""
        ...

    @property
    def plugin_id(self) -> str:
        return self.manifest.id

    @property
    def plugin_name(self) -> str:
        return self.manifest.name

    @property
    def plugin_version(self) -> str:
        return self.manifest.version


@dataclass
class LoadedPlugin:
    """Container for a successfully loaded plugin."""

    manifest: PluginManifest
    instance: PluginBase
    state: PluginState = PluginState.LOADED
    error: str | None = None
    plugin_dir: Path = field(default_factory=Path)


class PluginLoader:
    """
    Manages the full lifecycle of plugins.

    Usage:
        loader = PluginLoader(plugins_dir="plugins", event_bus=bus)
        await loader.load_all()

        # Get a specific plugin
        plugin = loader.get_plugin("com.espressif.esp32")

        # Unload a plugin
        await loader.unload_plugin("com.espressif.esp32")
    """

    def __init__(self, plugins_dir: str, event_bus: Any) -> None:
        self._plugins_dir = Path(plugins_dir)
        self._event_bus = event_bus
        self._plugins: dict[str, LoadedPlugin] = {}
        self._current_platform = _current_platform()

    async def load_all(self, disabled: list[str] | None = None) -> dict[str, PluginState]:
        """
        Scan and load all plugins from the plugins directory.

        Returns:
            Dict mapping plugin_id to load result state.
        """
        disabled = disabled or []
        results: dict[str, PluginState] = {}

        if not self._plugins_dir.exists():
            logger.warning("Plugins directory not found", path=str(self._plugins_dir))
            return results

        # Find all plugin directories (those containing manifest.yaml)
        plugin_dirs = [
            d for d in self._plugins_dir.iterdir()
            if d.is_dir()
            and not d.name.startswith("_")
            and not d.name.startswith(".")
            and (d / "manifest.yaml").exists()
        ]

        logger.info("Scanning plugins", count=len(plugin_dirs), directory=str(self._plugins_dir))

        for plugin_dir in sorted(plugin_dirs):
            result = await self.load_plugin(plugin_dir, disabled=disabled)
            if result:
                results[result.manifest.id] = result.state

        loaded = sum(1 for s in results.values() if s == PluginState.ENABLED)
        logger.info("Plugin loading complete", total=len(results), loaded=loaded)
        return results

    async def load_plugin(
        self, plugin_dir: Path, disabled: list[str] | None = None
    ) -> LoadedPlugin | None:
        """
        Load a single plugin from a directory.

        All exceptions are caught — plugin failures never affect other plugins.

        Returns:
            LoadedPlugin if loading succeeded (even partially), None on manifest parse error.
        """
        disabled = disabled or []
        manifest_path = plugin_dir / "manifest.yaml"

        # Step 1: Parse and validate manifest (before any code runs)
        manifest = self._parse_manifest(manifest_path)
        if manifest is None:
            return None

        plugin_id = manifest.id

        # Step 2: Check if disabled
        if plugin_id in disabled:
            logger.info("Plugin disabled by config", plugin_id=plugin_id)
            loaded = LoadedPlugin(
                manifest=manifest,
                instance=_DummyPlugin(manifest),
                state=PluginState.DISABLED,
                plugin_dir=plugin_dir,
            )
            self._plugins[plugin_id] = loaded
            return loaded

        # Step 3: Check platform compatibility
        if not self._check_platform(manifest):
            logger.warning(
                "Plugin not compatible with current platform",
                plugin_id=plugin_id,
                plugin_platforms=manifest.supported_platforms,
                current_platform=self._current_platform,
            )
            return None

        # Step 4: Check runtime version compatibility
        if not self._check_runtime_version(manifest):
            logger.warning(
                "Plugin not compatible with runtime version",
                plugin_id=plugin_id,
                required=manifest.runtime_version,
                current=RUNTIME_VERSION,
            )
            return None

        # Step 5: Load Python module (isolated in try/except)
        try:
            plugin_instance = self._import_plugin(plugin_dir, manifest)
        except Exception as exc:
            logger.error(
                "Failed to import plugin module",
                plugin_id=plugin_id,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            loaded = LoadedPlugin(
                manifest=manifest,
                instance=_DummyPlugin(manifest),
                state=PluginState.ERROR,
                error=str(exc),
                plugin_dir=plugin_dir,
            )
            self._plugins[plugin_id] = loaded
            return loaded

        # Step 6: Initialize plugin (isolated in try/except)
        try:
            await plugin_instance.initialize(self._event_bus)
            plugin_instance._initialized = True
        except Exception as exc:
            logger.error(
                "Plugin initialize() raised exception",
                plugin_id=plugin_id,
                error=str(exc),
                exc_info=True,
            )
            loaded = LoadedPlugin(
                manifest=manifest,
                instance=plugin_instance,
                state=PluginState.ERROR,
                error=str(exc),
                plugin_dir=plugin_dir,
            )
            self._plugins[plugin_id] = loaded
            return loaded

        # Success
        loaded = LoadedPlugin(
            manifest=manifest,
            instance=plugin_instance,
            state=PluginState.ENABLED,
            plugin_dir=plugin_dir,
        )
        self._plugins[plugin_id] = loaded
        logger.info(
            "Plugin loaded successfully",
            plugin_id=plugin_id,
            version=manifest.version,
            capabilities=manifest.capabilities,
        )
        return loaded

    async def unload_plugin(self, plugin_id: str) -> bool:
        """
        Gracefully unload a plugin.

        Calls plugin.shutdown() with exception isolation.

        Returns:
            True if plugin was found and unloaded.
        """
        if plugin_id not in self._plugins:
            logger.warning("Plugin not found for unload", plugin_id=plugin_id)
            return False

        loaded = self._plugins[plugin_id]

        if loaded.instance._initialized:
            try:
                await loaded.instance.shutdown()
            except Exception as exc:
                logger.error(
                    "Plugin shutdown() raised exception — continuing unload",
                    plugin_id=plugin_id,
                    error=str(exc),
                    exc_info=True,
                )

        loaded.state = PluginState.UNLOADED
        del self._plugins[plugin_id]
        logger.info("Plugin unloaded", plugin_id=plugin_id)
        return True

    async def unload_all(self) -> None:
        """Unload all plugins gracefully during runtime shutdown."""
        plugin_ids = list(self._plugins.keys())
        for plugin_id in plugin_ids:
            await self.unload_plugin(plugin_id)

    def get_plugin(self, plugin_id: str) -> LoadedPlugin | None:
        """Get a loaded plugin by ID."""
        return self._plugins.get(plugin_id)

    def list_plugins(self) -> list[LoadedPlugin]:
        """List all loaded plugins."""
        return list(self._plugins.values())

    def get_enabled_plugins(self) -> list[LoadedPlugin]:
        """List only enabled plugins."""
        return [p for p in self._plugins.values() if p.state == PluginState.ENABLED]

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _parse_manifest(self, manifest_path: Path) -> PluginManifest | None:
        """Parse and validate plugin manifest.yaml. Returns None on failure."""
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if not raw:
                logger.error("Empty manifest file", path=str(manifest_path))
                return None
            return PluginManifest(**raw)
        except Exception as exc:
            logger.error(
                "Failed to parse plugin manifest",
                path=str(manifest_path),
                error=str(exc),
            )
            return None

    def _check_platform(self, manifest: PluginManifest) -> bool:
        """Check if the plugin supports the current platform."""
        return (
            self._current_platform in manifest.supported_platforms
            or "all" in manifest.supported_platforms
        )

    def _check_runtime_version(self, manifest: PluginManifest) -> bool:
        """Check if the plugin is compatible with the current runtime version."""
        try:
            from packaging.specifiers import SpecifierSet
            spec = SpecifierSet(manifest.runtime_version)
            return Version(RUNTIME_VERSION) in spec
        except Exception:
            # If packaging can't parse the version spec, allow it (be permissive)
            logger.warning(
                "Could not parse runtime_version spec, allowing plugin",
                plugin_id=manifest.id,
                spec=manifest.runtime_version,
            )
            return True

    def _import_plugin(self, plugin_dir: Path, manifest: PluginManifest) -> PluginBase:
        """
        Dynamically import the plugin module and instantiate the plugin class.
        Uses importlib.util for complete isolation to prevent module name collisions.
        """
        entry_point = manifest.entry_point
        if ":" not in entry_point:
            raise ValueError(
                f"Invalid entry_point '{entry_point}'. "
                f"Expected format: 'module_name:ClassName' or 'path.to.module:ClassName'"
            )

        module_path, class_name = entry_point.rsplit(":", 1)
        
        # Resolve file path
        if "." in module_path:
            file_path = plugin_dir / f"{module_path.replace('.', '/')}.py"
        else:
            file_path = plugin_dir / f"{module_path}.py"

        if not file_path.exists():
            raise FileNotFoundError(f"Plugin module file not found: {file_path}")

        # Unique module name to prevent caching collisions across plugins
        unique_module_name = f"uhr_plugin_{manifest.id.replace('.', '_')}"

        spec = importlib.util.spec_from_file_location(unique_module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load spec for {file_path}")

        module = importlib.util.module_from_spec(spec)
        # Register in sys.modules so inner imports work if they reference the module
        sys.modules[unique_module_name] = module
        
        # We temporarily add the plugin dir to sys.path so the plugin can import relative files
        plugin_dir_str = str(plugin_dir)
        inserted = False
        if plugin_dir_str not in sys.path:
            sys.path.insert(0, plugin_dir_str)
            inserted = True

        try:
            spec.loader.exec_module(module)
            plugin_class = getattr(module, class_name)

            if not issubclass(plugin_class, PluginBase):
                raise TypeError(
                    f"Plugin class '{class_name}' must inherit from PluginBase"
                )

            return plugin_class(manifest)  # type: ignore[no-any-return]
        finally:
            if inserted and plugin_dir_str in sys.path:
                sys.path.remove(plugin_dir_str)


class _DummyPlugin(PluginBase):
    """
    Placeholder plugin for disabled or errored plugins.
    Never actually called — just satisfies the type system.
    """

    async def initialize(self, event_bus: Any) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def get_capabilities(self) -> list[str]:
        return []
