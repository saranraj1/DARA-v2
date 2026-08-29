"""
DARA — SQLAlchemy ORM Models
All tables used across the pipeline. Single source of truth for schema.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    ARRAY,
    BigInteger,
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
    # Phase 3: tracks whether PatternMemory outcome has been consolidated into Neo4j
    memory_consolidated: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    memory_consolidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sandbox_iterations: Mapped[int] = mapped_column(Integer, server_default="0", default=0, nullable=False)
    security_retries: Mapped[int] = mapped_column(Integer, server_default="0", default=0, nullable=False)
    escalation_trigger: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        CheckConstraint(
            "escalation_trigger IS NULL OR escalation_trigger IN ('confidence_gate', 'blast_radius', 'security_blocked', 'strategy_escalation', 'low_confidence', 'high_regression_risk', 'critical_blast_radius', 'sandbox_failure', 'review_rejected', 'patch_application_failure')",
            name="chk_pipeline_runs_escalation_trigger"
        ),
        Index("idx_pipeline_runs_error_id", "error_id"),
        Index("idx_pipeline_runs_status", "status"),
        Index("idx_pipeline_memory", "memory_consolidated"),
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
    actor: Mapped[str | None] = mapped_column(String(200))
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


# ── Phase 2: Distributed Wedge Models ─────────────────────────


class DistributedTrace(Base):
    """
    Individual OTel span ingested from any instrumented service.
    Spans are grouped by trace_id into a full distributed trace.
    Links back to errors.trace_id when an error is associated.
    """
    __tablename__ = "distributed_traces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    span_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(32))
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    operation_name: Mapped[str] = mapped_column(String(500), nullable=False)
    # "ERROR", "OK", HTTP status code string, or gRPC status
    status_code: Mapped[str | None] = mapped_column(String(20))
    status_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # OTel span attributes: code.filepath, code.function, http.url, db.statement…
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    events: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_traces_trace_id", "trace_id"),
        Index("idx_traces_service", "service_name"),
        Index("idx_traces_status", "status_code"),
        Index("idx_traces_started_at", "started_at"),
        Index("idx_traces_service_error", "service_name", "status_code"),
    )


class ServiceTopology(Base):
    """
    Live call graph edge between two services.
    Updated on every trace ingestion. Used by Fault Propagation Mapper.
    """
    __tablename__ = "service_topology"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_service: Mapped[str] = mapped_column(String(200), nullable=False)
    target_service: Mapped[str] = mapped_column(String(200), nullable=False)
    call_count: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    error_count: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    avg_latency_ms: Mapped[float | None] = mapped_column(Float)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_topology_source", "source_service"),
        Index("idx_topology_target", "target_service"),
        sa.UniqueConstraint("source_service", "target_service", name="uq_topology_edge"),
    )


class ServiceRegistry(Base):
    """
    Maps a service_name → GitHub repo. Used by CrossServiceContextBuilder
    to fetch code from all services implicated in a distributed bug.
    """
    __tablename__ = "service_registry"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    service_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    repo_full_name: Mapped[str] = mapped_column(String(500), nullable=False)
    primary_language: Mapped[str | None] = mapped_column(String(50))
    default_branch: Mapped[str] = mapped_column(String(100), server_default="main", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_service_registry_name", "service_name"),
    )


class DeployEvent(Base):
    """
    CI/CD deployment notification. Used by BlameAttributionEngine to correlate
    git commits with production deploys and pinpoint culprit changes.
    """
    __tablename__ = "deploy_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    branch: Mapped[str | None] = mapped_column(String(300))
    environment: Mapped[str] = mapped_column(String(50), server_default="production", nullable=False)
    deployed_by: Mapped[str | None] = mapped_column(String(200))
    version_tag: Mapped[str | None] = mapped_column(String(200))
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_deploy_service", "service_name"),
        Index("idx_deploy_commit", "commit_sha"),
        Index("idx_deploy_deployed_at", "deployed_at"),
        Index("idx_deploy_service_time", "service_name", "deployed_at"),
    )


# ── Phase 3: Reflexive Memory Models ──────────────────────────


class StrategyVariant(Base):
    """
    A candidate fix generation prompt strategy for a specific error_class.
    Generated by StrategyGenerator (Groq LLM) when StrategyMonitor flags a
    class as failing. Lifecycle: draft → testing → active | retired.
    """
    __tablename__ = "strategy_variants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    error_class: Mapped[str] = mapped_column(String(100), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text)   # Why this should work better
    status: Mapped[str] = mapped_column(
        String(20), server_default="draft", nullable=False
    )  # draft | testing | active | retired | needs_refresh
    acceptance_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    rejection_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    total_uses: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    success_rate: Mapped[float] = mapped_column(Float, server_default="0.0", nullable=False)
    generated_by: Mapped[str] = mapped_column(
        String(50), server_default="llm", nullable=False
    )  # llm | human | seed
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_sv_error_class", "error_class"),
        Index("idx_sv_status", "status"),
        Index("idx_sv_error_status", "error_class", "status"),
    )


class AbTestResult(Base):
    """
    Records an A/B test comparison between two StrategyVariants.
    Used by StrategyEvaluator to auto-promote the better performing variant.
    """
    __tablename__ = "ab_test_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    variant_a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    variant_b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    error_class: Mapped[str] = mapped_column(String(100), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # → errors.id
    winner: Mapped[str | None] = mapped_column(
        String(20)
    )  # variant_a | variant_b | draw | inconclusive
    variant_a_score: Mapped[float | None] = mapped_column(Float)  # Wilson lower bound
    variant_b_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    promoted: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    tested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_ab_error_class", "error_class"),
        Index("idx_ab_variant_a", "variant_a_id"),
        Index("idx_ab_variant_b", "variant_b_id"),
    )


class AnomalyAlert(Base):
    """
    Proactive anomaly detected from TimescaleDB time-bucket analysis.
    AnomalyDetector fires these BEFORE errors appear in the errors table.
    """
    __tablename__ = "anomaly_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    alert_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # latency_spike | error_rate_surge | volume_drop
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    current_value: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_value: Mapped[float | None] = mapped_column(Float)
    z_score: Mapped[float | None] = mapped_column(Float)  # Deviation in σ
    threshold: Mapped[float] = mapped_column(Float, server_default="3.0", nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), server_default="warning", nullable=False
    )  # warning | critical
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    slack_notified: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_anomaly_service", "service_name"),
        Index("idx_anomaly_triggered", "triggered_at"),
        Index("idx_anomaly_service_time", "service_name", "triggered_at"),
    )


class ExportRun(Base):
    """
    Tracks each fine-tuning data export execution.
    One row per FineTuningExporter.export() call.
    """
    __tablename__ = "export_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    triple_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    minio_path: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(20), server_default="pending", nullable=False
    )  # pending | running | completed | failed
    quality_filters: Mapped[dict | None] = mapped_column(JSONB)  # snapshot of thresholds
    error_message: Mapped[str | None] = mapped_column(Text)
    exported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_export_exported_at", "exported_at"),
        Index("idx_export_status", "status"),
    )
