"""
Serial communication endpoints.
Open/close serial ports and write data to them.
Real-time serial data is streamed via WebSocket /ws/v1/serial/{device_id}.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from packages.core.runtime_state import RuntimeState, get_runtime_state
from packages.core.security import RequireAuth
from packages.types.base import BaseResponse
from packages.types.event import Event, EventCategory

router = APIRouter(prefix="/api/v1/serial", tags=["Serial"])

# Track open serial connections: device_id → baud_rate
_open_ports: dict[str, int] = {}


def _get_state() -> RuntimeState:
    return get_runtime_state()


class SerialOpenRequest(BaseModel):
    device_id: str = Field(description="Device ID to open serial port for")
    baud_rate: int = Field(default=115200, description="Serial baud rate")


class SerialWriteRequest(BaseModel):
    device_id: str = Field(description="Device ID to write to")
    data: str = Field(description="Data string to send over serial")
    add_newline: bool = Field(default=True, description="Append \\n to data")


class SerialCloseRequest(BaseModel):
    device_id: str = Field(description="Device ID to close serial port for")


@router.post("/open", response_model=BaseResponse, dependencies=[RequireAuth])
async def open_serial(req: SerialOpenRequest) -> BaseResponse:
    """
    Open a serial connection to a device.
    After opening, subscribe to /ws/v1/serial/{device_id} for real-time data.
    """
    state = _get_state()

    device = state.get_device(req.device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "DEVICE_NOT_FOUND",
                "message": f"Device '{req.device_id}' not found",
                "root_cause": "Device not in registry",
                "suggested_fix": "Run a device scan first",
            },
        )

    if req.device_id in _open_ports:
        return BaseResponse.ok(
            message=f"Serial port already open for device '{req.device_id}'",
            data={
                "device_id": req.device_id,
                "port": device.port,
                "baud_rate": _open_ports[req.device_id],
                "websocket_url": f"/ws/v1/serial/{req.device_id}",
            },
        )

    _open_ports[req.device_id] = req.baud_rate

    await state.event_bus.publish(
        Event.create(
            event_type="serial.opened",
            category=EventCategory.SERIAL,
            source="api",
            payload={
                "device_id": req.device_id,
                "port": device.port,
                "baud_rate": req.baud_rate,
            },
        )
    )

    return BaseResponse.ok(
        message=f"Serial port opened for device '{req.device_id}'",
        data={
            "device_id": req.device_id,
            "port": device.port,
            "baud_rate": req.baud_rate,
            "websocket_url": f"/ws/v1/serial/{req.device_id}",
        },
    )


@router.post("/close", response_model=BaseResponse, dependencies=[RequireAuth])
async def close_serial(req: SerialCloseRequest) -> BaseResponse:
    """Close an open serial connection."""
    state = _get_state()

    if req.device_id not in _open_ports:
        return BaseResponse.ok(
            message=f"Serial port was not open for device '{req.device_id}'",
            data={"device_id": req.device_id},
        )

    del _open_ports[req.device_id]

    await state.event_bus.publish(
        Event.create(
            event_type="serial.closed",
            category=EventCategory.SERIAL,
            source="api",
            payload={"device_id": req.device_id},
        )
    )

    return BaseResponse.ok(
        message=f"Serial port closed for device '{req.device_id}'",
        data={"device_id": req.device_id},
    )


@router.post("/write", response_model=BaseResponse, dependencies=[RequireAuth])
async def serial_write(req: SerialWriteRequest) -> BaseResponse:
    """Write data to an open serial port."""
    state = _get_state()

    if req.device_id not in _open_ports:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "SERIAL_NOT_OPEN",
                "message": f"Serial port is not open for device '{req.device_id}'",
                "root_cause": "Open the serial port first",
                "suggested_fix": "Call POST /api/v1/serial/open first",
            },
        )

    data = req.data + ("\n" if req.add_newline else "")

    await state.event_bus.publish(
        Event.create(
            event_type="serial.write_requested",
            category=EventCategory.SERIAL,
            source="api",
            payload={"device_id": req.device_id, "data": data},
        )
    )

    return BaseResponse.ok(
        message=f"Data queued for serial write to device '{req.device_id}'",
        data={"device_id": req.device_id, "bytes": len(data.encode())},
    )
