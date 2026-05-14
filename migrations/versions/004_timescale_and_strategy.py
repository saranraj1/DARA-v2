"""
DARA — Migration 004: Phase 3 Reflexive Memory Tables
=======================================================
Adds:
  - strategy_variants   — LLM-generated alternative prompt strategies per error_class
  - ab_test_results     — A/B test outcomes for strategy evaluation
  - anomaly_alerts      — Proactive anomaly detection events from TimescaleDB
  - export_runs         — Fine-tuning data export records (MinIO)

Also:
  - Adds `memory_consolidated` + `memory_consolidated_at` to pipeline_runs
  - Enables TimescaleDB extension (silently skips if unavailable)
  - Converts distributed_traces to a TimescaleDB hypertable on started_at
    (only if timescaledb extension is available)

Revision ID: 004
Revises: 003
Create Date: 2026-04-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── TimescaleDB Extension (optional — silently skips if unavailable) ──
    # MUST use SAVEPOINT: a failed DDL in Postgres poisons the whole transaction;
    # Python try/except alone does NOT reset the connection state.
    bind = op.get_bind()
    try:
        bind.execute(sa.text("SAVEPOINT before_tsdb_ext"))
        bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
        bind.execute(sa.text("RELEASE SAVEPOINT before_tsdb_ext"))
    except Exception:
        bind.execute(sa.text("ROLLBACK TO SAVEPOINT before_tsdb_ext"))

    # ── strategy_variants ─────────────────────────────────────
    # LLM-generated alternative prompt strategies. Each row is a candidate
    # fix generation prompt for a specific error class.
    # Status lifecycle: draft → testing → active | retired
    op.create_table(
        "strategy_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("error_class", sa.String(100), nullable=False),
        sa.Column("variant_name", sa.String(200), nullable=False),
        sa.Column(
            "prompt_template",
            sa.Text(),
            nullable=False,
            comment="Full specialist CoT prompt template for this error class",
        ),
        sa.Column(
            "hypothesis",
            sa.Text(),
            nullable=True,
            comment="Why this variant should outperform the current strategy",
        ),
        sa.Column(
            "status",
            sa.String(20),
            server_default="draft",
            nullable=False,
            comment="draft | testing | active | retired | needs_refresh",
        ),
        sa.Column(
            "acceptance_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "rejection_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "total_uses",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "success_rate",
            sa.Float(),
            server_default="0.0",
            nullable=False,
        ),
        sa.Column(
            "generated_by",
            sa.String(50),
            server_default="llm",
            nullable=False,
            comment="llm | human | seed",
        ),
        sa.Column(
            "superseded_by_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="FK to the variant that replaced this one",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sv_error_class", "strategy_variants", ["error_class"])
    op.create_index("idx_sv_status", "strategy_variants", ["status"])
    op.create_index(
        "idx_sv_error_status",
        "strategy_variants",
        ["error_class", "status"],
    )

    # ── ab_test_results ───────────────────────────────────────
    # Records each A/B test comparison between two strategy variants.
    op.create_table(
        "ab_test_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "variant_a_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Control: typically the currently active variant",
        ),
        sa.Column(
            "variant_b_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Challenger: the new LLM-generated candidate",
        ),
        sa.Column("error_class", sa.String(100), nullable=False),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="FK to errors.id — the test case used",
        ),
        sa.Column(
            "winner",
            sa.String(20),
            nullable=True,
            comment="variant_a | variant_b | draw | inconclusive",
        ),
        sa.Column(
            "variant_a_score",
            sa.Float(),
            nullable=True,
            comment="Acceptance rate / Wilson score lower bound",
        ),
        sa.Column(
            "variant_b_score",
            sa.Float(),
            nullable=True,
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=True,
            comment="Statistical confidence in the winner declaration",
        ),
        sa.Column(
            "sample_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("promoted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "tested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ab_error_class", "ab_test_results", ["error_class"])
    op.create_index("idx_ab_variant_a", "ab_test_results", ["variant_a_id"])
    op.create_index("idx_ab_variant_b", "ab_test_results", ["variant_b_id"])

    # ── anomaly_alerts ────────────────────────────────────────
    # Proactive anomaly detection results from TimescaleDB time-bucket analysis.
    op.create_table(
        "anomaly_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column(
            "alert_type",
            sa.String(50),
            nullable=False,
            comment="latency_spike | error_rate_surge | volume_drop",
        ),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column(
            "current_value",
            sa.Float(),
            nullable=False,
            comment="Observed metric value that triggered the alert",
        ),
        sa.Column(
            "baseline_value",
            sa.Float(),
            nullable=True,
            comment="Historical baseline mean",
        ),
        sa.Column(
            "z_score",
            sa.Float(),
            nullable=True,
            comment="Deviation in standard deviations from baseline",
        ),
        sa.Column(
            "threshold",
            sa.Float(),
            server_default="3.0",
            nullable=False,
            comment="Z-score threshold that was exceeded",
        ),
        sa.Column(
            "severity",
            sa.String(20),
            server_default="warning",
            nullable=False,
            comment="warning | critical",
        ),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "slack_notified",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_anomaly_service", "anomaly_alerts", ["service_name"])
    op.create_index("idx_anomaly_triggered", "anomaly_alerts", ["triggered_at"])
    op.create_index(
        "idx_anomaly_service_time",
        "anomaly_alerts",
        ["service_name", "triggered_at"],
    )

    # ── export_runs ───────────────────────────────────────────
    # Tracks each fine-tuning data export to MinIO.
    op.create_table(
        "export_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "triple_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="Number of (error, root_cause, fix) triples exported",
        ),
        sa.Column(
            "minio_path",
            sa.String(500),
            nullable=True,
            comment="s3://dara-training-data/dara_triples_YYYYMMDD_runid.jsonl",
        ),
        sa.Column(
            "status",
            sa.String(20),
            server_default="pending",
            nullable=False,
            comment="pending | running | completed | failed",
        ),
        sa.Column(
            "quality_filters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Snapshot of filter thresholds used (min_confidence etc.)",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "exported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_export_exported_at", "export_runs", ["exported_at"])
    op.create_index("idx_export_status", "export_runs", ["status"])

    # ── pipeline_runs: memory consolidation columns ───────────
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "memory_consolidated",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "memory_consolidated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_pipeline_memory",
        "pipeline_runs",
        ["memory_consolidated"],
    )

    # ── TimescaleDB hypertable (only if extension available) ──
    # Converts distributed_traces to a time-series hypertable.
    # This dramatically accelerates time_bucket() aggregation queries
    # used by the AnomalyDetector.
    try:
        bind.execute(sa.text("SAVEPOINT before_hypertable"))
        bind.execute(sa.text(
            "SELECT create_hypertable('distributed_traces', 'started_at', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        ))
        bind.execute(sa.text("RELEASE SAVEPOINT before_hypertable"))
    except Exception:
        bind.execute(sa.text("ROLLBACK TO SAVEPOINT before_hypertable"))


def downgrade() -> None:
    # Remove memory consolidation columns
    op.drop_index("idx_pipeline_memory", "pipeline_runs")
    op.drop_column("pipeline_runs", "memory_consolidated_at")
    op.drop_column("pipeline_runs", "memory_consolidated")

    # Drop new tables
    op.drop_table("export_runs")
    op.drop_table("anomaly_alerts")
    op.drop_table("ab_test_results")
    op.drop_table("strategy_variants")
