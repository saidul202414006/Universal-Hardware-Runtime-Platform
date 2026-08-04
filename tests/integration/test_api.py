"""
Integration tests for the REST API.

Uses Starlette's TestClient which correctly handles the ASGI lifespan
(startup/shutdown hooks). All tests run with an in-memory SQLite database
— no real hardware or external services required.
"""

from __future__ import annotations

import pytest

from packages.core.api import create_app
from packages.core.config import DatabaseConfig, LoggingConfig, RuntimeConfig, SecurityConfig

TEST_API_KEY = "test-api-key-12345"


def _make_config() -> RuntimeConfig:
    """Build a test configuration."""
    config = RuntimeConfig()
    config.database = DatabaseConfig(url="sqlite+aiosqlite:///:memory:")
    config.logging = LoggingConfig(level="WARNING", format="console", output="console")
    config.security = SecurityConfig(api_key=TEST_API_KEY)
    config.plugins.directory = "plugins"
    return config


def _reset_singletons() -> None:
    """Reset module-level singletons between tests."""
    import packages.core.runtime_state as rs
    import packages.core.event_bus as eb
    import packages.core.database as db_mod
    rs._runtime_state = None
    eb._bus = None
    db_mod._db = None


@pytest.fixture()
def api_client():
    """
    Synchronous Starlette TestClient — triggers full app lifespan.
    This is the recommended approach for FastAPI integration testing.
    """
    from starlette.testclient import TestClient

    _reset_singletons()
    config = _make_config()
    app = create_app(config)

    with TestClient(app, headers={"X-API-Key": TEST_API_KEY}) as client:
        yield client, TEST_API_KEY


# =============================================================================
# Health Check (public endpoint)
# =============================================================================

class TestHealthEndpoint:
    def test_health_returns_200(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/system/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "overall" in data["data"]

    def test_health_has_base_response_fields(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/system/health")
        body = resp.json()
        assert "success" in body
        assert "request_id" in body
        assert "timestamp" in body
        assert "message" in body

    def test_health_components_listed(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/system/health")
        data = resp.json()["data"]
        assert "components" in data
        component_names = [c["component"] for c in data["components"]]
        assert "database" in component_names
        assert "event_bus" in component_names


# =============================================================================
# Authentication
# =============================================================================

class TestAuthentication:
    def test_protected_endpoint_without_key_returns_401(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/system/info", headers={"X-API-Key": ""})
        assert resp.status_code == 401

    def test_wrong_api_key_returns_401(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/system/info", headers={"X-API-Key": "absolutely-wrong"})
        assert resp.status_code == 401

    def test_correct_api_key_accepted(self, api_client) -> None:
        client, key = api_client
        resp = client.get("/api/v1/system/info", headers={"X-API-Key": key})
        assert resp.status_code == 200

    def test_error_response_has_error_code(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/system/info", headers={"X-API-Key": ""})
        detail = resp.json().get("detail", {})
        assert "error_code" in detail


# =============================================================================
# System Endpoints
# =============================================================================

class TestSystemEndpoints:
    def test_system_info_returns_version(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/system/info")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "runtime_version" in data
        assert "uptime_seconds" in data
        assert "device_count" in data
        assert data["device_count"] == 0  # No devices at startup

    def test_diagnostics_checks_all_components(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/system/diagnostics")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "database" in data
        assert "event_bus" in data
        assert "tools" in data
        assert "devices" in data
        assert "plugins" in data

    def test_root_endpoint(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert "name" in body
        assert "version" in body

    def test_docs_accessible(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_openapi_schema_accessible(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        assert "info" in schema


# =============================================================================
# Device Endpoints
# =============================================================================

class TestDeviceEndpoints:
    def test_list_devices_returns_empty_list(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["total"] == 0
        assert data["data"]["devices"] == []

    def test_get_device_not_found(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/devices/nonexistent-device-id")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error_code"] == "DEVICE_NOT_FOUND"

    def test_scan_devices_returns_200(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/devices/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_get_capabilities_device_not_found(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/devices/bad-id/capabilities")
        assert resp.status_code == 404


# =============================================================================
# Task Endpoints
# =============================================================================

class TestTaskEndpoints:
    def test_list_tasks_empty(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "tasks" in data["data"]

    def test_get_task_not_found(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/tasks/nonexistent-task-id")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "TASK_NOT_FOUND"

    def test_cancel_nonexistent_task_404(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/tasks/bad-task-id/cancel")
        assert resp.status_code == 404


# =============================================================================
# Plugin Endpoints
# =============================================================================

class TestPluginEndpoints:
    def test_list_plugins_returns_list(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/plugins")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "plugins" in data["data"]
        assert "total" in data["data"]

    def test_test_dummy_plugin_loaded(self, api_client) -> None:
        """The test_dummy plugin should be loaded automatically."""
        client, _ = api_client
        resp = client.get("/api/v1/plugins")
        data = resp.json()["data"]
        plugin_ids = [p["id"] for p in data["plugins"]]
        assert "com.uhr.test_dummy" in plugin_ids

    def test_get_plugin_detail(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/plugins/com.uhr.test_dummy")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == "com.uhr.test_dummy"
        assert "capabilities" in data

    def test_get_plugin_not_found(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/plugins/com.nonexistent.plugin")
        assert resp.status_code == 404

    def test_disable_and_enable_plugin(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/plugins/com.uhr.test_dummy/disable")
        assert resp.status_code == 200
        assert resp.json()["data"]["state"] == "disabled"

        resp = client.post("/api/v1/plugins/com.uhr.test_dummy/enable")
        assert resp.status_code == 200
        assert resp.json()["data"]["state"] == "enabled"


# =============================================================================
# Flash / Build / Erase Safety Tests
# =============================================================================

class TestOperationSafety:
    def test_flash_rejected_without_confirmation(self, api_client) -> None:
        """confirmed=False must always be rejected."""
        client, _ = api_client
        resp = client.post("/api/v1/flash", json={
            "device_id": "dev-001",
            "firmware_path": "/tmp/firmware.bin",
            "confirmed": False,
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error_code"] == "CONFIRMATION_REQUIRED"

    def test_erase_rejected_without_confirmation(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/erase", json={
            "device_id": "dev-001",
            "confirmed": False,
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error_code"] == "CONFIRMATION_REQUIRED"

    def test_flash_device_not_found(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/flash", json={
            "device_id": "nonexistent-device",
            "firmware_path": "/tmp/firmware.bin",
            "confirmed": True,
        })
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "DEVICE_NOT_FOUND"

    def test_erase_device_not_found(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/erase", json={
            "device_id": "nonexistent-device",
            "confirmed": True,
        })
        assert resp.status_code == 404

    def test_build_nonexistent_path_rejected(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/build", json={
            "project_path": "/this/path/does/not/exist",
            "target_board": "esp32",
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error_code"] == "PROJECT_NOT_FOUND"

    def test_build_existing_path_returns_task_id(self, api_client, tmp_path) -> None:
        """A valid project path returns a task_id immediately."""
        client, _ = api_client
        resp = client.post("/api/v1/build", json={
            "project_path": str(tmp_path),
            "target_board": "esp32",
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "task_id" in data
        assert data["state"] == "queued"


# =============================================================================
# Serial Endpoints
# =============================================================================

class TestSerialEndpoints:
    def test_open_serial_device_not_found(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/serial/open", json={
            "device_id": "nonexistent",
            "baud_rate": 115200,
        })
        assert resp.status_code == 404

    def test_write_serial_not_open_returns_409(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/serial/write", json={
            "device_id": "some-device",
            "data": "AT\r\n",
        })
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "SERIAL_NOT_OPEN"

    def test_close_serial_not_open_is_graceful(self, api_client) -> None:
        client, _ = api_client
        resp = client.post("/api/v1/serial/close", json={"device_id": "some-device"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# =============================================================================
# Response format compliance
# =============================================================================

class TestResponseFormat:
    def test_all_success_responses_have_base_fields(self, api_client) -> None:
        client, _ = api_client
        endpoints = [
            ("GET", "/api/v1/system/health"),
            ("GET", "/api/v1/system/info"),
            ("GET", "/api/v1/devices"),
            ("GET", "/api/v1/tasks"),
            ("GET", "/api/v1/plugins"),
        ]
        required = ["success", "request_id", "timestamp", "message"]
        for method, path in endpoints:
            resp = client.request(method, path)
            body = resp.json()
            for field in required:
                assert field in body, f"Missing '{field}' in response from {method} {path}"

    def test_request_id_injected_in_headers(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/system/info")
        assert "x-request-id" in resp.headers
        assert "x-response-time-ms" in resp.headers

    def test_response_time_header_is_numeric(self, api_client) -> None:
        client, _ = api_client
        resp = client.get("/api/v1/system/info")
        time_ms = float(resp.headers.get("x-response-time-ms", "0"))
        assert time_ms >= 0
