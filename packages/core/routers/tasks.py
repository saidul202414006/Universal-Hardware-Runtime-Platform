"""Task management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from packages.core.runtime_state import RuntimeState, get_runtime_state
from packages.core.security import RequireAuth
from packages.types.base import BaseResponse

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])


def _get_state() -> RuntimeState:
    return get_runtime_state()


@router.get("", response_model=BaseResponse, dependencies=[RequireAuth])
async def list_tasks() -> BaseResponse:
    """List all running/queued tasks in memory."""
    state = _get_state()
    tasks = state.task_scheduler._live_tasks
    return BaseResponse.ok(
        message=f"Found {len(tasks)} live task(s)",
        data={
            "tasks": [t.model_dump(mode="json") for t in tasks.values()],
            "total": len(tasks),
        },
    )


@router.get("/{task_id}", response_model=BaseResponse, dependencies=[RequireAuth])
async def get_task(task_id: str) -> BaseResponse:
    """Get status and details for a specific task."""
    state = _get_state()

    # Check live tasks first
    task = state.task_scheduler.get_task(task_id)

    if not task:
        # Check database for historical task
        from packages.core.database import TaskRecord

        async with state.database.session() as session:
            record = await session.get(TaskRecord, task_id)
            if record:
                # Return historical record
                return BaseResponse.ok(
                    message="Task found in history",
                    data={
                        "id": record.id,
                        "task_type": record.task_type,
                        "state": record.state,
                        "progress": record.progress,
                        "progress_message": record.progress_message,
                        "device_id": record.device_id,
                        "created_at": record.created_at.isoformat() if record.created_at else None,
                        "completed_at": record.completed_at.isoformat()
                        if record.completed_at
                        else None,
                    },
                )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "TASK_NOT_FOUND",
                "message": f"Task '{task_id}' not found",
                "root_cause": "Task ID does not exist",
                "suggested_fix": "Check the task ID returned by the flash/build operation",
            },
        )

    return BaseResponse.ok(message="Task found", data=task.model_dump(mode="json"))


@router.post("/{task_id}/cancel", response_model=BaseResponse, dependencies=[RequireAuth])
async def cancel_task(task_id: str) -> BaseResponse:
    """
    Cancel a running or queued task.
    Cannot cancel tasks that are already completed, failed, or cancelled.
    """
    state = _get_state()
    success = await state.task_scheduler.cancel(task_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "TASK_NOT_CANCELABLE",
                "message": f"Task '{task_id}' not found or cannot be cancelled",
                "root_cause": "Task is missing or already terminal",
                "suggested_fix": "Only running or queued live tasks can be cancelled",
            },
        )

    return BaseResponse.ok(
        message=f"Task '{task_id}' cancellation requested",
        data={"task_id": task_id, "state": "cancelled"},
    )
