"""
Base response models — every API response follows BaseResponse format.
This is the contract between all layers of the system.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, field_validator

DataT = TypeVar("DataT")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ErrorDetail(BaseModel):
    """Structured error information — never swallow exceptions, always report."""

    error_code: str = Field(
        description="Machine-readable error code, e.g. 'FLASH_VERIFY_FAILED'"
    )
    message: str = Field(description="Human-readable error message")
    root_cause: str = Field(description="Technical root cause of the error")
    suggested_fix: str = Field(description="Actionable suggestion to resolve the error")
    retryable: bool = Field(
        default=False, description="Whether the operation can be safely retried"
    )
    tool_output: str | None = Field(
        default=None, description="Raw tool/subprocess output if available"
    )
    context: dict[str, Any] = Field(
        default_factory=dict, description="Additional context key-value pairs"
    )

    model_config = {"frozen": True}


class BaseResponse(BaseModel):
    """
    Universal API response format.

    EVERY endpoint in this platform returns this format.
    Clients can always rely on success, request_id, timestamp, message.
    """

    success: bool = Field(description="Whether the request succeeded")
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique request identifier for tracing",
    )
    timestamp: datetime = Field(
        default_factory=_utcnow, description="UTC timestamp of response"
    )
    message: str = Field(description="Human-readable status message")
    data: dict[str, Any] | None = Field(
        default=None, description="Response payload data"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Non-fatal warnings about the operation"
    )
    errors: list[ErrorDetail] = Field(
        default_factory=list, description="Errors that occurred (if success=False)"
    )

    model_config = {"frozen": False}

    @classmethod
    def ok(
        cls,
        message: str,
        data: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        request_id: str | None = None,
    ) -> "BaseResponse":
        """Factory method for successful responses."""
        kwargs: dict[str, Any] = {
            "success": True,
            "message": message,
            "data": data,
            "warnings": warnings or [],
        }
        if request_id:
            kwargs["request_id"] = request_id
        return cls(**kwargs)

    @classmethod
    def fail(
        cls,
        message: str,
        errors: list[ErrorDetail] | None = None,
        data: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> "BaseResponse":
        """Factory method for error responses."""
        kwargs: dict[str, Any] = {
            "success": False,
            "message": message,
            "errors": errors or [],
            "data": data,
        }
        if request_id:
            kwargs["request_id"] = request_id
        return cls(**kwargs)

    @classmethod
    def from_exception(
        cls,
        exception: Exception,
        error_code: str = "INTERNAL_ERROR",
        request_id: str | None = None,
    ) -> "BaseResponse":
        """Create an error response from an exception."""
        error = ErrorDetail(
            error_code=error_code,
            message=str(exception),
            root_cause=type(exception).__name__,
            suggested_fix="Check logs for more details",
            retryable=False,
        )
        return cls.fail(
            message=f"Operation failed: {str(exception)}",
            errors=[error],
            request_id=request_id,
        )


class PaginatedResponse(BaseModel, Generic[DataT]):
    """Paginated list response for endpoints returning collections."""

    success: bool = True
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=_utcnow)
    message: str
    items: list[DataT]
    total: int = Field(description="Total number of items matching the query")
    page: int = Field(ge=1, description="Current page number (1-indexed)")
    page_size: int = Field(ge=1, le=500, description="Number of items per page")
    has_next: bool
    has_prev: bool

    @field_validator("has_next", mode="before")
    @classmethod
    def compute_has_next(cls, v: bool, info: Any) -> bool:  # noqa: ARG003
        return v

    model_config = {"frozen": False}
