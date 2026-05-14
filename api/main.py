"""
DARA — FastAPI Application
Main entry point: initializes app, registers all routers,
sets up middleware, and wires up OpenTelemetry + Prometheus.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from api.middleware.logging import LoggingMiddleware
from api.routers import admin, auth, errors, fixes, health, metrics, traces, webhooks, events
from api.routes.webhook_github import router as github_webhook_router
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
    Heavy init (Neo4j indexes, Qdrant) runs in a background task
    so the server starts accepting requests immediately.
    Gracefully close connections on shutdown.
    """
    import asyncio

    logger.info("Starting DARA API server")

    # Initialize storage clients (fast — just builds client objects)
    postgres = get_postgres()
    redis = get_redis()
    qdrant = get_qdrant()
    neo4j = get_neo4j()

    # Fast check: Redis ping (non-blocking, short timeout)
    try:
        if not await asyncio.wait_for(redis.ping(), timeout=3):
            logger.warning("Redis not reachable — caching disabled")
    except Exception:
        logger.warning("Redis ping timed out — caching disabled")

    # Defer slow init (Neo4j index creation, Qdrant collection setup)
    # to a background task so the server yields and becomes ready NOW.
    async def _background_init() -> None:
        try:
            connected = await neo4j.verify_connectivity()
            if connected:
                await neo4j.create_indexes()
                logger.info("Neo4j indexes ready")
        except Exception as exc:
            logger.warning("Neo4j init warning: %s", exc)
        try:
            await qdrant.initialize()
            logger.info("Qdrant collections ready")
        except Exception as exc:
            logger.warning("Qdrant initialization warning: %s", exc)
        logger.info("Background DB init complete")

    asyncio.create_task(_background_init())

    logger.info("DARA API server ready — background DB init in progress")
    yield  # Application runs here (accepts requests immediately)

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
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Admin-Token", "X-Org-Id"],
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
app.include_router(auth.router, tags=["Auth"])   # Phase 4 — public
app.include_router(errors.router, prefix="/api/v1", tags=["Errors"])
app.include_router(fixes.router, prefix="/api/v1", tags=["Fixes"])
app.include_router(webhooks.router, prefix="/api/v1", tags=["Webhooks"])
app.include_router(metrics.router, prefix="/api/v1", tags=["Metrics"])
app.include_router(traces.router, prefix="/api/v1", tags=["Traces"])
app.include_router(admin.router, tags=["Admin"])  # prefix set inside admin.py
app.include_router(events.router, prefix="/api/v1", tags=["Events"])
# RLHF feedback webhook (GitHub PR lifecycle events)
app.include_router(github_webhook_router, tags=["RLHF Feedback"])



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
