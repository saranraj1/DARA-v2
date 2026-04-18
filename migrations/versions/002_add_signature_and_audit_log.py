"""
DARA — Migration 002
Adds:
  - errors.signature column (deterministic hash for deduplication)
  - errors.signature index (for fast dedup lookup)
  - audit_log table (HITL approvals, security events, admin actions)

Revision ID: 002
Revises: 001
Create Date: 2026-04-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add signature column to errors ────────────────────────
    # signature = sha256(error_class + message[:200] + service + file_path)
    # Used to deduplicate identical errors arriving from multiple sources
    op.add_column(
        "errors",
        sa.Column("signature", sa.String(64), nullable=True),
    )
    op.create_index(
        "idx_errors_signature", "errors", ["signature"],
        unique=False,   # same signature CAN appear; we filter by status
    )

    # ── audit_log ─────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        # e.g. fix_approved, fix_rejected, pipeline_retriggered, repo_reindexed
        sa.Column("actor", sa.String(200), nullable=True),
        # slack username, API key fingerprint, or "system"
        sa.Column("resource_type", sa.String(50), nullable=True),
        # "fix", "error", "pipeline_run", "pattern"
        sa.Column("resource_id", sa.String(200), nullable=True),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", sa.String(50), nullable=True),
        sa.Column("extra_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_log_action", "audit_log", ["action"])
    op.create_index("idx_audit_log_actor", "audit_log", ["actor"])
    op.create_index("idx_audit_log_resource", "audit_log", ["resource_type", "resource_id"])
    op.create_index("idx_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_index("idx_errors_signature", "errors")
    op.drop_column("errors", "signature")
