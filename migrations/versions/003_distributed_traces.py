"""
DARA — Migration 003
Adds:
  - distributed_traces  — individual OTel spans ingested from any service
  - service_topology    — live call graph between services (updated on every trace)
  - service_registry    — maps service_name → GitHub repo for cross-service fetching
  - deploy_events       — CI/CD deploy notifications for blame correlation

Revision ID: 003
Revises: 002
Create Date: 2026-04-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── distributed_traces ────────────────────────────────────
    # Stores individual OTel spans. Grouped by trace_id.
    # trace_id links back to errors.trace_id (already in errors table).
    op.create_table(
        "distributed_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("span_id", sa.String(32), nullable=False),
        sa.Column("parent_span_id", sa.String(32), nullable=True),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("operation_name", sa.String(500), nullable=False),
        # HTTP status code, gRPC status code, or "ERROR"/"OK"
        sa.Column("status_code", sa.String(20), nullable=True),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        # OTel span attributes as JSONB: code.filepath, code.function, http.url, etc.
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("events", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_traces_trace_id", "distributed_traces", ["trace_id"])
    op.create_index("idx_traces_service", "distributed_traces", ["service_name"])
    op.create_index("idx_traces_status", "distributed_traces", ["status_code"])
    op.create_index("idx_traces_started_at", "distributed_traces", ["started_at"])
    # Composite: find error spans for a service quickly
    op.create_index(
        "idx_traces_service_error",
        "distributed_traces",
        ["service_name", "status_code"],
    )

    # ── service_topology ──────────────────────────────────────
    # Updated on every trace ingestion. Tracks call patterns between services.
    op.create_table(
        "service_topology",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_service", sa.String(200), nullable=False),
        sa.Column("target_service", sa.String(200), nullable=False),
        sa.Column("call_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("error_count", sa.BigInteger(), server_default="0", nullable=False),
        # Running average in ms
        sa.Column("avg_latency_ms", sa.Float(), nullable=True),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_service", "target_service", name="uq_topology_edge"),
    )
    op.create_index("idx_topology_source", "service_topology", ["source_service"])
    op.create_index("idx_topology_target", "service_topology", ["target_service"])

    # ── service_registry ──────────────────────────────────────
    # Maps service_name → GitHub repo for cross-service code fetching
    op.create_table(
        "service_registry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("repo_full_name", sa.String(500), nullable=False),
        sa.Column("primary_language", sa.String(50), nullable=True),
        sa.Column("default_branch", sa.String(100), server_default="main", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_name", name="uq_service_name"),
    )

    # ── deploy_events ─────────────────────────────────────────
    # CI/CD deploy notifications — used by Blame Attribution Engine
    op.create_table(
        "deploy_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("branch", sa.String(300), nullable=True),
        sa.Column("environment", sa.String(50), server_default="production", nullable=False),
        sa.Column("deployed_by", sa.String(200), nullable=True),
        sa.Column("version_tag", sa.String(200), nullable=True),
        sa.Column(
            "deployed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_deploy_service", "deploy_events", ["service_name"])
    op.create_index("idx_deploy_commit", "deploy_events", ["commit_sha"])
    op.create_index("idx_deploy_deployed_at", "deploy_events", ["deployed_at"])
    # For blame: find latest deploy before a given error timestamp
    op.create_index(
        "idx_deploy_service_time",
        "deploy_events",
        ["service_name", "deployed_at"],
    )


def downgrade() -> None:
    op.drop_table("deploy_events")
    op.drop_table("service_registry")
    op.drop_table("service_topology")
    op.drop_table("distributed_traces")
