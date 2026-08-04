"""
Configuration Manager — hierarchical, type-safe configuration.

Priority order (highest wins):
1. Environment variables (UHR_*)
2. Workspace config file (workspace.yaml)
3. Global runtime config file (configs/runtime.yaml)
4. Built-in defaults

This ensures the platform can be configured without touching any files
(just set env vars), which is critical for Docker/CI deployments.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Runtime version — single source of truth
RUNTIME_VERSION = "2.0.0"
PLUGIN_API_VERSION = "1.0.0"


class ServerConfig(BaseSettings):
    """HTTP server configuration."""

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8765, ge=1, le=65535)
    workers: int = Field(default=1, ge=1)
    reload: bool = Field(default=False)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    model_config = SettingsConfigDict(env_prefix="UHR_SERVER_", extra="ignore")


class SecurityConfig(BaseSettings):
    """Security configuration — API key, rate limiting, dangerous op guards."""

    # API key: generated on first run if not set, printed to console
    api_key: str = Field(default="")
    api_key_header: str = Field(default="X-API-Key")
    rate_limit_per_minute: int = Field(default=120, ge=1)
    rate_limit_burst: int = Field(default=20, ge=1)
    require_confirmation_for: list[str] = Field(
        default_factory=lambda: [
            "flash_firmware",
            "erase_flash",
            "delete_project",
            "reset_config",
            "shutdown_runtime",
        ]
    )

    model_config = SettingsConfigDict(env_prefix="UHR_SECURITY_", extra="ignore")

    @model_validator(mode="after")
    def generate_api_key_if_missing(self) -> SecurityConfig:
        """Auto-generate a secure API key if none is configured."""
        if not self.api_key:
            self.api_key = secrets.token_urlsafe(32)
        return self


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    url: str = Field(
        default="sqlite+aiosqlite:///./data/runtime.db",
        description="Database URL. Use sqlite+aiosqlite for SQLite or postgresql+asyncpg for PostgreSQL",
    )
    echo: bool = Field(default=False, description="Echo SQL statements to logs")
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)

    model_config = SettingsConfigDict(env_prefix="UHR_DATABASE_", extra="ignore")


class DiscoveryConfig(BaseSettings):
    """Device discovery configuration."""

    scan_interval_seconds: float = Field(default=5.0, ge=0.5)
    hotplug_enabled: bool = Field(default=True)
    vid_pid_database: str = Field(default="configs/vid_pid_db.json")

    model_config = SettingsConfigDict(env_prefix="UHR_DISCOVERY_", extra="ignore")


class SchedulerConfig(BaseSettings):
    """Task scheduler configuration."""

    max_concurrent_tasks: int = Field(default=3, ge=1)
    task_timeout_seconds: int = Field(default=300, ge=10)
    max_retry_attempts: int = Field(default=3, ge=0)
    retry_delay_seconds: float = Field(default=5.0, ge=0)
    task_history_retention_days: int = Field(default=30, ge=1)

    model_config = SettingsConfigDict(env_prefix="UHR_SCHEDULER_", extra="ignore")


class PluginsConfig(BaseSettings):
    """Plugin system configuration."""

    directory: str = Field(default="plugins")
    auto_load: bool = Field(default=True)
    disabled: list[str] = Field(default_factory=list)

    model_config = SettingsConfigDict(env_prefix="UHR_PLUGINS_", extra="ignore")


class AdaptersConfig(BaseSettings):
    """Adapter tool path configuration."""

    esptool_path: str = Field(default="")
    arduino_cli_path: str = Field(default="")
    avrdude_path: str = Field(default="")
    openocd_path: str = Field(default="")
    picotool_path: str = Field(default="")

    model_config = SettingsConfigDict(env_prefix="UHR_ADAPTERS_", extra="ignore")


class SerialConfig(BaseSettings):
    """Serial communication defaults."""

    default_baud_rate: int = Field(default=115200)
    timeout_seconds: float = Field(default=2.0, ge=0.1)
    read_buffer_size: int = Field(default=4096, ge=64)
    auto_reconnect: bool = Field(default=True)
    reconnect_delay_seconds: float = Field(default=2.0, ge=0.1)

    model_config = SettingsConfigDict(env_prefix="UHR_SERIAL_", extra="ignore")


class LoggingConfig(BaseSettings):
    """Logging configuration."""

    level: str = Field(default="INFO")
    format: str = Field(default="json")  # json | console
    output: str = Field(default="both")  # console | file | both
    file_path: str = Field(default="logs/runtime.log")
    max_file_size_mb: int = Field(default=50, ge=1)
    backup_count: int = Field(default=5, ge=0)

    model_config = SettingsConfigDict(env_prefix="UHR_LOGGING_", extra="ignore")

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"Invalid log level '{v}'. Must be one of: {valid}")
        return v.upper()

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v not in ("json", "console"):
            raise ValueError(f"Invalid log format '{v}'. Must be 'json' or 'console'")
        return v

    @field_validator("output")
    @classmethod
    def validate_output(cls, v: str) -> str:
        if v not in ("console", "file", "both"):
            raise ValueError(f"Invalid output '{v}'. Must be 'console', 'file', or 'both'")
        return v


class MCPConfig(BaseSettings):
    """MCP Server configuration."""

    enabled: bool = Field(default=True)
    transport: str = Field(default="stdio")  # stdio | sse | streamable-http
    server_name: str = Field(default="universal-hardware-runtime")
    server_version: str = Field(default=RUNTIME_VERSION)

    model_config = SettingsConfigDict(env_prefix="UHR_MCP_", extra="ignore")


class DashboardConfig(BaseSettings):
    """Web Dashboard configuration."""

    enabled: bool = Field(default=True)
    static_dir: str = Field(default="apps/dashboard/out")
    serve_dashboard: bool = Field(default=True)

    model_config = SettingsConfigDict(env_prefix="UHR_DASHBOARD_", extra="ignore")


class RuntimeConfig(BaseSettings):
    """
    Root configuration object.

    Loaded via layered approach:
    1. Defaults (above)
    2. YAML file (configs/runtime.yaml)
    3. Environment variables override everything (UHR_*)
    """

    # Core identity
    version: str = Field(default=RUNTIME_VERSION)
    env: str = Field(default="development")  # development | production

    # Sub-configs (each can also be overridden by env vars with their own prefix)
    server: ServerConfig = Field(default_factory=ServerConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    adapters: AdaptersConfig = Field(default_factory=AdaptersConfig)
    serial: SerialConfig = Field(default_factory=SerialConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)

    model_config = SettingsConfigDict(
        env_prefix="UHR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def _load_yaml_config(config_path: str) -> dict[str, Any]:
    """Load configuration from a YAML file. Returns empty dict if not found."""
    path = Path(config_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(
    config_file: str = "configs/runtime.yaml",
    workspace_config_file: str | None = None,
) -> RuntimeConfig:
    """
    Load and merge the runtime configuration.

    Layer order (last wins):
    1. Built-in defaults
    2. Global config file (configs/runtime.yaml)
    3. Workspace config file (if provided)
    4. Environment variables

    Returns:
        Validated RuntimeConfig instance.
    """
    # Load YAML files
    global_config = _load_yaml_config(config_file)
    workspace_config = _load_yaml_config(workspace_config_file) if workspace_config_file else {}

    # Merge: workspace overrides global
    merged: dict[str, Any] = {}
    merged.update(global_config)
    _deep_merge(merged, workspace_config)

    # Inject merged YAML values into environment (env vars take precedence)
    # We do this by creating a config from the merged dict first, then
    # letting pydantic-settings override with actual env vars
    config = RuntimeConfig(**merged)
    return config


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Deep merge override into base (modifies base in-place)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


@lru_cache(maxsize=1)
def get_config() -> RuntimeConfig:
    """
    Get the cached global runtime configuration.

    This is a singleton — called once at startup, cached forever.
    Use load_config() directly in tests to avoid cache issues.
    """
    return load_config()
