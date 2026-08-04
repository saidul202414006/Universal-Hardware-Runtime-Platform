"""
WebSocket endpoints for real-time communication.

Three WebSocket channels:
1. /ws/v1/events — all runtime events (device connect/disconnect, task progress, etc.)
2. /ws/v1/serial/{device_id} — live serial output from a device
3. /ws/v1/tasks/{task_id} — real-time progress for a specific task

All WebSocket connections authenticate via ?api_key=<key> query parameter
(browsers cannot set custom headers on WebSocket connections).

Protocol:
- Server sends JSON messages
- Client can send "ping" → server responds "pong"
- On disconnect, server cleans up subscription
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from packages.core.runtime_state import RuntimeState, get_runtime_state
from packages.core.security import verify_ws_api_key
from packages.logger import get_logger
from packages.types.event import Event

logger = get_logger("websocket")

router = APIRouter(tags=["WebSocket"])


def _serialize_event(event: Event) -> str:
    """Convert an Event to a JSON string for WebSocket transmission."""
    return json.dumps({
        "type": "event",
        "event_id": event.event_id,
        "event_type": event.event_type,
        "category": event.category.value,
        "priority": event.priority.value,
        "timestamp": event.timestamp.isoformat(),
        "source": event.source,
        "payload": event.payload,
        "correlation_id": event.correlation_id,
    })


@router.websocket("/ws/v1/events")
async def ws_events(websocket: WebSocket) -> None:
    """
    Real-time event stream WebSocket.

    Connect with: ws://host:port/ws/v1/events?api_key=<key>

    Receives all runtime events as JSON:
    {
        "type": "event",
        "event_type": "device.connected",
        "category": "device",
        "payload": {...},
        ...
    }
    """
    # Auth check
    if not await verify_ws_api_key(websocket.scope.get("app"), websocket.query_params.get("api_key")):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    state = get_runtime_state()
    conn_id = id(websocket)

    logger.info("WebSocket event stream connected", conn_id=conn_id)

    # Subscribe to ALL events
    async def forward_event(event: Event) -> None:
        try:
            await websocket.send_text(_serialize_event(event))
        except Exception:
            pass  # Client disconnected — will be cleaned up below

    sub_id = state.event_bus.subscribe("*", forward_event, f"ws_events_{conn_id}")

    # Send welcome message
    await websocket.send_text(json.dumps({
        "type": "connected",
        "message": "Connected to event stream",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    try:
        while True:
            # Wait for any client message (keepalive ping)
            data = await websocket.receive_text()
            if data.strip() == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()}))
    except WebSocketDisconnect:
        logger.info("WebSocket event stream disconnected", conn_id=conn_id)
    except Exception as exc:
        logger.error("WebSocket event stream error", conn_id=conn_id, error=str(exc))
    finally:
        state.event_bus.unsubscribe(sub_id)


@router.websocket("/ws/v1/serial/{device_id}")
async def ws_serial(websocket: WebSocket, device_id: str) -> None:
    """
    Real-time serial monitor WebSocket for a specific device.

    Connect with: ws://host:port/ws/v1/serial/{device_id}?api_key=<key>

    Receives serial data events as JSON:
    {
        "type": "serial_data",
        "device_id": "...",
        "data": "Hello from ESP32!\n",
        "timestamp": "..."
    }

    Send data to serial:
    { "type": "write", "data": "AT\r\n" }
    """
    if not await verify_ws_api_key(None, websocket.query_params.get("api_key")):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    state = get_runtime_state()

    # Verify device exists
    device = state.get_device(device_id)
    if not device:
        await websocket.accept()
        await websocket.send_text(json.dumps({
            "type": "error",
            "error_code": "DEVICE_NOT_FOUND",
            "message": f"Device '{device_id}' not found",
        }))
        await websocket.close(code=4004, reason="Device not found")
        return

    await websocket.accept()
    conn_id = id(websocket)
    logger.info("WebSocket serial connected", device_id=device_id, conn_id=conn_id)

    # Subscribe to serial data events for this specific device
    async def forward_serial(event: Event) -> None:
        if event.payload.get("device_id") != device_id:
            return
        try:
            await websocket.send_text(json.dumps({
                "type": "serial_data",
                "device_id": device_id,
                "data": event.payload.get("data", ""),
                "timestamp": event.timestamp.isoformat(),
            }))
        except Exception:
            pass

    sub_id = state.event_bus.subscribe("serial.data", forward_serial, f"ws_serial_{device_id}_{conn_id}")

    await websocket.send_text(json.dumps({
        "type": "connected",
        "device_id": device_id,
        "port": device.port,
        "message": f"Connected to serial monitor for {device.name}",
    }))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("type") == "write":
                    # Forward write request to serial
                    from packages.types.event import EventCategory
                    await state.event_bus.publish(
                        Event.create(
                            event_type="serial.write_requested",
                            category=EventCategory.SERIAL,
                            source=f"ws_serial_{conn_id}",
                            payload={"device_id": device_id, "data": msg.get("data", "")},
                        )
                    )
                elif raw.strip() == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        logger.info("WebSocket serial disconnected", device_id=device_id, conn_id=conn_id)
    except Exception as exc:
        logger.error("WebSocket serial error", device_id=device_id, error=str(exc))
    finally:
        state.event_bus.unsubscribe(sub_id)


@router.websocket("/ws/v1/tasks/{task_id}")
async def ws_task_progress(websocket: WebSocket, task_id: str) -> None:
    """
    Real-time task progress WebSocket.

    Connect with: ws://host:port/ws/v1/tasks/{task_id}?api_key=<key>

    Receives task progress events:
    {
        "type": "task_progress",
        "task_id": "...",
        "state": "running",
        "progress": 45.0,
        "message": "Writing sector 3/8",
        "log_line": "..."
    }

    Closes automatically when task reaches terminal state.
    """
    if not await verify_ws_api_key(None, websocket.query_params.get("api_key")):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    state = get_runtime_state()
    await websocket.accept()
    conn_id = id(websocket)
    logger.info("WebSocket task progress connected", task_id=task_id, conn_id=conn_id)

    terminal_states = {"completed", "failed", "cancelled"}
    closed = asyncio.Event()

    async def forward_task_event(event: Event) -> None:
        if event.payload.get("task_id") != task_id:
            return
        try:
            await websocket.send_text(json.dumps({
                "type": "task_event",
                "task_id": task_id,
                "event_type": event.event_type,
                "state": event.payload.get("state", ""),
                "progress": event.payload.get("progress", 0),
                "message": event.payload.get("message", ""),
                "log_line": event.payload.get("log_line", ""),
                "timestamp": event.timestamp.isoformat(),
            }))
            # Auto-close on terminal state
            if event.payload.get("state") in terminal_states:
                closed.set()
        except Exception:
            pass

    sub_id = state.event_bus.subscribe("task.*", forward_task_event, f"ws_task_{task_id}_{conn_id}")

    await websocket.send_text(json.dumps({
        "type": "connected",
        "task_id": task_id,
        "message": f"Monitoring task {task_id}",
    }))

    try:
        # Wait until task completes or client disconnects
        receive_task = asyncio.create_task(websocket.receive_text())
        closed_task = asyncio.create_task(closed.wait())

        done, pending = await asyncio.wait(
            [receive_task, closed_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()

        if closed.is_set():
            await websocket.send_text(json.dumps({
                "type": "task_complete",
                "task_id": task_id,
                "message": "Task reached terminal state",
            }))
            await websocket.close()

    except WebSocketDisconnect:
        logger.info("WebSocket task progress disconnected", task_id=task_id)
    except Exception as exc:
        logger.error("WebSocket task error", task_id=task_id, error=str(exc))
    finally:
        state.event_bus.unsubscribe(sub_id)
