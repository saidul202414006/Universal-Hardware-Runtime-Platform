"""Plugin management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from packages.core.runtime_state import RuntimeState, get_runtime_state
from packages.core.security import RequireAuth
from packages.types.base import BaseResponse
from packages.types.plugin import PluginState

router = APIRouter(prefix="/api/v1/plugins", tags=["Plugins"])


def _get_state() -> RuntimeState:
    return get_runtime_state()


@router.get("", response_model=BaseResponse, dependencies=[RequireAuth])
async def list_plugins() -> BaseResponse:
    """List all loaded plugins and their states."""
    state = _get_state()
    plugins = state.plugin_loader.list_plugins()
    return BaseResponse.ok(
        message=f"Found {len(plugins)} plugin(s)",
        data={
            "plugins": [
                {
                    "id": p.manifest.id,
                    "name": p.manifest.name,
                    "version": p.manifest.version,
                    "author": p.manifest.author,
                    "vendor": p.manifest.vendor,
                    "family": p.manifest.family,
                    "state": p.state.value,
                    "capabilities": p.manifest.capabilities,
                    "supported_boards": p.manifest.supported_boards,
                    "error": p.error,
                }
                for p in plugins
            ],
            "total": len(plugins),
            "enabled": sum(1 for p in plugins if p.state == PluginState.ENABLED),
        },
    )


@router.get("/{plugin_id}", response_model=BaseResponse, dependencies=[RequireAuth])
async def get_plugin(plugin_id: str) -> BaseResponse:
    """Get details for a specific plugin."""
    state = _get_state()
    plugin = state.plugin_loader.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "PLUGIN_NOT_FOUND",
                "message": f"Plugin '{plugin_id}' not found",
                "root_cause": "Plugin ID not in registry",
                "suggested_fix": "Check plugin ID or reload plugins",
            },
        )
    return BaseResponse.ok(
        message="Plugin found",
        data={
            "id": plugin.manifest.id,
            "name": plugin.manifest.name,
            "version": plugin.manifest.version,
            "author": plugin.manifest.author,
            "license": plugin.manifest.license,
            "description": plugin.manifest.description,
            "vendor": plugin.manifest.vendor,
            "family": plugin.manifest.family,
            "state": plugin.state.value,
            "capabilities": plugin.manifest.capabilities,
            "supported_boards": plugin.manifest.supported_boards,
            "supported_platforms": plugin.manifest.supported_platforms,
            "required_tools": [t.model_dump() for t in plugin.manifest.required_tools],
            "entry_point": plugin.manifest.entry_point,
            "error": plugin.error,
            "plugin_dir": str(plugin.plugin_dir),
        },
    )


@router.post("/{plugin_id}/enable", response_model=BaseResponse, dependencies=[RequireAuth])
async def enable_plugin(plugin_id: str) -> BaseResponse:
    """Enable a disabled plugin."""
    state = _get_state()
    plugin = state.plugin_loader.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "PLUGIN_NOT_FOUND",
                "message": f"Plugin '{plugin_id}' not found",
                "root_cause": "Plugin ID not in registry",
                "suggested_fix": "Check plugin ID",
            },
        )
    if plugin.state == PluginState.ENABLED:
        return BaseResponse.ok(
            message=f"Plugin '{plugin_id}' is already enabled",
            data={"id": plugin_id, "state": plugin.state.value},
        )
    plugin.state = PluginState.ENABLED
    return BaseResponse.ok(
        message=f"Plugin '{plugin_id}' enabled",
        data={"id": plugin_id, "state": plugin.state.value},
    )


@router.post("/{plugin_id}/disable", response_model=BaseResponse, dependencies=[RequireAuth])
async def disable_plugin(plugin_id: str) -> BaseResponse:
    """Disable an enabled plugin without unloading it."""
    state = _get_state()
    plugin = state.plugin_loader.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "PLUGIN_NOT_FOUND",
                "message": f"Plugin '{plugin_id}' not found",
                "root_cause": "Plugin ID not in registry",
                "suggested_fix": "Check plugin ID",
            },
        )
    plugin.state = PluginState.DISABLED
    return BaseResponse.ok(
        message=f"Plugin '{plugin_id}' disabled",
        data={"id": plugin_id, "state": plugin.state.value},
    )
