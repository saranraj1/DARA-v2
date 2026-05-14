"""
DARA — Auth Router (Phase 4)
============================
POST /api/v1/auth/login   — validate credentials, return JWT
GET  /api/v1/auth/me      — decode token, return user info
POST /api/v1/auth/logout  — client-side (token blacklist not implemented)
POST /api/v1/auth/refresh — issue a fresh token for a valid one
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

from config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])

settings = get_settings()

# ── Helpers ────────────────────────────────────────────────────

ALGORITHM = "HS256"


def _make_token(username: str, org_id: str = "default", role: str = "admin") -> dict[str, Any]:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": username,
        "org": org_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_hours * 3600,
        "username": username,
        "org_id": org_id,
        "role": role,
    }


def _decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


def _extract_bearer(authorization: str | None) -> str:
    """Extract token from 'Bearer <token>' header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1]


# ── Schemas ────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str
    org_id: str = "default"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    username: str
    org_id: str
    role: str


class MeResponse(BaseModel):
    username: str
    org_id: str
    role: str
    exp: int


# ── Endpoints ──────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse, summary="Authenticate and get JWT")
async def login(body: LoginRequest) -> dict:
    """
    Validate admin credentials and return a signed JWT.
    The token must be sent as: Authorization: Bearer <token>
    """
    # Constant-time comparison to prevent timing attacks
    import hmac
    valid_user = hmac.compare_digest(body.username, settings.admin_username)
    valid_pass = hmac.compare_digest(body.password, settings.admin_password)

    if not (valid_user and valid_pass):
        logger.warning("Failed login attempt for username=%s", body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = _make_token(
        username=body.username,
        org_id=body.org_id or settings.default_org_id,
    )
    logger.info("Login success: username=%s org=%s", body.username, body.org_id)
    return token_data


@router.get("/me", response_model=MeResponse, summary="Get current user from token")
async def me(authorization: str | None = Header(None, alias="Authorization")) -> dict:
    """Decode and return the authenticated user's info."""
    token = _extract_bearer(authorization)
    payload = _decode_token(token)
    return {
        "username": payload.get("sub", ""),
        "org_id": payload.get("org", "default"),
        "role": payload.get("role", "admin"),
        "exp": payload.get("exp", 0),
    }


@router.post("/refresh", response_model=TokenResponse, summary="Refresh JWT token")
async def refresh(authorization: str | None = Header(None, alias="Authorization")) -> dict:
    """Issue a fresh token if the current one is still valid."""
    token = _extract_bearer(authorization)
    payload = _decode_token(token)
    return _make_token(
        username=payload.get("sub", ""),
        org_id=payload.get("org", "default"),
        role=payload.get("role", "admin"),
    )


@router.post("/logout", summary="Logout (client-side token discard)")
async def logout() -> dict:
    """
    No server-side blacklist — client must discard the token.
    Returns a success response so the frontend can clear localStorage.
    """
    return {"ok": True, "message": "Token discarded on client"}
