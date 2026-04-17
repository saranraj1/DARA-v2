"""
DARA — Initial Database Migration
Creates all core tables: errors, fixes, pipeline_runs, pattern_library

Revision ID: 001
Revises: 
Create Date: 2025-04-16
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── errors ────────────────────────────────────────────────
    op.create_table(
        "errors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("error_class", sa.String(100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("column_number", sa.Integer(), nullable=True),
        sa.Column("stack_trace", sa.Text(), nullable=True),
        sa.Column("service", sa.String(100), nullable=True),
        sa.Column("environment", sa.String(50), nullable=False, server_default="production"),
        sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(50), nullable=False, server_default="direct"),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("branch", sa.String(200), nullable=True),
        sa.Column("deploy_id", sa.String(200), nullable=True),
        sa.Column("trace_id", sa.String(100), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("auto_fix_eligible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("severity IN ('low','medium','high','critical')", name="chk_errors_severity"),
        sa.CheckConstraint(
            "status IN ('pending','analyzing','fixing','validating','fixed','failed','escalated')",
            name="chk_errors_status",
        ),
        sa.CheckConstraint("source IN ('github_actions','opentelemetry','direct')", name="chk_errors_source"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_errors_service", "errors", ["service"])
    op.create_index("idx_errors_severity", "errors", ["severity"])
    op.create_index("idx_errors_status", "errors", ["status"])
    op.create_index("idx_errors_created_at", "errors", ["created_at"])
    op.create_index("idx_errors_error_class", "errors", ["error_class"])
    op.create_index("idx_errors_trace_id", "errors", ["trace_id"])

    # ── fixes ─────────────────────────────────────────────────
    op.create_table(
        "fixes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("error_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("patch_content", sa.Text(), nullable=False),
        sa.Column("files_changed", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("lines_changed", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("strategy", sa.String(50), nullable=True),
        sa.Column("validation_pass", sa.Boolean(), nullable=True),
        sa.Column("test_results", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("semgrep_results", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("coverage_delta", sa.Float(), nullable=True),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.String(20), nullable=True),
        sa.Column("llm_provider_used", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="chk_fixes_confidence"),
        sa.CheckConstraint(
            "outcome IN ('accepted','rejected','reverted','pending','auto_merged')",
            name="chk_fixes_outcome",
        ),
        sa.ForeignKeyConstraint(["error_id"], ["errors.id"], name="fk_fixes_error_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_fixes_error_id", "fixes", ["error_id"])
    op.create_index("idx_fixes_outcome", "fixes", ["outcome"])
    op.create_index("idx_fixes_confidence", "fixes", ["confidence"])
    op.create_index("idx_fixes_created_at", "fixes", ["created_at"])

    # ── pipeline_runs ─────────────────────────────────────────
    op.create_table(
        "pipeline_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("error_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="started"),
        sa.Column("current_stage", sa.String(50), nullable=True),
        sa.Column("stages_completed", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["error_id"], ["errors.id"], name="fk_pipeline_runs_error_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pipeline_runs_error_id", "pipeline_runs", ["error_id"])
    op.create_index("idx_pipeline_runs_status", "pipeline_runs", ["status"])

    # ── pattern_library ───────────────────────────────────────
    op.create_table(
        "pattern_library",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("error_class", sa.String(100), nullable=False),
        sa.Column("error_signature", sa.Text(), nullable=True),
        sa.Column("fix_template", sa.Text(), nullable=False),
        sa.Column("language", sa.String(50), nullable=True),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_rate", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pattern_library_error_class", "pattern_library", ["error_class"])
    op.create_index("idx_pattern_library_active", "pattern_library", ["is_active"])


def downgrade() -> None:
    op.drop_table("pattern_library")
    op.drop_table("pipeline_runs")
    op.drop_table("fixes")
    op.drop_table("errors")
