"""
Device Registry — persistent device storage backed by the database.

Responsibilities:
- Persist device records across runtime restarts
- Load known devices from DB on startup
- Sync in-memory state changes to DB
- Track connection history
- Provide CRUD operations for device records

The in-memory registry (RuntimeState.devices) is the single source of truth
for live state. The database is for persistence and history.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from packages.core.database import DatabaseManager, DeviceRecord
from packages.logger import get_logger
from packages.types.device import Device, DeviceState, DeviceType, Capability, DeviceInfo

logger = get_logger("device_registry")


def _device_to_record(device: Device) -> DeviceRecord:
    """Convert Device domain model to DB record."""
    return DeviceRecord(
        id=device.id,
        name=device.name,
        device_type=device.device_type.value,
        state=DeviceState.OFFLINE.value,  # Always store as offline (offline = not connected right now)
        port=device.port,
        vid=device.vid,
        pid=device.pid,
        serial_number=device.serial_number,
        manufacturer=device.manufacturer,
        product=device.product,
        adapter_id=device.adapter_id,
        plugin_id=device.plugin_id,
        capabilities=json.dumps([c.value for c in device.capabilities]),
        info=json.dumps(device.info.model_dump(mode="json")),
        first_seen=device.first_seen,
        last_seen=device.last_seen,
        last_state_change=device.last_state_change,
    )


def _record_to_device(record: DeviceRecord) -> Device:
    """Convert DB record back to Device domain model."""
    try:
        caps_raw = json.loads(record.capabilities or "[]")
        capabilities = [Capability(c) for c in caps_raw if c in Capability._value2member_map_]
    except Exception:
        capabilities = []

    try:
        info_raw = json.loads(record.info or "{}")
        info = DeviceInfo(**info_raw)
    except Exception:
        info = DeviceInfo()

    return Device(
        id=record.id,
        name=record.name,
        device_type=DeviceType(record.device_type) if record.device_type in DeviceType._value2member_map_ else DeviceType.UNKNOWN,
        state=DeviceState.OFFLINE,  # Loaded devices start as offline (not connected)
        port=record.port,
        vid=record.vid,
        pid=record.pid,
        serial_number=record.serial_number,
        manufacturer=record.manufacturer,
        product=record.product,
        adapter_id=record.adapter_id,
        plugin_id=record.plugin_id,
        capabilities=capabilities,
        info=info,
        first_seen=record.first_seen,
        last_seen=record.last_seen,
        last_state_change=record.last_state_change,
    )


class DeviceRegistry:
    """
    Persistent device registry — bridges in-memory state and database.

    Usage:
        registry = DeviceRegistry(database=db)
        await registry.load_all()   # Load known devices from DB into state

        await registry.save(device)  # Persist a device to DB
        await registry.update_state(device)  # Update device state in DB
    """

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database

    async def load_all(self) -> list[Device]:
        """
        Load all known devices from DB.
        Returns list of Device objects (all in OFFLINE state).
        """
        devices: list[Device] = []
        try:
            async with self._db.session() as session:
                result = await session.execute(select(DeviceRecord))
                records = result.scalars().all()
                for record in records:
                    device = _record_to_device(record)
                    devices.append(device)
            logger.info("Loaded devices from registry", count=len(devices))
        except Exception as exc:
            logger.error("Failed to load devices from DB", error=str(exc))
        return devices

    async def save(self, device: Device) -> None:
        """
        Save or update a device in the database.
        Uses INSERT OR REPLACE (upsert) semantics.
        """
        try:
            async with self._db.session() as session:
                existing = await session.get(DeviceRecord, device.id)
                if existing:
                    # Update existing record
                    existing.name = device.name
                    existing.device_type = device.device_type.value
                    existing.port = device.port
                    existing.vid = device.vid
                    existing.pid = device.pid
                    existing.serial_number = device.serial_number
                    existing.manufacturer = device.manufacturer
                    existing.product = device.product
                    existing.adapter_id = device.adapter_id
                    existing.plugin_id = device.plugin_id
                    existing.capabilities = json.dumps([c.value for c in device.capabilities])
                    existing.info = json.dumps(device.info.model_dump(mode="json"))
                    existing.last_seen = datetime.now(timezone.utc)
                else:
                    record = _device_to_record(device)
                    session.add(record)
            logger.debug("Device saved to registry", device_id=device.id)
        except Exception as exc:
            logger.error("Failed to save device", device_id=device.id, error=str(exc))

    async def update_last_seen(self, device_id: str) -> None:
        """Update the last_seen timestamp for a device."""
        try:
            async with self._db.session() as session:
                await session.execute(
                    update(DeviceRecord)
                    .where(DeviceRecord.id == device_id)
                    .values(last_seen=datetime.now(timezone.utc))
                )
        except Exception as exc:
            logger.error("Failed to update last_seen", device_id=device_id, error=str(exc))

    async def delete(self, device_id: str) -> bool:
        """Remove a device from the registry."""
        try:
            async with self._db.session() as session:
                record = await session.get(DeviceRecord, device_id)
                if record:
                    await session.delete(record)
                    return True
            return False
        except Exception as exc:
            logger.error("Failed to delete device", device_id=device_id, error=str(exc))
            return False

    async def get(self, device_id: str) -> Device | None:
        """Load a single device from DB."""
        try:
            async with self._db.session() as session:
                record = await session.get(DeviceRecord, device_id)
                if record:
                    return _record_to_device(record)
        except Exception as exc:
            logger.error("Failed to get device", device_id=device_id, error=str(exc))
        return None
