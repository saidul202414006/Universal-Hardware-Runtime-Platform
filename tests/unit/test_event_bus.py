"""
Unit tests for the Event Bus.
Tests pub/sub, wildcard matching, priority ordering, and exception isolation.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from packages.core.event_bus import EventBus, initialize_event_bus
from packages.types.event import Event, EventCategory, EventPriority, EventType


@pytest.fixture
async def bus() -> EventBus:
    """Provide a fresh, started EventBus for each test."""
    b = EventBus(queue_size=100)
    await b.start()
    yield b
    await b.stop()


class TestEventBusSubscriptions:
    async def test_subscribe_and_publish(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe("device.connected", handler, "test")

        event = Event.create("device.connected", EventCategory.DEVICE, "test")
        await bus.publish(event)
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].event_type == "device.connected"

    async def test_wildcard_subscription(self, bus: EventBus) -> None:
        received: list[str] = []

        async def handler(event: Event) -> None:
            received.append(event.event_type)

        bus.subscribe("device.*", handler, "test")

        await bus.publish(Event.create("device.connected", EventCategory.DEVICE, "src"))
        await bus.publish(Event.create("device.disconnected", EventCategory.DEVICE, "src"))
        await bus.publish(Event.create("flash.started", EventCategory.FLASH, "src"))
        await asyncio.sleep(0.05)

        assert "device.connected" in received
        assert "device.disconnected" in received
        assert "flash.started" not in received  # Should not match "device.*"

    async def test_catch_all_subscription(self, bus: EventBus) -> None:
        received: list[str] = []

        async def handler(event: Event) -> None:
            received.append(event.event_type)

        bus.subscribe("*", handler, "catch_all")

        await bus.publish(Event.create("device.connected", EventCategory.DEVICE, "src"))
        await bus.publish(Event.create("flash.progress", EventCategory.FLASH, "src"))
        await bus.publish(Event.create("task.completed", EventCategory.TASK, "src"))
        await asyncio.sleep(0.05)

        assert len(received) >= 3

    async def test_unsubscribe(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        sub_id = bus.subscribe("device.*", handler, "test")

        await bus.publish(Event.create("device.connected", EventCategory.DEVICE, "src"))
        await asyncio.sleep(0.05)
        assert len(received) == 1

        # Unsubscribe
        result = bus.unsubscribe(sub_id)
        assert result is True

        await bus.publish(Event.create("device.disconnected", EventCategory.DEVICE, "src"))
        await asyncio.sleep(0.05)
        assert len(received) == 1  # No new events

    async def test_unsubscribe_nonexistent(self, bus: EventBus) -> None:
        result = bus.unsubscribe("sub_99999")
        assert result is False

    async def test_multiple_subscribers_same_pattern(self, bus: EventBus) -> None:
        received_a: list[Event] = []
        received_b: list[Event] = []

        async def handler_a(event: Event) -> None:
            received_a.append(event)

        async def handler_b(event: Event) -> None:
            received_b.append(event)

        bus.subscribe("device.*", handler_a, "a")
        bus.subscribe("device.*", handler_b, "b")

        await bus.publish(Event.create("device.connected", EventCategory.DEVICE, "src"))
        await asyncio.sleep(0.05)

        assert len(received_a) == 1
        assert len(received_b) == 1


class TestEventBusExceptionIsolation:
    async def test_bad_handler_does_not_crash_bus(self, bus: EventBus) -> None:
        """A handler that raises must NOT affect other handlers or the bus."""
        good_received: list[Event] = []

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("Intentional crash in handler")

        async def good_handler(event: Event) -> None:
            good_received.append(event)

        bus.subscribe("test.*", bad_handler, "bad")
        bus.subscribe("test.*", good_handler, "good")

        await bus.publish(Event.create("test.event", EventCategory.SYSTEM, "src"))
        await asyncio.sleep(0.05)

        # Bus should still be running
        assert bus._running
        # Good handler should still have received the event
        assert len(good_received) == 1
        # Error count should be incremented
        stats = bus.get_stats()
        assert stats["total_errors"] >= 1

    async def test_bus_continues_after_multiple_errors(self, bus: EventBus) -> None:
        count = 0

        async def bad_handler(event: Event) -> None:
            raise Exception("Error!")

        async def good_handler(event: Event) -> None:
            nonlocal count
            count += 1

        bus.subscribe("*", bad_handler, "bad")
        bus.subscribe("*", good_handler, "good")

        for i in range(5):
            await bus.publish(Event.create("test.event", EventCategory.SYSTEM, "src"))

        await asyncio.sleep(0.3)  # Allow time for all 5 events to dispatch on ARM
        assert count == 5  # All delivered to good handler
        assert bus._running  # Bus still alive


class TestEventBusStats:
    async def test_stats_tracking(self, bus: EventBus) -> None:
        async def noop(event: Event) -> None:
            pass

        bus.subscribe("*", noop, "noop")

        await bus.publish(Event.create("device.connected", EventCategory.DEVICE, "src"))
        await bus.publish(Event.create("device.disconnected", EventCategory.DEVICE, "src"))
        await asyncio.sleep(0.05)

        stats = bus.get_stats()
        assert stats["running"] is True
        assert stats["subscription_count"] >= 1
        assert stats["total_events_processed"] >= 2


class TestEventBusLifecycle:
    async def test_publish_before_start_raises(self) -> None:
        bus = EventBus()
        with pytest.raises(RuntimeError, match="not running"):
            await bus.publish(Event.create("test", EventCategory.SYSTEM, "src"))

    async def test_start_stop_cycle(self) -> None:
        bus = EventBus()
        assert not bus._running
        await bus.start()
        assert bus._running
        await bus.stop()
        assert not bus._running

    async def test_double_start_is_safe(self) -> None:
        bus = EventBus()
        await bus.start()
        await bus.start()  # Should log warning, not crash
        await bus.stop()
