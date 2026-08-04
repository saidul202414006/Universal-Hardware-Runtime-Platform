"""System information and health check endpoints."""

from __future__ import annotations

import platform
import sys

from fastapi import APIRouter, Depends, Request

from packages.core.runtime_state import RuntimeState, get_runtime_state
from packages.core.security import RequireAuth
from packages.types.base import BaseResponse
from packages.types.health import ComponentHealth, HealthLevel, HealthStatus

router = APIRouter(prefix="/api/v1/system", tags=["System"])


def _get_state() -> RuntimeState:
    return get_runtime_state()


@router.get("/health", response_model=BaseResponse, include_in_schema=True)
async def health_check() -> BaseResponse:
    """
    Public health check endpoint — no auth required.
    Used by load balancers and monitoring systems.
    Returns runtime health status.
    """
    state = _get_state()

    # Check database
    db_healthy = await state.database.health_check()
    db_health = ComponentHealth(
        component="database",
        level=HealthLevel.HEALTHY if db_healthy else HealthLevel.UNHEALTHY,
        message="OK" if db_healthy else "Database unreachable",
    )

    # Check event bus
    bus_stats = state.event_bus.get_stats()
    bus_healthy = bus_stats.get("running", False)
    bus_health = ComponentHealth(
        component="event_bus",
        level=HealthLevel.HEALTHY if bus_healthy else HealthLevel.UNHEALTHY,
        message=f"Running, {bus_stats.get('total_events_processed', 0)} events processed",
    )

    # Plugin system
    plugins = state.plugin_loader.list_plugins()
    plugin_health = ComponentHealth(
        component="plugin_system",
        level=HealthLevel.HEALTHY,
        message=f"{len(plugins)} plugins loaded",
        details={"plugin_count": len(plugins)},
    )

    components = [db_health, bus_health, plugin_health]
    overall = HealthStatus.compute_overall(components)

    health_status = HealthStatus(
        overall=overall,
        runtime_version=state.version,
        uptime_seconds=state.uptime_seconds,
        components=components,
    )

    return BaseResponse.ok(
        message=f"Runtime is {overall.value}",
        data=health_status.model_dump(mode="json"),
    )


@router.get("/info", response_model=BaseResponse, dependencies=[RequireAuth])
async def system_info() -> BaseResponse:
    """
    Runtime information — version, platform, configuration summary.
    Requires authentication.
    """
    state = _get_state()
    return BaseResponse.ok(
        message="System info retrieved",
        data={
            "runtime_version": state.version,
            "python_version": sys.version,
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "uptime_seconds": state.uptime_seconds,
            "device_count": len(state.devices),
            "plugin_count": len(state.plugin_loader.list_plugins()),
            "event_bus_stats": state.event_bus.get_stats(),
            "config": {
                "host": state.config.server.host,
                "port": state.config.server.port,
                "log_level": state.config.logging.level,
                "mcp_enabled": state.config.mcp.enabled,
                "env": state.config.env,
            },
        },
    )


@router.post("/diagnostics", response_model=BaseResponse, dependencies=[RequireAuth])
async def run_diagnostics() -> BaseResponse:
    """
    Run a full system diagnostics check.
    Verifies all components, tools, and permissions.
    """
    state = _get_state()
    results: dict[str, dict] = {}
    warnings: list[str] = []

    # Database
    db_ok = await state.database.health_check()
    results["database"] = {"status": "ok" if db_ok else "fail", "url": state.config.database.url.split("///")[0]}

    # Event Bus
    bus_stats = state.event_bus.get_stats()
    results["event_bus"] = {
        "status": "ok" if bus_stats["running"] else "fail",
        "events_processed": bus_stats["total_events_processed"],
        "subscriptions": bus_stats["subscription_count"],
    }

    # Plugin system
    plugins = state.plugin_loader.list_plugins()
    results["plugins"] = {
        "status": "ok",
        "count": len(plugins),
        "list": [{"id": p.manifest.id, "state": p.state.value} for p in plugins],
    }

    # Devices
    results["devices"] = {
        "status": "ok",
        "count": len(state.devices),
        "connected": [d.port for d in state.list_devices()],
    }

    # Tool availability checks
    import shutil
    tools = {
        "esptool": shutil.which("esptool") or shutil.which("esptool.py"),
        "arduino-cli": shutil.which("arduino-cli"),
        "avrdude": shutil.which("avrdude"),
        "openocd": shutil.which("openocd"),
    }
    results["tools"] = {
        name: {"found": path is not None, "path": path or "not found"}
        for name, path in tools.items()
    }

    for name, info in results["tools"].items():
        if not info["found"]:
            warnings.append(f"Tool '{name}' not found in PATH — install before using {name} features")

    all_ok = all(
        v.get("status") == "ok"
        for k, v in results.items()
        if k != "tools"
    )

    return BaseResponse.ok(
        message="Diagnostics complete",
        data=results,
        warnings=warnings,
    )
