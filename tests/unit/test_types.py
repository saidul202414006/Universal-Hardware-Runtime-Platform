"""
Unit tests for the Shared Types package.
Tests all Pydantic models, state machines, and validation logic.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from packages.types.base import BaseResponse, ErrorDetail, PaginatedResponse
from packages.types.device import (
    Capability,
    Device,
    DeviceInfo,
    DeviceState,
    DeviceType,
    VALID_TRANSITIONS,
)
from packages.types.event import Event, EventCategory, EventPriority, EventType
from packages.types.task import Task, TaskState, TaskType
from packages.types.health import ComponentHealth, HealthLevel, HealthStatus
from packages.types.plugin import AdapterConfig, PluginManifest, PluginState


# =============================================================================
# BaseResponse Tests
# =============================================================================


class TestBaseResponse:
    def test_ok_factory(self) -> None:
        r = BaseResponse.ok("Success", data={"key": "value"})
        assert r.success is True
        assert r.message == "Success"
        assert r.data == {"key": "value"}
        assert r.errors == []
        assert r.request_id  # auto-generated

    def test_fail_factory(self) -> None:
        r = BaseResponse.fail("Something failed")
        assert r.success is False
        assert r.message == "Something failed"
        assert r.data is None

    def test_fail_with_errors(self) -> None:
        error = ErrorDetail(
            error_code="FLASH_FAILED",
            message="Flash failed",
            root_cause="Port not found",
            suggested_fix="Check connection",
            retryable=True,
        )
        r = BaseResponse.fail("Flash operation failed", errors=[error])
        assert r.success is False
        assert len(r.errors) == 1
        assert r.errors[0].error_code == "FLASH_FAILED"
        assert r.errors[0].retryable is True

    def test_from_exception(self) -> None:
        exc = ValueError("bad input")
        r = BaseResponse.from_exception(exc, error_code="INVALID_INPUT")
        assert r.success is False
        assert len(r.errors) == 1
        assert r.errors[0].error_code == "INVALID_INPUT"
        assert r.errors[0].root_cause == "ValueError"

    def test_request_id_unique(self) -> None:
        r1 = BaseResponse.ok("Test 1")
        r2 = BaseResponse.ok("Test 2")
        assert r1.request_id != r2.request_id

    def test_custom_request_id(self) -> None:
        r = BaseResponse.ok("Test", request_id="my-custom-id")
        assert r.request_id == "my-custom-id"

    def test_warnings_field(self) -> None:
        r = BaseResponse.ok("Success", warnings=["Deprecation warning", "Port busy"])
        assert len(r.warnings) == 2
        assert "Deprecation warning" in r.warnings


# =============================================================================
# Device Tests
# =============================================================================


class TestDevice:
    def test_create_device(self) -> None:
        d = Device(
            name="ESP32 Dev Board",
            device_type=DeviceType.ESP32,
            port="/dev/ttyUSB0",
        )
        assert d.name == "ESP32 Dev Board"
        assert d.device_type == DeviceType.ESP32
        assert d.state == DeviceState.DETECTED
        assert d.id  # auto-generated UUID

    def test_valid_state_transitions(self) -> None:
        d = Device(name="Test", device_type=DeviceType.ARDUINO, port="COM3")
        assert d.state == DeviceState.DETECTED
        d.transition_to(DeviceState.IDENTIFYING)
        assert d.state == DeviceState.IDENTIFYING
        d.transition_to(DeviceState.READY)
        assert d.state == DeviceState.READY
        d.transition_to(DeviceState.BUSY)
        assert d.state == DeviceState.BUSY
        d.transition_to(DeviceState.READY)
        assert d.state == DeviceState.READY

    def test_invalid_state_transition_raises(self) -> None:
        d = Device(name="Test", device_type=DeviceType.ESP32, port="/dev/ttyUSB0")
        with pytest.raises(ValueError, match="Invalid device state transition"):
            d.transition_to(DeviceState.BUSY)  # DETECTED → BUSY is invalid

    def test_offline_from_any_state(self) -> None:
        """Any state should be able to go offline."""
        for state in (DeviceState.READY, DeviceState.BUSY, DeviceState.IDENTIFYING):
            d = Device(name="Test", device_type=DeviceType.ESP32, port="/dev/ttyUSB0")
            d.state = state
            d.transition_to(DeviceState.OFFLINE)
            assert d.state == DeviceState.OFFLINE

    def test_device_locking(self) -> None:
        d = Device(name="Test", device_type=DeviceType.ESP32, port="/dev/ttyUSB0")
        d.state = DeviceState.READY
        assert not d.is_locked
        d.lock("task-001")
        assert d.is_locked
        assert d.locked_by_task == "task-001"

    def test_lock_prevents_double_lock(self) -> None:
        d = Device(name="Test", device_type=DeviceType.ESP32, port="/dev/ttyUSB0")
        d.lock("task-001")
        with pytest.raises(RuntimeError, match="already locked"):
            d.lock("task-002")

    def test_unlock_wrong_task_raises(self) -> None:
        d = Device(name="Test", device_type=DeviceType.ESP32, port="/dev/ttyUSB0")
        d.lock("task-001")
        with pytest.raises(RuntimeError):
            d.unlock("task-999")

    def test_unlock_correct_task(self) -> None:
        d = Device(name="Test", device_type=DeviceType.ESP32, port="/dev/ttyUSB0")
        d.lock("task-001")
        d.unlock("task-001")
        assert not d.is_locked
        assert d.locked_by_task is None

    def test_is_available(self) -> None:
        d = Device(name="Test", device_type=DeviceType.ESP32, port="/dev/ttyUSB0")
        d.state = DeviceState.READY
        assert d.is_available
        d.lock("task-001")
        assert not d.is_available

    def test_last_state_change_updated(self) -> None:
        d = Device(name="Test", device_type=DeviceType.ESP32, port="/dev/ttyUSB0")
        before = d.last_state_change
        d.transition_to(DeviceState.IDENTIFYING)
        assert d.last_state_change >= before


# =============================================================================
# Event Tests
# =============================================================================


class TestEvent:
    def test_create_event(self) -> None:
        e = Event.create(
            "device.connected",
            EventCategory.DEVICE,
            "test_component",
            payload={"port": "/dev/ttyUSB0"},
        )
        assert e.event_type == "device.connected"
        assert e.category == EventCategory.DEVICE
        assert e.source == "test_component"
        assert e.payload["port"] == "/dev/ttyUSB0"
        assert e.event_id  # auto-generated

    def test_critical_event_is_persistent(self) -> None:
        e = Event.create(
            EventType.SYSTEM_ERROR,
            EventCategory.SYSTEM,
            "runtime",
            priority=EventPriority.CRITICAL,
        )
        assert e.persistent is True

    def test_normal_event_not_persistent_by_default(self) -> None:
        e = Event.create("device.connected", EventCategory.DEVICE, "test")
        assert e.persistent is False

    def test_correlation_id_propagation(self) -> None:
        e = Event.create(
            "device.connected",
            EventCategory.DEVICE,
            "test",
            correlation_id="my-correlation-id",
        )
        assert e.correlation_id == "my-correlation-id"

    def test_event_is_immutable(self) -> None:
        e = Event.create("device.connected", EventCategory.DEVICE, "test")
        with pytest.raises(Exception):  # frozen=True
            e.event_type = "device.disconnected"  # type: ignore


# =============================================================================
# Task Tests
# =============================================================================


class TestTask:
    def test_create_task(self) -> None:
        t = Task(task_type=TaskType.FLASH, device_id="dev-001")
        assert t.task_type == TaskType.FLASH
        assert t.state == TaskState.CREATED
        assert t.progress == 0.0
        assert t.id  # auto-generated

    def test_task_state_machine(self) -> None:
        t = Task(task_type=TaskType.BUILD)
        t.transition_to(TaskState.QUEUED)
        assert t.queued_at is not None
        t.transition_to(TaskState.PREPARING)
        t.transition_to(TaskState.RUNNING)
        assert t.started_at is not None
        t.transition_to(TaskState.COMPLETED)
        assert t.completed_at is not None

    def test_invalid_task_transition(self) -> None:
        t = Task(task_type=TaskType.FLASH)
        with pytest.raises(ValueError, match="Invalid task state transition"):
            t.transition_to(TaskState.COMPLETED)  # Cannot skip states

    def test_update_progress(self) -> None:
        t = Task(task_type=TaskType.FLASH)
        t.update_progress(50.0, "Writing sector 4/8")
        assert t.progress == 50.0
        assert t.progress_message == "Writing sector 4/8"

    def test_progress_clamped(self) -> None:
        t = Task(task_type=TaskType.FLASH)
        t.update_progress(150.0)  # Should clamp to 100
        assert t.progress == 100.0
        t.update_progress(-50.0)  # Should clamp to 0
        assert t.progress == 0.0

    def test_is_terminal(self) -> None:
        t = Task(task_type=TaskType.FLASH)
        assert not t.is_terminal
        t.state = TaskState.COMPLETED
        assert t.is_terminal
        t.state = TaskState.FAILED
        assert t.is_terminal
        t.state = TaskState.CANCELLED
        assert t.is_terminal

    def test_can_retry(self) -> None:
        t = Task(task_type=TaskType.FLASH, max_retries=3)
        t.state = TaskState.FAILED
        assert t.can_retry
        t.retry_count = 3
        assert not t.can_retry

    def test_append_log(self) -> None:
        t = Task(task_type=TaskType.BUILD)
        t.append_log("Compiling main.c")
        t.append_log("Linking...")
        assert len(t.log_lines) == 2
        assert "Compiling main.c" in t.log_lines


# =============================================================================
# Health Tests
# =============================================================================


class TestHealth:
    def test_compute_overall_healthy(self) -> None:
        components = [
            ComponentHealth(component="db", level=HealthLevel.HEALTHY, message="OK"),
            ComponentHealth(component="bus", level=HealthLevel.HEALTHY, message="OK"),
        ]
        assert HealthStatus.compute_overall(components) == HealthLevel.HEALTHY

    def test_compute_overall_degraded(self) -> None:
        components = [
            ComponentHealth(component="db", level=HealthLevel.HEALTHY, message="OK"),
            ComponentHealth(component="bus", level=HealthLevel.DEGRADED, message="Slow"),
        ]
        assert HealthStatus.compute_overall(components) == HealthLevel.DEGRADED

    def test_compute_overall_unhealthy(self) -> None:
        components = [
            ComponentHealth(component="db", level=HealthLevel.UNHEALTHY, message="Down"),
            ComponentHealth(component="bus", level=HealthLevel.HEALTHY, message="OK"),
        ]
        assert HealthStatus.compute_overall(components) == HealthLevel.UNHEALTHY

    def test_compute_overall_empty(self) -> None:
        assert HealthStatus.compute_overall([]) == HealthLevel.UNKNOWN

    def test_unhealthy_dominates_degraded(self) -> None:
        components = [
            ComponentHealth(component="a", level=HealthLevel.DEGRADED, message="Slow"),
            ComponentHealth(component="b", level=HealthLevel.UNHEALTHY, message="Down"),
        ]
        assert HealthStatus.compute_overall(components) == HealthLevel.UNHEALTHY
