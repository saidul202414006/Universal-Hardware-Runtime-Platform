"""Device management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from packages.core.runtime_state import RuntimeState, get_runtime_state
from packages.core.security import RequireAuth
from packages.types.base import BaseResponse
from packages.types.device import Device, DeviceState
from packages.types.event import Event, EventCategory, EventType

router = APIRouter(prefix="/api/v1/devices", tags=["Devices"])


def _get_state() -> RuntimeState:
    return get_runtime_state()


@router.get("", response_model=BaseResponse, dependencies=[RequireAuth])
async def list_devices() -> BaseResponse:
    """List all connected and known devices."""
    state = _get_state()
    devices = state.list_devices()
    return BaseResponse.ok(
        message=f"Found {len(devices)} device(s)",
        data={
            "devices": [d.model_dump(mode="json") for d in devices],
            "total": len(devices),
        },
    )


@router.get("/{device_id}", response_model=BaseResponse, dependencies=[RequireAuth])
async def get_device(device_id: str) -> BaseResponse:
    """Get details for a specific device."""
    state = _get_state()
    device = state.get_device(device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "DEVICE_NOT_FOUND",
                "message": f"Device '{device_id}' not found",
                "root_cause": "Device ID does not exist in registry",
                "suggested_fix": "Run a device scan first or check the device ID",
            },
        )
    return BaseResponse.ok(
        message="Device found",
        data=device.model_dump(mode="json"),
    )


@router.post("/scan", response_model=BaseResponse, dependencies=[RequireAuth])
async def scan_devices() -> BaseResponse:
    """
    Trigger an immediate device scan.
    Device discovery runs continuously in the background,
    but this forces an immediate scan cycle.
    """
    state = _get_state()

    # Emit a scan request event — the device discovery component handles it
    await state.event_bus.publish(
        Event.create(
            event_type="device.scan_requested",
            category=EventCategory.DEVICE,
            source="api",
            payload={"trigger": "manual"},
        )
    )

    # Return current known devices immediately
    devices = state.list_devices()
    return BaseResponse.ok(
        message="Device scan triggered. Results will appear via WebSocket events.",
        data={
            "current_devices": [d.model_dump(mode="json") for d in devices],
            "total": len(devices),
            "note": "Subscribe to /ws/v1/events for real-time device updates",
        },
    )


@router.get("/{device_id}/capabilities", response_model=BaseResponse, dependencies=[RequireAuth])
async def get_device_capabilities(device_id: str) -> BaseResponse:
    """Get the capabilities of a specific device."""
    state = _get_state()
    device = state.get_device(device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "DEVICE_NOT_FOUND",
                "message": f"Device '{device_id}' not found",
                "root_cause": "Device ID does not exist in registry",
                "suggested_fix": "Run a device scan first",
            },
        )
    return BaseResponse.ok(
        message=f"Device '{device.name}' capabilities retrieved",
        data={
            "device_id": device_id,
            "device_name": device.name,
            "capabilities": [c.value for c in device.capabilities],
            "state": device.state.value,
            "is_available": device.is_available,
        },
    )
