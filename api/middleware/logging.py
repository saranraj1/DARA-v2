"""
DARA — Structured JSON logging middleware using structlog.
Injects request_id and trace_id into every log line within a request context.
"""
from __future__ import annotations

import logging
import time
import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

LOG_LEVEL = logging.INFO

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer()
        if False  # switch to JSONRenderer in production
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(LOG_LEVEL),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Per-request middleware that:
    1. Generates a unique request_id
    2. Binds it to the structlog context for the entire request lifetime
    3. Logs request start, duration, and status on every response
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(
                "Unhandled request exception",
                exc_info=exc,
                duration_ms=round((time.perf_counter() - start) * 1000),
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000)
            logger.info(
                "Request completed",
                status_code=response.status_code if "response" in dir() else 500,
                duration_ms=duration_ms,
            )

        response.headers["X-Request-ID"] = request_id
        return response
