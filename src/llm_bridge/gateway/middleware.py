"""FastAPI middleware for auth, rate limiting, logging, and error handling."""

from __future__ import annotations

import logging
import time

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from llm_bridge.config import get_config
from llm_bridge.models import ErrorDetail, ErrorResponse
from llm_bridge.providers.base import ProviderError

logger = logging.getLogger(__name__)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validates API key from Authorization header."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        config = get_config()
        api_key = config.server.api_key

        # Skip auth if no API key is configured
        if not api_key:
            return await call_next(request)

        # Skip auth for non-API paths
        path = request.url.path
        if not (path.startswith("/v1/") or path.startswith("/auth/") or path.startswith("/admin/")):
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = ""

        if token != api_key:
            err = ErrorResponse(
                error=ErrorDetail(message="Invalid API key", type="authentication_error")
            )
            return JSONResponse(status_code=401, content=err.model_dump())

        return await call_next(request)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Catches ProviderError and converts to OpenAI-format error responses."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except ProviderError as e:
            err = ErrorResponse(
                error=ErrorDetail(
                    message=e.message,
                    type="provider_error",
                    code=e.provider,
                )
            )
            return JSONResponse(status_code=e.status_code, content=err.model_dump())
        except Exception:
            logger.exception("Unhandled error")
            err = ErrorResponse(
                error=ErrorDetail(message="Internal server error", type="server_error")
            )
            return JSONResponse(status_code=500, content=err.model_dump())


class LoggingMiddleware(BaseHTTPMiddleware):
    """Request/response logging with timing."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        elapsed = (time.monotonic() - start) * 1000

        logger.info(
            "%s %s -> %d (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response
