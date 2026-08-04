"""
Build and Flash endpoints.

CRITICAL SAFETY RULES enforced here:
1. Flash and Erase operations REQUIRE explicit confirmation field in request body
2. Flash operations REQUIRE a device to be in READY state
3. Flash operations LOCK the device for the duration
4. All destructive operations return a task_id immediately (async)
5. Raw firmware paths are validated before accepting

These endpoints return task_id immediately. Clients poll /api/v1/tasks/{task_id}
or subscribe to /ws/v1/tasks/{task_id} for real-time progress.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from packages.core.runtime_state import RuntimeState, get_runtime_state
from packages.core.security import RequireAuth
from packages.types.base import BaseResponse
from packages.types.device import DeviceState
from packages.types.task import Task, TaskType

router = APIRouter(prefix="/api/v1", tags=["Build & Flash"])


def _get_state() -> RuntimeState:
    return get_runtime_state()


# =============================================================================
# Request models
# =============================================================================


class BuildRequest(BaseModel):
    """Request body for firmware build operations."""

    project_path: str = Field(description="Path to firmware project directory")
    target_board: str = Field(
        description="Target board identifier, e.g. 'esp32', 'arduino:avr:uno'"
    )
    device_id: str | None = Field(default=None, description="Target device ID (optional for build)")
    clean_build: bool = Field(default=False, description="Clean before building")
    extra_flags: list[str] = Field(default_factory=list, description="Extra compiler flags")


class FlashRequest(BaseModel):
    """Request body for firmware flash operations — DANGEROUS."""

    device_id: str = Field(description="Target device ID to flash")
    firmware_path: str = Field(description="Path to compiled firmware file (.bin, .hex, .uf2)")
    flash_address: str = Field(default="0x0", description="Flash start address (ESP32: '0x0')")
    verify_after_flash: bool = Field(
        default=True, description="Verify flash contents after writing"
    )
    # SAFETY: explicit confirmation required
    confirmed: bool = Field(
        description="Must be True to proceed. Flash OVERWRITES existing firmware.",
    )


class EraseRequest(BaseModel):
    """Request body for flash erase — EXTREMELY DANGEROUS."""

    device_id: str = Field(description="Target device ID")
    # SAFETY: explicit confirmation required
    confirmed: bool = Field(
        description="Must be True to proceed. Erase PERMANENTLY DELETES all flash contents.",
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/build", response_model=BaseResponse, dependencies=[RequireAuth])
async def build_firmware(req: BuildRequest) -> BaseResponse:
    """
    Start a firmware build operation.

    Returns immediately with a task_id. Monitor progress via:
    - GET /api/v1/tasks/{task_id}
    - WebSocket /ws/v1/tasks/{task_id}

    Build does NOT require device connection — it only compiles code.
    """
    state = _get_state()

    # Validate project path exists
    project = Path(req.project_path)
    if not project.exists():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "PROJECT_NOT_FOUND",
                "message": f"Project path does not exist: {req.project_path}",
                "root_cause": "File system path check failed",
                "suggested_fix": "Verify the project_path is correct and accessible",
            },
        )

    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        task_type=TaskType.BUILD,
        device_id=req.device_id,
        params=req.model_dump(),
        created_by="api",
    )

    # In a real build, we'd wait for the task scheduler to pick it up.
    # The submission to the scheduler is async.
    await state.task_scheduler.submit(task)

    return BaseResponse.ok(
        message="Build task queued successfully",
        data={
            "task_id": task_id,
            "state": "queued",
            "monitor_url": f"/api/v1/tasks/{task_id}",
            "websocket_url": f"/ws/v1/tasks/{task_id}",
        },
    )


@router.post("/flash", response_model=BaseResponse, dependencies=[RequireAuth])
async def flash_firmware(req: FlashRequest) -> BaseResponse:
    """
    Flash firmware to a device. DANGEROUS — overwrites existing firmware.

    REQUIRES confirmed=True in request body.
    Returns immediately with a task_id.
    Device is locked for the duration of flashing.
    """
    state = _get_state()

    # SAFETY CHECK 1: Confirmation required
    if not req.confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "CONFIRMATION_REQUIRED",
                "message": "Flash operation requires explicit confirmation",
                "root_cause": "confirmed=False in request body",
                "suggested_fix": "Set confirmed=True to acknowledge firmware will be overwritten",
            },
        )

    # SAFETY CHECK 2: Device must exist
    device = state.get_device(req.device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "DEVICE_NOT_FOUND",
                "message": f"Device '{req.device_id}' not found",
                "root_cause": "Device not in registry",
                "suggested_fix": "Run a device scan first",
            },
        )

    # SAFETY CHECK 3: Device must be ready
    if not device.is_available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "DEVICE_NOT_AVAILABLE",
                "message": f"Device '{req.device_id}' is not available (state={device.state.value})",
                "root_cause": f"Device is in state '{device.state.value}'",
                "suggested_fix": "Wait for device to become ready or cancel the current operation",
            },
        )

    # SAFETY CHECK 4: Firmware file must exist
    firmware = Path(req.firmware_path)
    if not firmware.exists():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "FIRMWARE_NOT_FOUND",
                "message": f"Firmware file does not exist: {req.firmware_path}",
                "root_cause": "File system path check failed",
                "suggested_fix": "Build the firmware first or check the path",
            },
        )

    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        task_type=TaskType.FLASH,
        device_id=req.device_id,
        params=req.model_dump(),
        created_by="api",
    )

    device.lock(task_id)
    device.transition_to(DeviceState.BUSY)

    await state.task_scheduler.submit(task)

    return BaseResponse.ok(
        message="Flash task queued. Device is now locked.",
        data={
            "task_id": task_id,
            "state": "queued",
            "device_id": req.device_id,
            "monitor_url": f"/api/v1/tasks/{task_id}",
            "websocket_url": f"/ws/v1/tasks/{task_id}",
        },
        warnings=["Device is locked. Do not disconnect during flash."],
    )


@router.post("/erase", response_model=BaseResponse, dependencies=[RequireAuth])
async def erase_flash(req: EraseRequest) -> BaseResponse:
    """
    Erase ALL flash contents on a device. EXTREMELY DANGEROUS — data is unrecoverable.

    REQUIRES confirmed=True in request body.
    """
    state = _get_state()

    # SAFETY CHECK 1: Confirmation required
    if not req.confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "CONFIRMATION_REQUIRED",
                "message": "Erase operation requires explicit confirmation",
                "root_cause": "confirmed=False in request body",
                "suggested_fix": "Set confirmed=True to acknowledge ALL flash contents will be permanently erased",
            },
        )

    # SAFETY CHECK 2: Device must exist and be available
    device = state.get_device(req.device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "DEVICE_NOT_FOUND",
                "message": f"Device '{req.device_id}' not found",
                "root_cause": "Device not in registry",
                "suggested_fix": "Run a device scan first",
            },
        )

    if not device.is_available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "DEVICE_NOT_AVAILABLE",
                "message": f"Device is not available (state={device.state.value})",
                "root_cause": f"Device is in state '{device.state.value}'",
                "suggested_fix": "Wait for device to become ready",
            },
        )

    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        task_type=TaskType.ERASE,
        device_id=req.device_id,
        params=req.model_dump(),
        created_by="api",
    )

    device.lock(task_id)
    device.transition_to(DeviceState.BUSY)

    await state.task_scheduler.submit(task)

    return BaseResponse.ok(
        message="Erase task queued. ALL flash contents will be permanently deleted.",
        data={
            "task_id": task_id,
            "state": "queued",
            "device_id": req.device_id,
            "monitor_url": f"/api/v1/tasks/{task_id}",
        },
        warnings=[
            "DESTRUCTIVE: All flash contents will be permanently erased. This cannot be undone."
        ],
    )
