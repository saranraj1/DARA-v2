"""
DARA — FastAPI Application
Main entry point: initializes app, registers all routers,
sets up middleware, and wires up OpenTelemetry + Prometheus.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from api.middleware.logging import LoggingMiddleware
from api.routers import admin, errors, fixes, health, metrics, webhooks
from config.settings import get_settings
from storage.neo4j_client import get_neo4j
from storage.postgres import get_postgres
from storage.qdrant_client import get_qdrant
from storage.redis_client import get_redis

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """
    Initialize all database connections on startup.
    Gracefully close them on shutdown.
    """
    logger.info("Starting DARA API server")

    # Initialize storage clients
    postgres = get_postgres()
    redis = get_redis()
    qdrant = get_qdrant()
    neo4j = get_neo4j()

    # Verify connectivity
    if not await redis.ping():
        logger.warning("Redis not reachable — caching disabled")

    connected = await neo4j.verify_connectivity()
    if connected:
        await neo4j.create_indexes()

    # Initialize Qdrant collections
    try:
        await qdrant.initialize()
    except Exception as e:
        logger.warning("Qdrant initialization warning", extra={"error": str(e)})

    logger.info("All storage clients initialized")
    yield  # Application runs here

    # Cleanup
    await postgres.close()
    await redis.close()
    await qdrant.close()
    await neo4j.close()
    logger.info("All storage clients shut down cleanly")


# ── Build the FastAPI app ────────────────────────────────────

app = FastAPI(
    title="DARA — Distributed Architecture Root-cause Agent",
    description=(
        "Autonomous debugging AI for distributed systems. "
        "Ingests errors, analyzes root causes, generates and validates fixes."
    ),
    version=settings.app_version,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

# ── Middleware (order matters — outermost runs first) ─────────

app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Admin-Token"],
)

# ── Prometheus metrics (HTTP request instrumentation) ────────
# NB: Our metrics router already serves /metrics (Prometheus text format).
# The instrumentator wraps HTTP request/response for per-endpoint latency.
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app)

# ── Routers ───────────────────────────────────────────────────
app.include_router(health.router, tags=["Health"])
app.include_router(errors.router, prefix="/api/v1", tags=["Errors"])
app.include_router(fixes.router, prefix="/api/v1", tags=["Fixes"])
app.include_router(webhooks.router, prefix="/api/v1", tags=["Webhooks"])
app.include_router(metrics.router, prefix="/api/v1", tags=["Metrics"])
app.include_router(admin.router, tags=["Admin"])  # prefix set inside admin.py


# ── Global exception handler ─────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception",
        extra={"path": request.url.path, "error": str(exc)},
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred",
        },
    )
