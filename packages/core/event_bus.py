"""
Async Event Bus — the internal communication backbone.

Architecture rules enforced here:
- ALL inter-module communication goes through the Event Bus
- No direct cross-module imports
- Publishers are NEVER blocked (fire-and-forget with async queue)
- Subscriber exceptions are CAUGHT and logged — never propagate to publisher
- CRITICAL priority events are always delivered (persistent queue)

Design:
- asyncio.Queue per priority level (4 queues: CRITICAL, HIGH, NORMAL, LOW)
- Background dispatcher task drains queues in priority order
- Subscribers register handler coroutines with event type patterns
- Pattern matching supports wildcards: "device.*", "*.error"
- Correlation ID propagation for distributed tracing
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections import defaultdict
from typing import Any, Callable, Coroutine

from packages.logger import get_logger
from packages.types.event import Event, EventCategory, EventPriority, EventType

logger = get_logger("event_bus")

# Type alias for event handler coroutines
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class Subscription:
    """Represents a single event subscription."""

    def __init__(
        self,
        subscription_id: str,
        pattern: str,
        handler: EventHandler,
        subscriber_name: str,
    ) -> None:
        self.subscription_id = subscription_id
        self.pattern = pattern          # e.g., "device.*", "flash.progress", "*"
        self.handler = handler
        self.subscriber_name = subscriber_name

    def matches(self, event_type: str) -> bool:
        """Check if this subscription matches the given event type."""
        return fnmatch.fnmatch(event_type, self.pattern)


class EventBus:
    """
    Async Publish/Subscribe Event Bus.

    Usage:
        bus = EventBus()
        await bus.start()

        # Subscribe
        sub_id = bus.subscribe("device.*", my_handler, subscriber_name="my_component")

        # Publish (non-blocking — returns immediately)
        await bus.publish(Event.create("device.connected", ...))

        # Cleanup
        bus.unsubscribe(sub_id)
        await bus.stop()
    """

    def __init__(self, queue_size: int = 1000) -> None:
        self._subscriptions: list[Subscription] = []
        self._sub_counter = 0

        # Separate queues per priority for proper ordering
        self._queues: dict[EventPriority, asyncio.Queue[Event]] = {
            EventPriority.CRITICAL: asyncio.Queue(maxsize=0),  # Unbounded for critical
            EventPriority.HIGH: asyncio.Queue(maxsize=queue_size),
            EventPriority.NORMAL: asyncio.Queue(maxsize=queue_size),
            EventPriority.LOW: asyncio.Queue(maxsize=queue_size),
        }

        self._dispatcher_task: asyncio.Task[None] | None = None
        self._running = False
        self._event_count = 0
        self._error_count = 0

        # Stats per event type
        self._stats: dict[str, int] = defaultdict(int)

    async def start(self) -> None:
        """Start the event bus dispatcher. Must be called before publishing."""
        if self._running:
            logger.warning("EventBus already running")
            return

        self._running = True
        self._dispatcher_task = asyncio.create_task(
            self._dispatcher_loop(), name="event_bus_dispatcher"
        )
        logger.info("EventBus started")

        # Emit startup event
        await self.publish(
            Event.create(
                event_type=EventType.SYSTEM_STARTUP,
                category=EventCategory.SYSTEM,
                source="event_bus",
                payload={"message": "Event Bus started"},
                priority=EventPriority.HIGH,
            )
        )

    async def stop(self) -> None:
        """Stop the event bus gracefully. Drains remaining events."""
        if not self._running:
            return

        logger.info("EventBus stopping", total_events=self._event_count)
        self._running = False

        if self._dispatcher_task:
            # Allow a short time to drain remaining events
            try:
                await asyncio.wait_for(
                    self._drain_all_queues(), timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("EventBus drain timed out — some events may be lost")

            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None

        logger.info(
            "EventBus stopped",
            total_events=self._event_count,
            total_errors=self._error_count,
        )

    def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        subscriber_name: str = "unknown",
    ) -> str:
        """
        Register an event handler.

        Args:
            pattern: Event type pattern with optional wildcards.
                     Examples: "device.*", "flash.progress", "*", "*.error"
            handler: Async coroutine to call when a matching event arrives.
            subscriber_name: Human-readable name for logging/debugging.

        Returns:
            Subscription ID — use this to unsubscribe later.
        """
        self._sub_counter += 1
        sub_id = f"sub_{self._sub_counter}"
        subscription = Subscription(sub_id, pattern, handler, subscriber_name)
        self._subscriptions.append(subscription)
        logger.debug(
            "Subscription registered",
            sub_id=sub_id,
            pattern=pattern,
            subscriber=subscriber_name,
        )
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """
        Remove a subscription.

        Returns:
            True if subscription was found and removed, False otherwise.
        """
        before = len(self._subscriptions)
        self._subscriptions = [
            s for s in self._subscriptions if s.subscription_id != subscription_id
        ]
        removed = len(self._subscriptions) < before
        if removed:
            logger.debug("Subscription removed", sub_id=subscription_id)
        return removed

    async def publish(self, event: Event) -> None:
        """
        Publish an event to all matching subscribers.

        This method is NON-BLOCKING — it enqueues the event and returns immediately.
        The actual handler invocations happen in the background dispatcher.

        Args:
            event: The event to publish.

        Raises:
            RuntimeError: If EventBus is not started.
        """
        if not self._running:
            raise RuntimeError(
                "EventBus is not running. Call await bus.start() first."
            )

        queue = self._queues[event.priority]

        try:
            queue.put_nowait(event)
            self._stats[event.event_type] += 1
        except asyncio.QueueFull:
            # Queue overflow — log error but don't block publisher
            self._error_count += 1
            logger.error(
                "EventBus queue overflow — event dropped",
                event_type=event.event_type,
                priority=event.priority.name,
                queue_size=queue.qsize(),
            )

    async def publish_and_wait(self, event: Event, timeout: float = 5.0) -> None:
        """
        Publish and wait for all handlers to complete.
        Use only in tests or when synchronous completion is required.
        """
        await self.publish(event)
        # Give dispatcher time to process
        await asyncio.sleep(0)  # Yield to event loop
        await asyncio.wait_for(self._wait_for_queue_empty(), timeout=timeout)

    async def _wait_for_queue_empty(self) -> None:
        """Wait until all queues are empty."""
        while any(not q.empty() for q in self._queues.values()):
            await asyncio.sleep(0.01)

    async def _dispatcher_loop(self) -> None:
        """
        Background task that dispatches events to subscribers.

        Processes queues in priority order:
        CRITICAL → HIGH → NORMAL → LOW
        """
        priority_order = [
            EventPriority.CRITICAL,
            EventPriority.HIGH,
            EventPriority.NORMAL,
            EventPriority.LOW,
        ]

        while self._running:
            dispatched = False

            for priority in priority_order:
                queue = self._queues[priority]
                try:
                    event = queue.get_nowait()
                    await self._dispatch_event(event)
                    queue.task_done()
                    self._event_count += 1
                    dispatched = True
                    break  # Restart from CRITICAL after each dispatch
                except asyncio.QueueEmpty:
                    continue

            if not dispatched:
                # No events — yield briefly to avoid spinning
                await asyncio.sleep(0.001)

    async def _drain_all_queues(self) -> None:
        """Drain all remaining events during shutdown."""
        priority_order = [
            EventPriority.CRITICAL,
            EventPriority.HIGH,
            EventPriority.NORMAL,
            EventPriority.LOW,
        ]
        while any(not q.empty() for q in self._queues.values()):
            for priority in priority_order:
                queue = self._queues[priority]
                if not queue.empty():
                    try:
                        event = queue.get_nowait()
                        await self._dispatch_event(event)
                        queue.task_done()
                    except asyncio.QueueEmpty:
                        pass
            await asyncio.sleep(0)

    async def _dispatch_event(self, event: Event) -> None:
        """
        Find all matching subscribers and invoke their handlers.

        CRITICAL: Subscriber exceptions are CAUGHT here.
        A bad subscriber must NEVER bring down the event bus or the runtime.
        """
        matching = [s for s in self._subscriptions if s.matches(event.event_type)]

        if not matching:
            return

        # Dispatch to all matching subscribers concurrently
        tasks = [
            self._call_handler_safely(sub, event)
            for sub in matching
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _call_handler_safely(self, sub: Subscription, event: Event) -> None:
        """
        Call a subscriber handler with full exception isolation.
        Errors in handlers are logged but NEVER propagated.
        """
        try:
            await sub.handler(event)
        except Exception as exc:
            self._error_count += 1
            logger.error(
                "EventBus handler raised exception — isolated, not propagated",
                subscriber=sub.subscriber_name,
                pattern=sub.pattern,
                event_type=event.event_type,
                event_id=event.event_id,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )

    def get_stats(self) -> dict[str, Any]:
        """Return event bus statistics."""
        return {
            "running": self._running,
            "total_events_processed": self._event_count,
            "total_errors": self._error_count,
            "subscription_count": len(self._subscriptions),
            "queue_sizes": {
                p.name: self._queues[p].qsize() for p in EventPriority
            },
            "events_by_type": dict(self._stats),
        }


# Module-level singleton instance
# Initialized in application startup, not at import time
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global EventBus singleton. Must be initialized first."""
    global _bus
    if _bus is None:
        raise RuntimeError(
            "EventBus not initialized. Call initialize_event_bus() at startup."
        )
    return _bus


def initialize_event_bus(queue_size: int = 1000) -> EventBus:
    """Create and return the global EventBus singleton."""
    global _bus
    _bus = EventBus(queue_size=queue_size)
    return _bus
