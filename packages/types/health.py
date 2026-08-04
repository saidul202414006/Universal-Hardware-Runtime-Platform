"""
Health check models — system and component health status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HealthLevel(str, Enum):
    """Health status severity levels."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ComponentHealth(BaseModel):
    """Health status of a single runtime component."""

    component: str = Field(description="Component name, e.g. 'database', 'event_bus'")
    level: HealthLevel = Field(description="Health level")
    message: str = Field(description="Human-readable status message")
    latency_ms: float | None = Field(
        default=None, description="Measured latency in milliseconds (if applicable)"
    )
    details: dict[str, str | int | float | bool] = Field(
        default_factory=dict, description="Additional component-specific details"
    )
    last_checked: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": False}

    @property
    def is_healthy(self) -> bool:
        return self.level == HealthLevel.HEALTHY


class HealthStatus(BaseModel):
    """
    Aggregate health status of the entire runtime.
    Overall level is the worst level across all components.
    """

    overall: HealthLevel = Field(description="Worst health level across all components")
    runtime_version: str = Field(description="Runtime version string")
    uptime_seconds: float = Field(description="Seconds since runtime started")
    components: list[ComponentHealth] = Field(
        default_factory=list, description="Per-component health details"
    )
    timestamp: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": False}

    @classmethod
    def compute_overall(cls, components: list[ComponentHealth]) -> HealthLevel:
        """Compute overall health from component health levels."""
        if not components:
            return HealthLevel.UNKNOWN
        levels = {c.level for c in components}
        if HealthLevel.UNHEALTHY in levels:
            return HealthLevel.UNHEALTHY
        if HealthLevel.DEGRADED in levels:
            return HealthLevel.DEGRADED
        if HealthLevel.UNKNOWN in levels:
            return HealthLevel.UNKNOWN
        return HealthLevel.HEALTHY
