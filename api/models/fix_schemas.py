"""DARA — Fix and validation Pydantic schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FixResponse(BaseModel):
    id: UUID
    error_id: UUID
    confidence: float | None
    strategy: str | None
    validation_pass: bool | None
    outcome: str
    pr_url: str | None
    lines_changed: int | None
    files_changed: list[str] | None
    created_at: datetime


class FixApprovalRequest(BaseModel):
    reviewer_notes: str | None = Field(None, max_length=2000)
    create_pr: bool = Field(default=True)


class FixRejectionRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


class ValidationStepResult(BaseModel):
    step: str
    passed: bool
    duration_ms: int
    details: dict | None = None


class ValidationReport(BaseModel):
    fix_id: str
    passed: bool
    steps: list[ValidationStepResult]
    total_duration_ms: int
    test_count: int
    test_passed: int
    test_failed: int
    coverage_delta: float | None
    semgrep_findings: list[dict]
