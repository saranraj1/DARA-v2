"""
DARA — API Auth Middleware (Phase 4 upgrade)
=============================================
Validates Bearer JWT tokens issued by /api/v1/auth/login.
Falls back to legacy X-Admin-Token for Swagger / direct API calls.

Bypass paths (always public):
  /health, /metrics, /docs, /redoc, /openapi.json
  /api/v1/auth/*  (login, refresh, logout — must be public!)
  /api/v1/webhooks/*, /api/v1/slack/* (HMAC-signed)
"""
from __future__ import annotations

import logging

import jwt
from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import get_settings

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)
settings = get_settings()

BYPASS_PATHS = {
    "/health", "/health/ready",
    "/metrics",
    "/docs", "/redoc", "/openapi.json",
}
BYPASS_PREFIXES = (
    "/api/v1/auth/",       # login / refresh / logout — must stay public
    "/api/v1/webhooks/",   # GitHub webhook — HMAC signed
    "/api/v1/slack/",      # Slack events / interactions — HMAC signed
)

ALGORITHM = "HS256"


def _validate_jwt(token: str) -> dict:
    """Decode and return JWT payload, raise 401 on failure."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired — please log in again",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def verify_token(request: Request) -> None:
    """
    FastAPI dependency injected on all protected routes.

    Auth flow (in priority order):
      1. Skip entirely for public / webhook paths.
      2. In development mode — accept any non-empty Bearer token (permissive).
      3. Try to validate as a DARA JWT (issued by /api/v1/auth/login).
      4. Fall back to legacy X-Admin-Token / api_secret_key comparison.
      5. Reject with 401 if none of the above passes.
    """
    path = request.url.path

    # ── 1. Bypass public paths ──────────────────────────────────
    if path in BYPASS_PATHS or any(path.startswith(p) for p in BYPASS_PREFIXES):
        return

    # ── 2. Dev mode — no auth required at all ──────────────────
    if settings.environment == "development":
        return  # open access in dev

    # ── 3. Production — require a valid credential ──────────────
    creds = await _bearer(request)  # type: ignore

    if creds:
        bearer_token = creds.credentials

        # 3a. Try JWT validation first
        try:
            payload = _validate_jwt(bearer_token)
            # Attach user info to request state for downstream use
            request.state.jwt_user = payload.get("sub", "unknown")
            request.state.jwt_org  = payload.get("org", settings.default_org_id)
            request.state.jwt_role = payload.get("role", "admin")
            return
        except HTTPException:
            pass  # not a valid JWT — try legacy token next

        # 3b. Legacy: treat Bearer value as api_secret_key
        expected = getattr(settings, "api_secret_key", None) or "dara-admin-secret"
        if bearer_token == expected:
            return

    # 3c. X-Admin-Token header (Swagger UI / direct curl)
    x_admin = request.headers.get("X-Admin-Token")
    expected_admin = getattr(settings, "admin_api_key", None) or "dara-admin-secret"
    if x_admin and x_admin == expected_admin:
        return

    # ── 4. Nothing matched — reject ────────────────────────────
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid Authorization header — please log in",
        headers={"WWW-Authenticate": "Bearer"},
    )
