"""DARA — API models for error ingestion and responses."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ErrorIngestRequest(BaseModel):
    """Payload schema for direct API error submission."""
    error_class: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1)
    stack_trace: str | None = None
    file_path: str | None = None
    line_number: int | None = Field(None, ge=1)
    column_number: int | None = Field(None, ge=1)
    service: str | None = None
    environment: Literal["development", "staging", "production"] = "production"
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    commit_sha: str | None = Field(None, min_length=7, max_length=40)
    branch: str | None = None
    trace_id: str | None = None

    @field_validator("commit_sha")
    @classmethod
    def validate_sha(cls, v: str | None) -> str | None:
        if v and not all(c in "0123456789abcdefABCDEF" for c in v):
            raise ValueError("commit_sha must be a valid hex string")
        return v


class NormalizedError(BaseModel):
    """Internal canonical error representation after normalization."""
    id: str
    error_class: str
    message: str
    stack_trace: str | None
    file_path: str | None
    line_number: int | None
    service: str | None
    environment: str
    severity: str
    source: str
    commit_sha: str | None
    branch: str | None
    trace_id: str | None
    auto_fix_eligible: bool
    raw_payload: dict | None
    created_at: datetime


class ErrorResponse(BaseModel):
    """API response for a stored error."""
    id: UUID
    error_class: str
    message: str
    severity: str
    status: str
    service: str | None
    auto_fix_eligible: bool
    created_at: datetime
    resolved_at: datetime | None


class ErrorIngestResponse(BaseModel):
    """Response returned immediately after ingestion."""
    id: str
    status: str = "queued"
    message: str = "Error accepted for analysis"
