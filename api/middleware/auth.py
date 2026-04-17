"""
DARA — API Auth Middleware
Simple Bearer token validation for API endpoints.
In production, tokens are validated against HashiCorp Vault.
"""
from __future__ import annotations
import logging
from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import get_settings

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)
settings = get_settings()

BYPASS_PATHS = {"/health", "/health/ready", "/metrics", "/docs", "/redoc", "/openapi.json"}
WEBHOOK_PATHS = {"/api/v1/webhooks/github", "/api/v1/slack/events", "/api/v1/slack/interactions"}


async def verify_token(request: Request) -> None:
    """
    FastAPI dependency for token verification.
    Webhooks are excluded (they use HMAC signing instead).
    Health + metrics endpoints are always public.
    """
    path = request.url.path
    if path in BYPASS_PATHS or path in WEBHOOK_PATHS:
        return

    creds: HTTPAuthorizationCredentials | None = await _bearer(request)  # type: ignore
    if not creds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # In dev mode, accept any non-empty token
    if settings.environment == "development":
        return

    # Production: validate against known tokens (simplified — use Vault in prod)
    if creds.credentials != settings.api_secret_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
