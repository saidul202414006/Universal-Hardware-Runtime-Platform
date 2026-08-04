"""
MCP Server — AI Agent interface layer.

Exposes 14 tools that allow AI Agents (Claude, etc.) to:
- Discover and identify hardware
- Build and flash firmware
- Monitor serial output
- Manage tasks and plugins

MCP Transport: stdio (default) for Claude Desktop integration.
Also supports SSE and streamable-http for web-based agents.

Architecture rule: MCP tools are THIN wrappers — they delegate
all logic to the Runtime Core via the RuntimeState/EventBus.
No business logic lives in this file.

Tool safety rules (enforced):
- flash_firmware and erase_flash have dangerous=True annotations
- They require explicit confirmation in their input schema
- All async operations return task_id immediately
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC
from typing import Any

from mcp.server.fastmcp import FastMCP

from packages.core.config import RUNTIME_VERSION, RuntimeConfig, load_config
from packages.core.database import initialize_database
from packages.core.event_bus import initialize_event_bus
from packages.core.plugin_loader import PluginLoader
from packages.core.runtime_state import RuntimeState, set_runtime_state
from packages.logger import configure_logging, get_logger
from packages.types.event import Event, EventCategory

logger = get_logger("mcp_server")

# Create FastMCP server instance
mcp = FastMCP(
    name="universal-hardware-runtime",
    instructions=(
        "You are connected to the Universal Hardware Runtime Platform. "
        "This server allows you to discover, identify, and control hardware boards "
        "(ESP32, Arduino, STM32, Raspberry Pi, etc.) through a unified API.\n\n"
        "SAFETY RULES:\n"
        "- flash_firmware and erase_flash are DESTRUCTIVE operations. "
        "Always ask the user for explicit confirmation before calling them.\n"
        "- Verify the correct device_id before any hardware operation.\n"
        "- Use task_status to monitor long-running operations.\n"
        "- Use run_diagnostics if hardware is not responding."
    ),
)

# Runtime state — initialized when MCP server starts
_state: RuntimeState | None = None


def _get_state() -> RuntimeState:
    if _state is None:
        raise RuntimeError("MCP server not initialized")
    return _state


# =============================================================================
# Tool 1: system_info
# =============================================================================


@mcp.tool()
async def system_info() -> dict[str, Any]:
    """
    Get Universal Hardware Runtime Platform status and version information.

    Returns runtime version, uptime, connected device count, and loaded plugins.
    Call this first to verify the runtime is operational.
    """
    import platform
    import sys

    state = _get_state()
    return {
        "runtime_version": RUNTIME_VERSION,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "uptime_seconds": round(state.uptime_seconds, 2),
        "device_count": len(state.devices),
        "plugin_count": len(state.plugin_loader.list_plugins()),
        "status": "operational",
    }


# =============================================================================
# Tool 2: scan_devices
# =============================================================================


@mcp.tool()
async def scan_devices() -> dict[str, Any]:
    """
    Scan for connected hardware boards and return the list.

    Triggers an immediate USB/serial scan and returns currently known devices.
    For real-time updates, subscribe to device events via the WebSocket API.

    Returns a list of detected devices with their port, type, state, and capabilities.
    """
    state = _get_state()

    await state.event_bus.publish(
        Event.create(
            event_type="device.scan_requested",
            category=EventCategory.DEVICE,
            source="mcp",
            payload={"trigger": "mcp_tool"},
        )
    )

    await asyncio.sleep(0.1)  # Brief wait for any immediate results

    devices = state.list_devices()
    return {
        "device_count": len(devices),
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "type": d.device_type.value,
                "port": d.port,
                "state": d.state.value,
                "capabilities": [c.value for c in d.capabilities],
                "vid": d.vid,
                "pid": d.pid,
                "manufacturer": d.manufacturer,
            }
            for d in devices
        ],
    }


# =============================================================================
# Tool 3: identify_device
# =============================================================================


@mcp.tool()
async def identify_device(device_id: str) -> dict[str, Any]:
    """
    Get detailed information about a specific connected device.

    Args:
        device_id: The device ID returned by scan_devices.

    Returns chip type, MAC address, flash size, firmware version, and capabilities.
    """
    state = _get_state()
    device = state.get_device(device_id)

    if not device:
        return {
            "error": True,
            "error_code": "DEVICE_NOT_FOUND",
            "message": f"Device '{device_id}' not found. Run scan_devices first.",
        }

    return {
        "id": device.id,
        "name": device.name,
        "type": device.device_type.value,
        "port": device.port,
        "state": device.state.value,
        "is_available": device.is_available,
        "capabilities": [c.value for c in device.capabilities],
        "vid": device.vid,
        "pid": device.pid,
        "serial_number": device.serial_number,
        "manufacturer": device.manufacturer,
        "product": device.product,
        "info": {
            "chip_type": device.info.chip_type,
            "mac_address": device.info.mac_address,
            "flash_size_bytes": device.info.flash_size_bytes,
            "ram_size_bytes": device.info.ram_size_bytes,
            "firmware_version": device.info.firmware_version,
            "fqbn": device.info.fqbn,
            "features": device.info.features,
        },
    }


# =============================================================================
# Tool 4: list_plugins
# =============================================================================


@mcp.tool()
async def list_plugins() -> dict[str, Any]:
    """
    List all loaded hardware plugins and their capabilities.

    Returns plugin names, versions, supported boards, and current state.
    """
    state = _get_state()
    plugins = state.plugin_loader.list_plugins()
    return {
        "plugin_count": len(plugins),
        "plugins": [
            {
                "id": p.manifest.id,
                "name": p.manifest.name,
                "version": p.manifest.version,
                "vendor": p.manifest.vendor,
                "family": p.manifest.family,
                "state": p.state.value,
                "capabilities": p.manifest.capabilities,
                "supported_boards": p.manifest.supported_boards,
            }
            for p in plugins
        ],
    }


# =============================================================================
# Tool 5: build_project
# =============================================================================


@mcp.tool()
async def build_project(
    project_path: str,
    target_board: str,
    device_id: str | None = None,
    clean_build: bool = False,
) -> dict[str, Any]:
    """
    Compile firmware for a hardware board.

    This is non-destructive — it only compiles code, does not touch hardware.
    Returns a task_id to monitor progress via task_status.

    Args:
        project_path: Path to the firmware project directory (Arduino sketch, ESP-IDF, etc.)
        target_board: Board identifier (e.g., 'esp32', 'arduino:avr:uno', 'rp2040')
        device_id: Optional target device ID (for board auto-detection)
        clean_build: If True, cleans build artifacts before compiling

    Returns task_id for monitoring with task_status.
    """
    import uuid
    from datetime import datetime

    from packages.core.routers.tasks import register_task

    state = _get_state()

    task_id = str(uuid.uuid4())
    task_data = {
        "id": task_id,
        "task_type": "build",
        "state": "queued",
        "device_id": device_id,
        "params": {
            "project_path": project_path,
            "target_board": target_board,
            "clean_build": clean_build,
        },
        "progress": 0.0,
        "progress_message": "Queued for build",
        "log_lines": [],
        "created_at": datetime.now(UTC).isoformat(),
        "correlation_id": str(uuid.uuid4()),
        "created_by": "mcp",
    }
    register_task(task_data)

    await state.event_bus.publish(
        Event.create(
            event_type="build.started",
            category=EventCategory.BUILD,
            source="mcp",
            payload={
                "task_id": task_id,
                "project": project_path,
                "board": target_board,
            },
        )
    )

    return {
        "task_id": task_id,
        "state": "queued",
        "message": "Build task queued. Use task_status to monitor progress.",
        "monitor": f"task_status('{task_id}')",
    }


# =============================================================================
# Tool 6: flash_firmware  [DANGEROUS]
# =============================================================================


@mcp.tool()
async def flash_firmware(
    device_id: str,
    firmware_path: str,
    confirmed: bool,
    verify_after_flash: bool = True,
    flash_address: str = "0x0",
) -> dict[str, Any]:
    """
    Flash firmware to a hardware device. DANGEROUS — overwrites existing firmware.

    ⚠️  WARNING: This PERMANENTLY overwrites the device's existing firmware.
    Always ask the user for explicit confirmation before calling this tool.

    Args:
        device_id: Target device ID (get from scan_devices or identify_device)
        firmware_path: Absolute path to compiled firmware file (.bin, .hex, or .uf2)
        confirmed: Must be True — explicit confirmation that firmware will be overwritten
        verify_after_flash: If True, reads back and verifies the flashed data (recommended)
        flash_address: Flash start address in hex (default '0x0', ESP32 uses '0x0')

    Returns task_id for monitoring with task_status.
    """
    import uuid
    from datetime import datetime
    from pathlib import Path

    from packages.core.routers.tasks import register_task

    if not confirmed:
        return {
            "error": True,
            "error_code": "CONFIRMATION_REQUIRED",
            "message": (
                "Flash operation requires confirmed=True. "
                "This will PERMANENTLY overwrite the device firmware. "
                "Ask the user to confirm before proceeding."
            ),
        }

    state = _get_state()
    device = state.get_device(device_id)

    if not device:
        return {
            "error": True,
            "error_code": "DEVICE_NOT_FOUND",
            "message": f"Device '{device_id}' not found. Run scan_devices first.",
        }

    if not device.is_available:
        return {
            "error": True,
            "error_code": "DEVICE_NOT_AVAILABLE",
            "message": f"Device is not available (state={device.state.value}). Wait for it to become ready.",
        }

    if not Path(firmware_path).exists():
        return {
            "error": True,
            "error_code": "FIRMWARE_NOT_FOUND",
            "message": f"Firmware file not found: {firmware_path}. Build the firmware first.",
        }

    task_id = str(uuid.uuid4())
    task_data = {
        "id": task_id,
        "task_type": "flash",
        "state": "queued",
        "device_id": device_id,
        "params": {
            "firmware_path": firmware_path,
            "verify_after_flash": verify_after_flash,
            "flash_address": flash_address,
        },
        "progress": 0.0,
        "progress_message": "Queued for flash",
        "log_lines": [],
        "created_at": datetime.now(UTC).isoformat(),
        "correlation_id": str(uuid.uuid4()),
        "created_by": "mcp",
    }
    register_task(task_data)

    from packages.types.device import DeviceState

    device.lock(task_id)
    device.transition_to(DeviceState.BUSY)

    await state.event_bus.publish(
        Event.create(
            event_type="flash.started",
            category=EventCategory.FLASH,
            source="mcp",
            payload={
                "task_id": task_id,
                "device_id": device_id,
                "firmware": firmware_path,
            },
        )
    )

    return {
        "task_id": task_id,
        "state": "queued",
        "device_id": device_id,
        "message": "Flash task queued. Device is now locked. Use task_status to monitor.",
        "monitor": f"task_status('{task_id}')",
        "warning": "Device is locked. Do not disconnect during flash operation.",
    }


# =============================================================================
# Tool 7: erase_flash  [DANGEROUS]
# =============================================================================


@mcp.tool()
async def erase_flash(device_id: str, confirmed: bool) -> dict[str, Any]:
    """
    Erase ALL flash memory on a device. EXTREMELY DANGEROUS — data is unrecoverable.

    ⚠️  WARNING: This PERMANENTLY and IRREVERSIBLY deletes all flash contents.
    The device will be unusable until new firmware is flashed.
    Always ask the user for explicit, unambiguous confirmation before calling.

    Args:
        device_id: Target device ID
        confirmed: Must be True — explicit confirmation that ALL flash will be erased

    Returns task_id for monitoring with task_status.
    """
    import uuid
    from datetime import datetime

    from packages.core.routers.tasks import register_task

    if not confirmed:
        return {
            "error": True,
            "error_code": "CONFIRMATION_REQUIRED",
            "message": (
                "Erase requires confirmed=True. "
                "This will PERMANENTLY erase ALL flash contents. "
                "The device will be unusable until reflashed. "
                "Ask the user to explicitly confirm this destructive action."
            ),
        }

    state = _get_state()
    device = state.get_device(device_id)

    if not device:
        return {
            "error": True,
            "error_code": "DEVICE_NOT_FOUND",
            "message": f"Device '{device_id}' not found.",
        }

    if not device.is_available:
        return {
            "error": True,
            "error_code": "DEVICE_NOT_AVAILABLE",
            "message": f"Device is not available (state={device.state.value}).",
        }

    task_id = str(uuid.uuid4())
    task_data = {
        "id": task_id,
        "task_type": "erase",
        "state": "queued",
        "device_id": device_id,
        "params": {"confirmed": True},
        "progress": 0.0,
        "progress_message": "Queued for erase",
        "log_lines": [],
        "created_at": datetime.now(UTC).isoformat(),
        "correlation_id": str(uuid.uuid4()),
        "created_by": "mcp",
    }
    register_task(task_data)

    from packages.types.device import DeviceState

    device.lock(task_id)
    device.transition_to(DeviceState.BUSY)

    await state.event_bus.publish(
        Event.create(
            event_type="flash.erase_started",
            category=EventCategory.FLASH,
            source="mcp",
            payload={"task_id": task_id, "device_id": device_id},
        )
    )

    return {
        "task_id": task_id,
        "state": "queued",
        "device_id": device_id,
        "message": "Erase task queued. ALL flash contents will be permanently deleted.",
        "monitor": f"task_status('{task_id}')",
        "warning": "DESTRUCTIVE: This cannot be undone.",
    }


# =============================================================================
# Tool 8: open_serial
# =============================================================================


@mcp.tool()
async def open_serial(device_id: str, baud_rate: int = 115200) -> dict[str, Any]:
    """
    Open a serial monitor connection to a device.

    After opening, serial data is streamed via the WebSocket API at:
    ws://host:port/ws/v1/serial/{device_id}

    Args:
        device_id: Target device ID
        baud_rate: Serial communication speed (default 115200)

    Returns connection info and WebSocket URL for real-time data.
    """
    from packages.core.routers.serial import _open_ports

    state = _get_state()
    device = state.get_device(device_id)

    if not device:
        return {
            "error": True,
            "error_code": "DEVICE_NOT_FOUND",
            "message": f"Device '{device_id}' not found.",
        }

    _open_ports[device_id] = baud_rate

    await state.event_bus.publish(
        Event.create(
            event_type="serial.opened",
            category=EventCategory.SERIAL,
            source="mcp",
            payload={"device_id": device_id, "port": device.port, "baud_rate": baud_rate},
        )
    )

    return {
        "device_id": device_id,
        "port": device.port,
        "baud_rate": baud_rate,
        "status": "opened",
        "websocket_url": f"/ws/v1/serial/{device_id}",
        "message": "Serial port opened. Connect WebSocket for real-time data.",
    }


# =============================================================================
# Tool 9: close_serial
# =============================================================================


@mcp.tool()
async def close_serial(device_id: str) -> dict[str, Any]:
    """
    Close an open serial connection to a device.

    Args:
        device_id: Target device ID
    """
    from packages.core.routers.serial import _open_ports

    state = _get_state()

    if device_id in _open_ports:
        del _open_ports[device_id]
        await state.event_bus.publish(
            Event.create(
                event_type="serial.closed",
                category=EventCategory.SERIAL,
                source="mcp",
                payload={"device_id": device_id},
            )
        )
        return {"device_id": device_id, "status": "closed"}

    return {
        "device_id": device_id,
        "status": "was_not_open",
        "message": "Serial port was not open for this device.",
    }


# =============================================================================
# Tool 10: serial_write
# =============================================================================


@mcp.tool()
async def serial_write(device_id: str, data: str, add_newline: bool = True) -> dict[str, Any]:
    """
    Send data over serial to a connected device.

    The serial port must be opened first with open_serial.

    Args:
        device_id: Target device ID
        data: String data to send (e.g., AT commands, REPL input)
        add_newline: If True, appends \\n to the data (default True)
    """
    from packages.core.routers.serial import _open_ports

    state = _get_state()

    if device_id not in _open_ports:
        return {
            "error": True,
            "error_code": "SERIAL_NOT_OPEN",
            "message": f"Serial not open for device '{device_id}'. Call open_serial first.",
        }

    payload = data + ("\n" if add_newline else "")
    await state.event_bus.publish(
        Event.create(
            event_type="serial.write_requested",
            category=EventCategory.SERIAL,
            source="mcp",
            payload={"device_id": device_id, "data": payload},
        )
    )

    return {
        "device_id": device_id,
        "bytes_queued": len(payload.encode()),
        "status": "queued",
    }


# =============================================================================
# Tool 11: task_status
# =============================================================================


@mcp.tool()
async def task_status(task_id: str) -> dict[str, Any]:
    """
    Check the status and progress of a running or completed task.

    Use this to monitor flash, build, and erase operations that return task_id.

    Args:
        task_id: Task ID returned by flash_firmware, build_project, or erase_flash

    Returns current state, progress (0-100%), log output, and result/error.
    """
    from packages.core.routers.tasks import _tasks

    task = _tasks.get(task_id)
    if not task:
        return {
            "error": True,
            "error_code": "TASK_NOT_FOUND",
            "message": f"Task '{task_id}' not found. Check the task ID.",
        }

    return {
        "task_id": task_id,
        "type": task.get("task_type"),
        "state": task.get("state"),
        "progress": task.get("progress", 0),
        "message": task.get("progress_message", ""),
        "device_id": task.get("device_id"),
        "created_at": task.get("created_at"),
        "log_lines": task.get("log_lines", [])[-20:],  # Last 20 lines
        "result": task.get("result"),
        "error_message": task.get("error_message"),
    }


# =============================================================================
# Tool 12: cancel_task
# =============================================================================


@mcp.tool()
async def cancel_task(task_id: str) -> dict[str, Any]:
    """
    Cancel a running or queued task.

    Cannot cancel tasks that are already completed, failed, or cancelled.

    Args:
        task_id: Task ID to cancel
    """
    from packages.core.routers.tasks import _tasks

    state = _get_state()
    task = _tasks.get(task_id)

    if not task:
        return {
            "error": True,
            "error_code": "TASK_NOT_FOUND",
            "message": f"Task '{task_id}' not found.",
        }

    terminal = {"completed", "failed", "cancelled"}
    if task.get("state") in terminal:
        return {
            "error": True,
            "error_code": "TASK_ALREADY_TERMINAL",
            "message": f"Cannot cancel task in state '{task.get('state')}'.",
        }

    task["state"] = "cancelled"
    await state.event_bus.publish(
        Event.create(
            event_type="task.cancelled",
            category=EventCategory.TASK,
            source="mcp",
            payload={"task_id": task_id},
        )
    )

    return {"task_id": task_id, "state": "cancelled", "message": "Task cancellation requested."}


# =============================================================================
# Tool 13: get_logs
# =============================================================================


@mcp.tool()
async def get_logs(
    level: str = "INFO",
    limit: int = 100,
    component: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve recent system log entries.

    Args:
        level: Minimum log level to include: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL
        limit: Maximum number of log entries to return (default 100, max 500)
        component: Filter by component name (e.g., 'device_discovery', 'event_bus')

    Returns recent log entries from the runtime log file.
    """
    from pathlib import Path

    log_file = Path("logs/runtime.log")
    if not log_file.exists():
        return {
            "message": "Log file not found. Logs may be going to console only.",
            "log_file": str(log_file),
            "entries": [],
        }

    valid_levels = {"TRACE": 0, "DEBUG": 1, "INFO": 2, "WARNING": 3, "ERROR": 4, "CRITICAL": 5}
    min_level_num = valid_levels.get(level.upper(), 2)

    entries = []
    try:
        with log_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entry_level = entry.get("level", "INFO").upper()
                    entry_level_num = valid_levels.get(entry_level, 2)
                    if entry_level_num < min_level_num:
                        continue
                    if component and entry.get("component") != component:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    entries.append({"raw": line})
    except Exception as exc:
        return {"error": True, "message": f"Failed to read logs: {exc}"}

    # Return last N entries
    entries = entries[-min(limit, 500) :]
    return {
        "entry_count": len(entries),
        "level_filter": level.upper(),
        "component_filter": component,
        "entries": entries,
    }


# =============================================================================
# Tool 14: run_diagnostics
# =============================================================================


@mcp.tool()
async def run_diagnostics() -> dict[str, Any]:
    """
    Run a comprehensive system health check.

    Checks:
    - Runtime health (event bus, database)
    - Connected devices
    - Loaded plugins
    - Tool availability (esptool, arduino-cli, etc.)
    - File system permissions

    Use this when hardware is not responding or to verify the runtime is healthy.
    """
    state = _get_state()
    results: dict[str, Any] = {}
    issues: list[str] = []
    warnings: list[str] = []

    # Runtime
    bus_stats = state.event_bus.get_stats()
    results["event_bus"] = {
        "running": bus_stats["running"],
        "events_processed": bus_stats["total_events_processed"],
        "subscriptions": bus_stats["subscription_count"],
        "errors": bus_stats["total_errors"],
    }
    if not bus_stats["running"]:
        issues.append("Event Bus is not running")

    # Database
    db_healthy = await state.database.health_check()
    results["database"] = {"healthy": db_healthy, "url": state.config.database.url.split("///")[0]}
    if not db_healthy:
        issues.append("Database is not accessible")

    # Devices
    devices = state.list_devices()
    results["devices"] = {
        "connected": len(devices),
        "list": [
            {"id": d.id, "name": d.name, "port": d.port, "state": d.state.value} for d in devices
        ],
    }

    # Plugins
    plugins = state.plugin_loader.list_plugins()
    results["plugins"] = {
        "count": len(plugins),
        "enabled": sum(1 for p in plugins if p.state.value == "enabled"),
        "errors": [p.manifest.id for p in plugins if p.state.value == "error"],
    }

    # Tool availability
    tools_to_check = {
        "esptool": ["esptool", "esptool.py"],
        "arduino-cli": ["arduino-cli"],
        "avrdude": ["avrdude"],
        "openocd": ["openocd"],
        "picotool": ["picotool"],
    }
    tool_results = {}
    for tool_name, candidates in tools_to_check.items():
        found = None
        for candidate in candidates:
            found = shutil.which(candidate)
            if found:
                break
        tool_results[tool_name] = {"found": found is not None, "path": found or "not in PATH"}
        if not found:
            warnings.append(f"Tool '{tool_name}' not found — install before using related features")

    results["tools"] = tool_results

    # Serial ports
    try:
        import serial.tools.list_ports

        ports = list(serial.tools.list_ports.comports())
        results["serial_ports"] = {
            "count": len(ports),
            "ports": [{"port": p.device, "description": p.description} for p in ports],
        }
    except Exception as exc:
        results["serial_ports"] = {"error": str(exc)}
        warnings.append(f"Cannot enumerate serial ports: {exc}")

    overall = "healthy" if not issues else "unhealthy"
    if warnings and not issues:
        overall = "degraded"

    return {
        "overall": overall,
        "issues": issues,
        "warnings": warnings,
        "checks": results,
        "runtime_version": RUNTIME_VERSION,
        "uptime_seconds": round(state.uptime_seconds, 2),
    }


# =============================================================================
# MCP Server initialization
# =============================================================================


async def _init_runtime(config: RuntimeConfig) -> RuntimeState:
    """Initialize runtime components for standalone MCP mode."""
    global _state

    configure_logging(
        level=config.logging.level,
        fmt=config.logging.format,
        output="console",  # MCP stdio mode — don't write to file
    )

    from packages.core.adapter_manager import AdapterManager
    from packages.core.device_discovery import DeviceDiscovery
    from packages.core.device_registry import DeviceRegistry
    from packages.core.task_scheduler import TaskScheduler
    from packages.types.task import Task

    bus = initialize_event_bus()
    await bus.start()

    db = initialize_database(config.database.url)
    await db.initialize()

    device_registry = DeviceRegistry(db)

    adapter_manager = AdapterManager(config.adapters)
    await adapter_manager.initialize()

    task_scheduler = TaskScheduler(db, bus, max_concurrent=config.scheduler.max_concurrent_tasks)

    async def handle_flash(task: Task) -> None:
        device = state.get_device(task.device_id)
        if not device:
            raise Exception(f"Device {task.device_id} not found")
        adapter = adapter_manager.get_adapter_for_device(device)
        if not adapter:
            raise Exception(f"No adapter found for device {device.name}")
        await adapter.flash(device, task.params["firmware_path"], task, **task.params)

    async def handle_erase(task: Task) -> None:
        device = state.get_device(task.device_id)
        if not device:
            raise Exception(f"Device {task.device_id} not found")
        adapter = adapter_manager.get_adapter_for_device(device)
        if not adapter:
            raise Exception(f"No adapter found for device {device.name}")
        await adapter.erase(device, task)

    async def handle_build(task: Task) -> None:
        board = task.params.get("target_board", "")
        adapter = (
            adapter_manager.get_adapter("esptool")
            if "esp" in board.lower()
            else adapter_manager.get_adapter("arduino-cli")
        )
        if not adapter:
            raise Exception(f"No adapter found to build for board {board}")
        await adapter.build(task.params["project_path"], board, task)

    task_scheduler.register_handler("flash", handle_flash)
    task_scheduler.register_handler("erase", handle_erase)
    task_scheduler.register_handler("build", handle_build)
    await task_scheduler.start()

    plugin_loader = PluginLoader(plugins_dir=config.plugins.directory, event_bus=bus)

    state = RuntimeState(
        config=config,
        event_bus=bus,
        database=db,
        plugin_loader=plugin_loader,
        device_registry=device_registry,
        device_discovery=None,
        task_scheduler=task_scheduler,
    )
    set_runtime_state(state)
    _state = state

    loaded_devices = await device_registry.load_all()
    for d in loaded_devices:
        state.add_device(d)

    device_discovery = DeviceDiscovery(
        event_bus=bus,
        runtime_state=state,
        scan_interval=config.discovery.scan_interval_seconds,
        vid_pid_db_path=config.discovery.vid_pid_database,
    )
    state.device_discovery = device_discovery
    await device_discovery.start()

    await plugin_loader.load_all(disabled=config.plugins.disabled)
    return state


def run_mcp_server() -> None:
    """
    Run MCP server in stdio mode (for Claude Desktop integration).

    Add to claude_desktop_config.json:
    {
        "mcpServers": {
            "uhr": {
                "command": "python",
                "args": ["-m", "packages.mcp-server.server"]
            }
        }
    }
    """
    import asyncio

    config = load_config()

    async def startup() -> None:
        await _init_runtime(config)
        logger.info("MCP Server ready", transport="stdio")

    asyncio.get_event_loop().run_until_complete(startup())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_mcp_server()
