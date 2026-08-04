"""
Task models — job scheduler types.

Tasks represent long-running operations: flash, build, erase.
The state machine ensures proper lifecycle management.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskType(str, Enum):
    """Types of operations that can be scheduled as tasks."""

    BUILD = "build"
    FLASH = "flash"
    ERASE = "erase"
    VERIFY = "verify"
    READ_FLASH = "read_flash"
    SERIAL_MONITOR = "serial_monitor"
    OTA = "ota"
    DIAGNOSTICS = "diagnostics"
    IDENTIFY = "identify"
    RESET = "reset"


class TaskState(str, Enum):
    """
    Task state machine.

    CREATED → QUEUED → PREPARING → RUNNING → VERIFYING → COMPLETED
                                       ↓
                                   FAILED → (RETRY) → QUEUED
                                       ↓
                                   CANCELLED
    """

    CREATED = "created"
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


# Valid task state transitions
VALID_TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {TaskState.QUEUED, TaskState.CANCELLED},
    TaskState.QUEUED: {TaskState.PREPARING, TaskState.CANCELLED},
    TaskState.PREPARING: {TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.RUNNING: {
        TaskState.VERIFYING,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.VERIFYING: {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.FAILED: {TaskState.RETRYING, TaskState.QUEUED},
    TaskState.RETRYING: {TaskState.QUEUED},
    TaskState.COMPLETED: set(),   # Terminal state
    TaskState.CANCELLED: set(),   # Terminal state
}


class Task(BaseModel):
    """
    Task — represents a long-running hardware operation.

    Operations like flash/build/erase are not instantaneous.
    They are queued, tracked with progress, and can be cancelled.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique task identifier",
    )
    task_type: TaskType = Field(description="What kind of operation this task performs")
    state: TaskState = Field(
        default=TaskState.CREATED, description="Current task state"
    )

    # Association
    device_id: str | None = Field(
        default=None, description="Device this task operates on (if applicable)"
    )
    plugin_id: str | None = Field(
        default=None, description="Plugin that handles this task"
    )
    adapter_id: str | None = Field(
        default=None, description="Adapter that executes this task"
    )

    # Progress tracking (0.0 to 100.0)
    progress: float = Field(
        default=0.0, ge=0.0, le=100.0, description="Task progress percentage"
    )
    progress_message: str = Field(
        default="", description="Human-readable progress description"
    )

    # Input parameters
    params: dict[str, Any] = Field(
        default_factory=dict, description="Task-specific input parameters"
    )

    # Output
    result: dict[str, Any] | None = Field(
        default=None, description="Task result data (populated on completion)"
    )
    error_message: str | None = Field(
        default=None, description="Error message if task failed"
    )
    log_lines: list[str] = Field(
        default_factory=list, description="Captured output lines from the operation"
    )

    # Retry tracking
    retry_count: int = Field(default=0, description="Number of retry attempts made")
    max_retries: int = Field(default=3, description="Maximum retry attempts allowed")

    # Correlation
    correlation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Links this task to the API request that created it",
    )
    created_by: str = Field(
        default="api", description="Who created this task: 'api', 'mcp', 'scheduler'"
    )

    # Timestamps
    created_at: datetime = Field(default_factory=_utcnow)
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"frozen": False}

    def transition_to(self, new_state: TaskState) -> None:
        """Apply a state transition. Raises ValueError for invalid transitions."""
        allowed = VALID_TASK_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid task state transition: {self.state} → {new_state}. "
                f"Allowed from {self.state}: {[s.value for s in allowed]}"
            )
        self.state = new_state
        now = _utcnow()

        # Set timing fields
        if new_state == TaskState.QUEUED:
            self.queued_at = now
        elif new_state == TaskState.RUNNING:
            self.started_at = now
        elif new_state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            self.completed_at = now

    @property
    def is_terminal(self) -> bool:
        """Task has reached a final state and will not change."""
        return self.state in (
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        )

    @property
    def can_retry(self) -> bool:
        """Task failed and has remaining retry attempts."""
        return self.state == TaskState.FAILED and self.retry_count < self.max_retries

    def update_progress(self, progress: float, message: str = "") -> None:
        """Update progress percentage and optional message."""
        self.progress = max(0.0, min(100.0, progress))
        if message:
            self.progress_message = message

    def append_log(self, line: str) -> None:
        """Append a log line to the task output."""
        self.log_lines.append(line)
