"""add progress_analysis_records table

Revision ID: 202605101200
Revises: 202605101100
Create Date: 2026-05-10 12:00:00.000000

Task 14.3 of the Scenario Ops & Emergency Collaboration spec.

This migration adds the progress_analysis_records table to store the history
of progress analysis results for collaboration sessions.

Requirements:
- 10.5: Support manual trigger of progress analysis
- 10.6: Support configurable automatic analysis interval
- 10.7: Update collaboration session progress status when analysis completes
- 10.8: Store analysis results in collaboration session records
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "202605101200"
down_revision: str | None = "202605101100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Create progress_analysis_records table
    # ------------------------------------------------------------------
    op.create_table(
        "progress_analysis_records",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("collaboration_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("current_phase", sa.String(length=32), nullable=False),
        sa.Column(
            "completed_steps",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "pending_items",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "key_events",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "phase_metrics",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "total_duration_minutes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "message_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "analysis_type",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "trigger_source",
            sa.String(length=32),
            nullable=False,
            server_default="api",
        ),
        sa.Column("raw_llm_output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "analysis_config",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Add check constraint for analysis_type
    op.create_check_constraint(
        "ck_progress_analysis_records_analysis_type",
        "progress_analysis_records",
        "analysis_type IN ('manual', 'automatic', 'force_refresh')",
    )

    # Add check constraint for trigger_source
    op.create_check_constraint(
        "ck_progress_analysis_records_trigger_source",
        "progress_analysis_records",
        "trigger_source IN ('api', 'scheduler', 'system')",
    )

    # Add check constraint for current_phase
    op.create_check_constraint(
        "ck_progress_analysis_records_current_phase",
        "progress_analysis_records",
        "current_phase IN ('created', 'investigation', 'diagnosis', 'resolution', 'verification', 'completed')",
    )

    # Add indexes for efficient queries
    op.create_index(
        "idx_progress_analysis_records_session_created",
        "progress_analysis_records",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_progress_analysis_records_type",
        "progress_analysis_records",
        ["session_id", "analysis_type", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index(
        "idx_progress_analysis_records_type",
        table_name="progress_analysis_records",
    )
    op.drop_index(
        "idx_progress_analysis_records_session_created",
        table_name="progress_analysis_records",
    )

    # Drop check constraints
    op.drop_constraint(
        "ck_progress_analysis_records_current_phase",
        "progress_analysis_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_progress_analysis_records_trigger_source",
        "progress_analysis_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_progress_analysis_records_analysis_type",
        "progress_analysis_records",
        type_="check",
    )

    # Drop table
    op.drop_table("progress_analysis_records")
