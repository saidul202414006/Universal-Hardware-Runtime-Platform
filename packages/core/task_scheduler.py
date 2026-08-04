"""
Task Scheduler — asynchronous job queue for long-running operations.

Design:
- In-memory async queue for execution
- Backed by Database for persistence
- Worker pool (size configurable) pulls from queue
- Updates task state and emits events at each transition
- Handles task cancellation and retries safely
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from packages.core.database import DatabaseManager, TaskRecord
from packages.core.event_bus import EventBus
from packages.logger import get_logger
from packages.types.event import Event, EventCategory
from packages.types.task import Task, TaskState

logger = get_logger("task_scheduler")

# Type for the actual work function: async def work(task: Task) -> None
WorkFunction = Callable[[Task], Coroutine[Any, Any, None]]


class TaskScheduler:
    """
    Manages background execution of hardware operations (flash, build).
    """

    def __init__(
        self,
        database: DatabaseManager,
        event_bus: EventBus,
        max_concurrent: int = 3,
    ) -> None:
        self._db = database
        self._bus = event_bus
        self._max_concurrent = max_concurrent
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._handlers: dict[str, WorkFunction] = {}

        # In-memory index of live tasks (for fast lookup/updates)
        self._live_tasks: dict[str, Task] = {}

    def register_handler(self, task_type: str, handler: WorkFunction) -> None:
        """Register the function that will execute a specific task type."""
        self._handlers[task_type] = handler
        logger.debug("Task handler registered", task_type=task_type)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        for i in range(self._max_concurrent):
            worker = asyncio.create_task(self._worker_loop(i), name=f"task_worker_{i}")
            self._workers.append(worker)

        logger.info("Task Scheduler started", workers=self._max_concurrent)

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        logger.info("Task Scheduler stopped")

    async def submit(self, task: Task) -> None:
        """Submit a new task to the queue."""
        if task.task_type not in self._handlers:
            raise ValueError(f"No handler registered for task type: {task.task_type}")

        self._live_tasks[task.id] = task
        task.transition_to(TaskState.QUEUED)
        await self._persist_task(task)
        await self._emit_task_event(task)

        await self._queue.put(task)
        logger.info(
            "Task queued",
            task_id=task.id,
            task_type=task.task_type.value,
        )

    def get_task(self, task_id: str) -> Task | None:
        """Get a live task from memory."""
        return self._live_tasks.get(task_id)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a queued or running task."""
        task = self._live_tasks.get(task_id)
        if not task or task.is_terminal:
            return False

        task.transition_to(TaskState.CANCELLED)
        task.error_message = "Cancelled by user"
        await self._persist_task(task)
        await self._emit_task_event(task)
        logger.info("Task cancelled", task_id=task_id)
        return True

    async def _worker_loop(self, worker_id: int) -> None:
        """Background worker pulling from the queue."""
        while self._running:
            try:
                task = await self._queue.get()
            except asyncio.CancelledError:
                break

            if task.state == TaskState.CANCELLED:
                self._queue.task_done()
                continue

            # Execute
            try:
                task.transition_to(TaskState.RUNNING)
                await self._persist_task(task)
                await self._emit_task_event(task)

                handler = self._handlers[task.task_type]
                await handler(task)

                if task.state != TaskState.CANCELLED:
                    task.transition_to(TaskState.COMPLETED)

            except Exception as exc:
                if task.state != TaskState.CANCELLED:
                    task.transition_to(TaskState.FAILED)
                    task.error_message = str(exc)
                    task.append_log(f"ERROR: {exc!s}")
                    logger.error(
                        "Task execution failed",
                        task_id=task.id,
                        error=str(exc),
                        exc_info=True,
                    )

            finally:
                # Final persistence and event
                await self._persist_task(task)
                await self._emit_task_event(task)

                # Cleanup from memory
                self._live_tasks.pop(task.id, None)
                self._queue.task_done()

    async def _persist_task(self, task: Task) -> None:
        """Save task state to database."""
        import json

        try:
            async with self._db.session() as session:
                existing = await session.get(TaskRecord, task.id)
                if existing:
                    existing.state = task.state.value
                    existing.progress = task.progress
                    existing.progress_message = task.progress_message
                    existing.log_lines = json.dumps(task.log_lines)
                    existing.error_message = task.error_message
                    existing.result = json.dumps(task.result) if task.result else None
                    if task.started_at:
                        existing.started_at = task.started_at
                    if task.completed_at:
                        existing.completed_at = task.completed_at
                else:
                    record = TaskRecord(
                        id=task.id,
                        task_type=task.task_type.value,
                        state=task.state.value,
                        device_id=task.device_id,
                        plugin_id=task.plugin_id,
                        adapter_id=task.adapter_id,
                        progress=task.progress,
                        progress_message=task.progress_message,
                        params=json.dumps(task.params),
                        correlation_id=task.correlation_id,
                        created_by=task.created_by,
                        created_at=task.created_at,
                    )
                    session.add(record)
        except Exception as exc:
            logger.error("Failed to persist task", task_id=task.id, error=str(exc))

    async def _emit_task_event(self, task: Task) -> None:
        """Emit task progress to event bus."""
        event_type = f"task.{task.state.value}"
        payload = {
            "task_id": task.id,
            "task_type": task.task_type.value,
            "state": task.state.value,
            "progress": task.progress,
            "message": task.progress_message,
            "device_id": task.device_id,
        }
        if task.log_lines:
            payload["log_line"] = task.log_lines[-1]

        await self._bus.publish(
            Event.create(
                event_type=event_type,
                category=EventCategory.TASK,
                source="task_scheduler",
                payload=payload,
                correlation_id=task.correlation_id,
            )
        )
