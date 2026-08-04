"""
Security middleware — API Key authentication and rate limiting.

Design:
- API Key passed via X-API-Key header (configurable)
- Rate limiting via slowapi (per-IP by default)
- All unauthenticated requests → 401
- All rate-exceeded requests → 429
- Health check endpoint is PUBLIC (no auth required) — for load balancers
- WebSocket endpoints use query-param auth (browsers can't set headers in WS)
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from packages.core.runtime_state import get_runtime_state
from packages.logger import get_logger

logger = get_logger("security")

# Header extractor — reads X-API-Key from request headers
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Public paths that do NOT require authentication
PUBLIC_PATHS = {
    "/health",
    "/api/v1/system/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


async def verify_api_key(
    request: Request,
    api_key: str | None = Security(_api_key_header),
) -> str:
    """
    FastAPI dependency that validates the API key.

    Returns the API key if valid.
    Raises HTTP 401 if missing or invalid.
    Skips check for public paths.
    """
    # Skip auth for public paths
    if request.url.path in PUBLIC_PATHS:
        return ""

    state = get_runtime_state()
    expected_key = state.config.security.api_key

    if not api_key:
        logger.warning(
            "Request missing API key",
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "MISSING_API_KEY",
                "message": "API key is required",
                "root_cause": "No X-API-Key header provided",
                "suggested_fix": "Include 'X-API-Key: <your-key>' in request headers",
            },
        )

    if api_key != expected_key:
        logger.warning(
            "Invalid API key attempt",
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "INVALID_API_KEY",
                "message": "Invalid API key",
                "root_cause": "Provided key does not match configured key",
                "suggested_fix": "Check UHR_API_KEY environment variable",
            },
        )

    return api_key


async def verify_ws_api_key(request: Request, api_key: str | None = None) -> bool:
    """
    WebSocket auth — reads key from query param since browsers can't set WS headers.
    Usage: ws://host/ws/v1/events?api_key=<key>
    """
    state = get_runtime_state()
    expected_key = state.config.security.api_key

    # Get from query param
    key = api_key or request.query_params.get("api_key")

    if not key or key != expected_key:
        return False
    return True


# Shorthand dependency for protected routes
RequireAuth = Depends(verify_api_key)
