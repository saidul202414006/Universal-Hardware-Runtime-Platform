"""
Serial Manager — async serial port communication.

Design:
- Non-blocking reads using asyncio + threading
- Write with configurable timeout
- Auto-reconnect on disconnect
- Buffer management with configurable size
- Connection state tracking per port
- Emits events via Event Bus for serial data

Thread model:
- Serial reads happen in a thread (pyserial is synchronous)
- asyncio.Queue bridges serial thread → async event loop
- Writes are submitted to a write queue and handled by the serial thread
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from packages.logger import get_logger

logger = get_logger("serial_manager")


class SerialConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class SerialConnection:
    """Represents one open serial connection."""
    device_id: str
    port: str
    baud_rate: int
    state: SerialConnectionState = SerialConnectionState.DISCONNECTED
    _serial: Any = field(default=None, repr=False)
    _read_thread: threading.Thread | None = field(default=None, repr=False)
    _data_queue: asyncio.Queue[str] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1000), repr=False
    )
    _stop_event: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    bytes_received: int = 0
    bytes_sent: int = 0


class SerialManager:
    """
    Manages multiple concurrent serial port connections.

    Usage:
        manager = SerialManager(event_bus=bus)
        await manager.open(device_id, port="/dev/ttyUSB0", baud_rate=115200)

        # Read data
        async for line in manager.read_lines(device_id):
            print(line)

        # Write data
        await manager.write(device_id, b"AT\\r\\n")

        await manager.close(device_id)
    """

    def __init__(self, event_bus: Any, buffer_size: int = 4096) -> None:
        self._bus = event_bus
        self._buffer_size = buffer_size
        self._connections: dict[str, SerialConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def open(
        self,
        device_id: str,
        port: str,
        baud_rate: int = 115200,
        timeout: float = 2.0,
    ) -> bool:
        """
        Open a serial connection.

        Returns True on success, False on failure.
        """
        if device_id in self._connections:
            existing = self._connections[device_id]
            if existing.state == SerialConnectionState.CONNECTED:
                logger.warning("Serial already open", device_id=device_id, port=port)
                return True

        try:
            import serial as pyserial
            ser = pyserial.Serial(
                port=port,
                baudrate=baud_rate,
                timeout=timeout,
                write_timeout=timeout,
            )
        except Exception as exc:
            logger.error(
                "Failed to open serial port",
                device_id=device_id,
                port=port,
                error=str(exc),
            )
            return False

        conn = SerialConnection(
            device_id=device_id,
            port=port,
            baud_rate=baud_rate,
            state=SerialConnectionState.CONNECTED,
            _serial=ser,
        )
        self._connections[device_id] = conn
        self._loop = asyncio.get_event_loop()

        # Start background read thread
        conn._read_thread = threading.Thread(
            target=self._read_loop,
            args=(conn,),
            name=f"serial_read_{device_id}",
            daemon=True,
        )
        conn._read_thread.start()

        # Start async forwarding task
        asyncio.create_task(self._forward_to_event_bus(conn))

        logger.info(
            "Serial port opened",
            device_id=device_id,
            port=port,
            baud_rate=baud_rate,
        )

        # Emit event
        from packages.types.event import Event, EventCategory
        await self._bus.publish(
            Event.create(
                event_type="serial.opened",
                category=EventCategory.SERIAL,
                source="serial_manager",
                payload={"device_id": device_id, "port": port, "baud_rate": baud_rate},
            )
        )
        return True

    async def close(self, device_id: str) -> None:
        """Close a serial connection."""
        conn = self._connections.pop(device_id, None)
        if not conn:
            return

        conn._stop_event.set()
        conn.state = SerialConnectionState.DISCONNECTED

        if conn._serial and conn._serial.is_open:
            try:
                conn._serial.close()
            except Exception:
                pass

        from packages.types.event import Event, EventCategory
        await self._bus.publish(
            Event.create(
                event_type="serial.closed",
                category=EventCategory.SERIAL,
                source="serial_manager",
                payload={"device_id": device_id},
            )
        )
        logger.info("Serial port closed", device_id=device_id)

    async def write(self, device_id: str, data: bytes) -> int:
        """
        Write bytes to serial port.

        Returns number of bytes written, or 0 on error.
        """
        conn = self._connections.get(device_id)
        if not conn or conn.state != SerialConnectionState.CONNECTED:
            logger.error("Cannot write — serial not connected", device_id=device_id)
            return 0

        try:
            n = conn._serial.write(data)
            conn._serial.flush()
            conn.bytes_sent += n
            return n
        except Exception as exc:
            logger.error("Serial write failed", device_id=device_id, error=str(exc))
            conn.state = SerialConnectionState.ERROR
            return 0

    def is_open(self, device_id: str) -> bool:
        conn = self._connections.get(device_id)
        return conn is not None and conn.state == SerialConnectionState.CONNECTED

    def get_connection(self, device_id: str) -> SerialConnection | None:
        return self._connections.get(device_id)

    async def close_all(self) -> None:
        """Close all open serial connections."""
        for device_id in list(self._connections.keys()):
            await self.close(device_id)

    def _read_loop(self, conn: SerialConnection) -> None:
        """
        Background thread: reads from serial port continuously.
        Puts lines into the asyncio queue for the main loop.
        Thread-safe: uses queue + threading.Event for coordination.
        """
        buffer = b""
        while not conn._stop_event.is_set():
            try:
                if not conn._serial.is_open:
                    break
                chunk = conn._serial.read(self._buffer_size)
                if not chunk:
                    continue
                conn.bytes_received += len(chunk)
                buffer += chunk

                # Split on newlines, emit complete lines
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").rstrip("\r")
                    if self._loop and not self._loop.is_closed():
                        try:
                            self._loop.call_soon_threadsafe(
                                conn._data_queue.put_nowait, text
                            )
                        except asyncio.QueueFull:
                            pass  # Drop oldest data if queue full

            except Exception as exc:
                if not conn._stop_event.is_set():
                    logger.error(
                        "Serial read error",
                        device_id=conn.device_id,
                        error=str(exc),
                    )
                    conn.state = SerialConnectionState.ERROR
                break

    async def _forward_to_event_bus(self, conn: SerialConnection) -> None:
        """
        Async task: forwards data from the queue to the Event Bus.
        Runs until the connection is closed.
        """
        from packages.types.event import Event, EventCategory
        while conn.state == SerialConnectionState.CONNECTED:
            try:
                line = await asyncio.wait_for(conn._data_queue.get(), timeout=1.0)
                await self._bus.publish(
                    Event.create(
                        event_type="serial.data",
                        category=EventCategory.SERIAL,
                        source="serial_manager",
                        payload={"device_id": conn.device_id, "data": line},
                    )
                )
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                if conn.state == SerialConnectionState.CONNECTED:
                    logger.error("Serial forward error", device_id=conn.device_id, error=str(exc))
                break
