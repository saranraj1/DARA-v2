"""DARA — Health check router."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from storage.redis_client import get_redis

router = APIRouter()


@router.get("/health", summary="Health check")
async def health() -> JSONResponse:
    redis_ok = await get_redis().ping()
    return JSONResponse({
        "status": "ok",
        "version": "0.1.0",
        "services": {
            "redis": "ok" if redis_ok else "unreachable",
        },
    })


@router.get("/health/ready", summary="Readiness probe")
async def readiness() -> JSONResponse:
    """Full readiness check — used by Docker/k8s probes."""
    issues = []
    if not await get_redis().ping():
        issues.append("redis")
    return JSONResponse(
        status_code=200 if not issues else 503,
        content={"ready": not issues, "failing": issues},
    )
