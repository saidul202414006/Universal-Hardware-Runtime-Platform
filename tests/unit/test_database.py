"""
Unit tests for the database layer.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from packages.core.database import (
    DatabaseManager,
    DeviceRecord,
    TaskRecord,
)


@pytest.fixture
async def db() -> DatabaseManager:
    """In-memory SQLite database for testing."""
    manager = DatabaseManager("sqlite+aiosqlite:///:memory:", echo=False)
    await manager.initialize()
    yield manager
    await manager.close()


class TestDatabaseInit:
    async def test_health_check(self, db: DatabaseManager) -> None:
        assert await db.health_check() is True


class TestDeviceRecord:
    async def test_insert_and_read(self, db: DatabaseManager) -> None:
        async with db.session() as session:
            session.add(DeviceRecord(
                id="dev-001",
                name="ESP32",
                device_type="esp32",
                state="ready",
                port="/dev/ttyUSB0",
                vid="0x10C4",
                pid="0xEA60",
            ))

        async with db.session() as session:
            result = await session.execute(
                select(DeviceRecord).where(DeviceRecord.id == "dev-001")
            )
            found = result.scalar_one_or_none()

        assert found is not None
        assert found.name == "ESP32"
        assert found.state == "ready"
        assert found.vid == "0x10C4"

    async def test_update_device(self, db: DatabaseManager) -> None:
        async with db.session() as session:
            session.add(DeviceRecord(
                id="dev-002",
                name="Arduino",
                device_type="arduino",
                state="detected",
                port="COM3",
            ))

        async with db.session() as session:
            result = await session.execute(
                select(DeviceRecord).where(DeviceRecord.id == "dev-002")
            )
            device = result.scalar_one()
            device.state = "ready"

        async with db.session() as session:
            result = await session.execute(
                select(DeviceRecord).where(DeviceRecord.id == "dev-002")
            )
            updated = result.scalar_one()

        assert updated.state == "ready"


class TestTaskRecord:
    async def test_insert_task(self, db: DatabaseManager) -> None:
        async with db.session() as session:
            session.add(TaskRecord(
                id="task-001",
                task_type="flash",
                state="created",
                correlation_id="corr-001",
            ))

        async with db.session() as session:
            result = await session.execute(
                select(TaskRecord).where(TaskRecord.id == "task-001")
            )
            task = result.scalar_one_or_none()

        assert task is not None
        assert task.task_type == "flash"
        assert task.progress == 0.0
