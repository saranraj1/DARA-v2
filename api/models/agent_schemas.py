"""DARA — Agent pipeline Pydantic schemas."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from validation.security_auditor import SecurityAuditResult


class CodeChunk(BaseModel):
    chunk_id: str
    file_path: str
    function_name: str | None
    class_name: str | None
    content: str
    line_start: int
    line_end: int
    language: str
    service: str | None
    relevance_score: float = 1.0


class ContextBundle(BaseModel):
    error_id: str
    erroring_file: str | None
    erroring_function: str | None
    erroring_code: str | None
    related_functions: list[CodeChunk] = Field(default_factory=list)
    recent_commits: list[dict] = Field(default_factory=list)
    similar_past_bugs: list[dict] = Field(default_factory=list)
    callers: list[dict] = Field(default_factory=list)
    total_tokens: int = 0
    trace_id: str | None = None


class RootCauseResult(BaseModel):
    immediate_cause: str
    root_cause: str
    contributing_factors: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_quality: Literal["high", "medium", "low"]
    files_to_change: list[str]
    suggested_strategy: Literal[
        "template_based", "llm_single_file", "llm_multi_file", "human_escalation"
    ]
    reasoning_trace: str


class PatchFile(BaseModel):
    file_path: str
    unified_diff: str
    lines_changed: int
    change_description: str


class Fix(BaseModel):
    error_id: str
    patches: list[PatchFile]
    total_files_changed: int
    total_lines_changed: int
    fix_explanation: str
    suggested_tests: list[str] = Field(default_factory=list)
    confidence_retained: float
    regression_risk: Literal["low", "medium", "high"]
    strategy: str
    llm_provider: str


class ReviewResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    quality_score: float = Field(..., ge=0.0, le=1.0)
    correctness_passes: bool
    security_passes: bool
    overall_recommendation: Literal["approve", "approve_with_comments", "reject"]
    rejection_reason: str | None
    reviewer_notes: str
    issues: list[str] = Field(default_factory=list)
    # Attached by ReviewerAgent for orchestrator retry logic (optional)
    security_audit: Any | None = Field(default=None, exclude=True)
