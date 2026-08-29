"""
DARA — Migration 005: Pipeline Run Reliability Metrics
======================================================
Adds:
  - `sandbox_iterations` (INTEGER, default 0, NOT NULL)
  - `security_retries`   (INTEGER, default 0, NOT NULL)
  - `escalation_trigger` (VARCHAR(50), NULL) with CHECK constraint

Revision ID: 005
Revises: 004
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "sandbox_iterations",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="Number of self-healing iterations in sandbox",
        ),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "security_retries",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="Number of security retry attempts",
        ),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "escalation_trigger",
            sa.String(50),
            nullable=True,
            comment="Trigger type: confidence_gate | blast_radius | security_blocked | strategy_escalation",
        ),
    )
    op.create_check_constraint(
        "chk_pipeline_runs_escalation_trigger",
        "pipeline_runs",
        "escalation_trigger IS NULL OR escalation_trigger IN ('confidence_gate', 'blast_radius', 'security_blocked', 'strategy_escalation', 'low_confidence', 'high_regression_risk', 'critical_blast_radius', 'sandbox_failure', 'review_rejected', 'patch_application_failure')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "chk_pipeline_runs_escalation_trigger",
        "pipeline_runs",
        type_="check",
    )
    op.drop_column("pipeline_runs", "escalation_trigger")
    op.drop_column("pipeline_runs", "security_retries")
    op.drop_column("pipeline_runs", "sandbox_iterations")
