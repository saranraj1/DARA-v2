"""
DARA — SQLAlchemy ORM Models
All tables used across the pipeline. Single source of truth for schema.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class Error(Base):
    """Stores every ingested error event."""
    __tablename__ = "errors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    error_class: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text)
    line_number: Mapped[int | None] = mapped_column(Integer)
    column_number: Mapped[int | None] = mapped_column(Integer)
    stack_trace: Mapped[str | None] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(String(100))
    environment: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="production"
    )
    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="medium",
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="pending"
    )
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="direct"
    )  # github_actions | opentelemetry | direct
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    branch: Mapped[str | None] = mapped_column(String(200))
    deploy_id: Mapped[str | None] = mapped_column(String(200))
    trace_id: Mapped[str | None] = mapped_column(String(100))  # OTel trace ID
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    signature: Mapped[str | None] = mapped_column(String(64))  # sha256 for dedup
    auto_fix_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "severity IN ('low','medium','high','critical')",
            name="chk_errors_severity",
        ),
        CheckConstraint(
            "status IN ('pending','analyzing','fixing','validating','fixed','failed','escalated')",
            name="chk_errors_status",
        ),
        CheckConstraint(
            "source IN ('github_actions','opentelemetry','direct')",
            name="chk_errors_source",
        ),
        Index("idx_errors_service", "service"),
        Index("idx_errors_severity", "severity"),
        Index("idx_errors_status", "status"),
        Index("idx_errors_created_at", "created_at"),
        Index("idx_errors_error_class", "error_class"),
        Index("idx_errors_trace_id", "trace_id"),
        Index("idx_errors_signature", "signature"),
    )


class Fix(Base):
    """Stores generated fixes and their validation outcomes."""
    __tablename__ = "fixes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    error_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    patch_content: Mapped[str] = mapped_column(Text, nullable=False)
    files_changed: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    lines_changed: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    strategy: Mapped[str | None] = mapped_column(String(50))
    # strategy: template_based | llm_single_file | llm_multi_file | human_escalation
    validation_pass: Mapped[bool | None] = mapped_column(Boolean)
    test_results: Mapped[dict | None] = mapped_column(JSONB)
    semgrep_results: Mapped[dict | None] = mapped_column(JSONB)
    coverage_delta: Mapped[float | None] = mapped_column(Float)
    pr_url: Mapped[str | None] = mapped_column(Text)
    pr_number: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    # outcome: accepted | rejected | reverted | pending | auto_merged
    reviewer_notes: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    llm_provider_used: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="chk_fixes_confidence",
        ),
        CheckConstraint(
            "outcome IN ('accepted','rejected','reverted','pending','auto_merged')",
            name="chk_fixes_outcome",
        ),
        Index("idx_fixes_error_id", "error_id"),
        Index("idx_fixes_outcome", "outcome"),
        Index("idx_fixes_confidence", "confidence"),
        Index("idx_fixes_created_at", "created_at"),
    )


class PipelineRun(Base):
    """Tracks the state of each full pipeline execution."""
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    error_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="started"
    )
    current_stage: Mapped[str | None] = mapped_column(String(50))
    stages_completed: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    error_message: Mapped[str | None] = mapped_column(Text)
    run_metadata: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_pipeline_runs_error_id", "error_id"),
        Index("idx_pipeline_runs_status", "status"),
    )


class PatternLibrary(Base):
    """Stores learned fix templates from successful fixes (the memory)."""
    __tablename__ = "pattern_library"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    error_class: Mapped[str] = mapped_column(String(100), nullable=False)
    error_signature: Mapped[str | None] = mapped_column(Text)  # regex or hash
    fix_template: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(50))
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    success_rate: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_pattern_library_error_class", "error_class"),
        Index("idx_pattern_library_active", "is_active"),
    )


class AuditLog(Base):
    """Immutable audit trail of all HITL decisions and admin actions."""
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. fix_approved, fix_rejected, pipeline_retriggered, repo_reindexed
    actor: Mapped[str | None] = mapped_column(String(200))
    # slack username, API key fingerprint, or "system"
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(200))
    before_state: Mapped[dict | None] = mapped_column(JSONB)
    after_state: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(50))
    extra_data: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_audit_log_action", "action"),
        Index("idx_audit_log_actor", "actor"),
        Index("idx_audit_log_created_at", "created_at"),
    )
