"""Task management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from packages.core.runtime_state import RuntimeState, get_runtime_state
from packages.core.security import RequireAuth
from packages.types.base import BaseResponse
from packages.types.event import Event, EventCategory

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])

# In-memory task store — tasks are created by flash/build operations
# In Phase 4 this will be backed by the Task Scheduler
_tasks: dict[str, dict] = {}


def _get_state() -> RuntimeState:
    return get_runtime_state()


@router.get("", response_model=BaseResponse, dependencies=[RequireAuth])
async def list_tasks() -> BaseResponse:
    """List all tasks (running, queued, and recent history)."""
    return BaseResponse.ok(
        message=f"Found {len(_tasks)} task(s)",
        data={
            "tasks": list(_tasks.values()),
            "total": len(_tasks),
        },
    )


@router.get("/{task_id}", response_model=BaseResponse, dependencies=[RequireAuth])
async def get_task(task_id: str) -> BaseResponse:
    """Get status and details for a specific task."""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "TASK_NOT_FOUND",
                "message": f"Task '{task_id}' not found",
                "root_cause": "Task ID does not exist",
                "suggested_fix": "Check the task ID returned by the flash/build operation",
            },
        )
    return BaseResponse.ok(message="Task found", data=task)


@router.post("/{task_id}/cancel", response_model=BaseResponse, dependencies=[RequireAuth])
async def cancel_task(task_id: str) -> BaseResponse:
    """
    Cancel a running or queued task.
    Cannot cancel tasks that are already completed, failed, or cancelled.
    """
    state = _get_state()
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "TASK_NOT_FOUND",
                "message": f"Task '{task_id}' not found",
                "root_cause": "Task ID does not exist",
                "suggested_fix": "Check the task ID",
            },
        )

    terminal_states = {"completed", "failed", "cancelled"}
    if task.get("state") in terminal_states:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "TASK_ALREADY_TERMINAL",
                "message": f"Task is already in terminal state: {task.get('state')}",
                "root_cause": "Cannot cancel a completed/failed/cancelled task",
                "suggested_fix": "Only running or queued tasks can be cancelled",
            },
        )

    task["state"] = "cancelled"

    await state.event_bus.publish(
        Event.create(
            event_type="task.cancelled",
            category=EventCategory.TASK,
            source="api",
            payload={"task_id": task_id},
        )
    )

    return BaseResponse.ok(
        message=f"Task '{task_id}' cancellation requested",
        data=task,
    )


def register_task(task_data: dict) -> None:
    """Register a task in the in-memory store (called by flash/build endpoints)."""
    _tasks[task_data["id"]] = task_data


def update_task(task_id: str, updates: dict) -> None:
    """Update task data (called by adapter during execution)."""
    if task_id in _tasks:
        _tasks[task_id].update(updates)
