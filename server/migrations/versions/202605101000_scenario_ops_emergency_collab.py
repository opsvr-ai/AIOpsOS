"""add scenario ops and emergency collaboration tables

Revision ID: 202605101000
Revises: 202605091000
Create Date: 2026-05-10 10:00:00.000000

Task 1.6 of the Scenario Ops & Emergency Collaboration spec.

This migration adds support for enhanced scenario operations and emergency
collaboration workflows. It includes:

1. New columns on `scenarios` table:
   - scenario_type: command | natural_language | hybrid
   - nl_prompt: Natural language prompt for NL/hybrid scenarios
   - template_id: Reference to scenario template
   - execution_timeout: Timeout in seconds (default 300)
   - enable_collaboration: Whether to enable emergency collaboration
   - collaboration_config: JSONB config for collaboration settings

2. New association tables:
   - scenario_knowledge_docs: Links scenarios to knowledge documents
   - scenario_channels: Links scenarios to notification channels

3. New table: scenario_executions
   - Tracks individual scenario execution instances

4. New table: collaboration_sessions
   - Manages emergency collaboration session lifecycle

5. New table: collaboration_messages
   - Records messages exchanged during collaboration sessions

6. New table: collaboration_recommendations
   - Stores AI-generated recommendations for collaboration sessions

7. New columns on `scene_triggers` table:
   - description: Text description of the trigger
   - last_triggered_at: Timestamp of last trigger
   - trigger_count: Number of times triggered

Requirements: 1.1-1.6, 3.1-3.8, 4.1-4.7, 5.1-5.11, 6.1-6.8
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "202605101000"
down_revision: str | None = "202605091000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add new columns to scenarios table
    # ------------------------------------------------------------------
    op.add_column(
        "scenarios",
        sa.Column(
            "scenario_type",
            sa.String(length=32),
            nullable=False,
            server_default="command",
        ),
    )
    op.add_column(
        "scenarios",
        sa.Column("nl_prompt", sa.Text(), nullable=True),
    )
    op.add_column(
        "scenarios",
        sa.Column("template_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "scenarios",
        sa.Column(
            "execution_timeout",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
    )
    op.add_column(
        "scenarios",
        sa.Column(
            "enable_collaboration",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "scenarios",
        sa.Column(
            "collaboration_config",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
    )

    # Add check constraint for scenario_type
    op.create_check_constraint(
        "ck_scenarios_scenario_type",
        "scenarios",
        "scenario_type IN ('command', 'natural_language', 'hybrid')",
    )

    # Add index for scenario_type queries
    op.create_index(
        "idx_scenarios_scenario_type",
        "scenarios",
        ["scenario_type"],
    )

    # Add index for template_id queries
    op.create_index(
        "idx_scenarios_template_id",
        "scenarios",
        ["template_id"],
        postgresql_where=sa.text("template_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 2. Add new columns to scene_triggers table
    # ------------------------------------------------------------------
    op.add_column(
        "scene_triggers",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "scene_triggers",
        sa.Column(
            "last_triggered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "scene_triggers",
        sa.Column(
            "trigger_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # ------------------------------------------------------------------
    # 3. Create scenario_knowledge_docs association table
    # ------------------------------------------------------------------
    op.create_table(
        "scenario_knowledge_docs",
        sa.Column(
            "scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # ------------------------------------------------------------------
    # 4. Create scenario_channels association table
    # ------------------------------------------------------------------
    op.create_table(
        "scenario_channels",
        sa.Column(
            "scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # ------------------------------------------------------------------
    # 5. Create collaboration_sessions table
    # ------------------------------------------------------------------
    op.create_table(
        "collaboration_sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="created",
        ),
        sa.Column("trigger_reason", sa.Text(), nullable=True),
        sa.Column("group_chat_id", sa.String(length=64), nullable=True),
        sa.Column("group_chat_name", sa.String(length=256), nullable=True),
        sa.Column(
            "progress_summary",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "config_snapshot",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary_report", JSONB(), nullable=True),
        sa.Column(
            "space_id",
            UUID(as_uuid=True),
            sa.ForeignKey("spaces.id", ondelete="SET NULL"),
            nullable=True,
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

    # Add check constraint for collaboration_sessions status
    op.create_check_constraint(
        "ck_collaboration_sessions_status",
        "collaboration_sessions",
        "status IN ('created', 'active', 'resolved', 'closed')",
    )

    # Add indexes for collaboration_sessions
    op.create_index(
        "idx_collab_sessions_scenario_id",
        "collaboration_sessions",
        ["scenario_id"],
    )
    op.create_index(
        "idx_collab_sessions_status",
        "collaboration_sessions",
        ["status", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_collab_sessions_space_id",
        "collaboration_sessions",
        ["space_id"],
        postgresql_where=sa.text("space_id IS NOT NULL"),
    )
    op.create_index(
        "idx_collab_sessions_group_chat_id",
        "collaboration_sessions",
        ["group_chat_id"],
        postgresql_where=sa.text("group_chat_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 6. Create scenario_executions table
    # ------------------------------------------------------------------
    op.create_table(
        "scenario_executions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("trigger_source", sa.String(length=256), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "params",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "result",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "logs",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "collaboration_session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("collaboration_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "space_id",
            UUID(as_uuid=True),
            sa.ForeignKey("spaces.id", ondelete="SET NULL"),
            nullable=True,
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

    # Add check constraints for scenario_executions
    op.create_check_constraint(
        "ck_scenario_executions_trigger_type",
        "scenario_executions",
        "trigger_type IN ('manual', 'schedule', 'trigger_rule')",
    )
    op.create_check_constraint(
        "ck_scenario_executions_status",
        "scenario_executions",
        "status IN ('pending', 'running', 'completed', 'failed', 'timeout')",
    )

    # Add indexes for scenario_executions
    op.create_index(
        "idx_scenario_executions_scenario_id",
        "scenario_executions",
        ["scenario_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_scenario_executions_status",
        "scenario_executions",
        ["status", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_scenario_executions_collab_session",
        "scenario_executions",
        ["collaboration_session_id"],
        postgresql_where=sa.text("collaboration_session_id IS NOT NULL"),
    )
    op.create_index(
        "idx_scenario_executions_space_id",
        "scenario_executions",
        ["space_id"],
        postgresql_where=sa.text("space_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 7. Create collaboration_messages table
    # ------------------------------------------------------------------
    op.create_table(
        "collaboration_messages",
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
        ),
        sa.Column("source_channel", sa.String(length=32), nullable=False),
        sa.Column("source_message_id", sa.String(length=128), nullable=True),
        sa.Column("sender_id", sa.String(length=128), nullable=True),
        sa.Column("sender_name", sa.String(length=256), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "message_type",
            sa.String(length=32),
            nullable=False,
            server_default="text",
        ),
        sa.Column(
            "metadata",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "synced_to",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Add check constraints for collaboration_messages
    op.create_check_constraint(
        "ck_collaboration_messages_source_channel",
        "collaboration_messages",
        "source_channel IN ('wecom', 'email', 'system', 'api')",
    )
    op.create_check_constraint(
        "ck_collaboration_messages_message_type",
        "collaboration_messages",
        "message_type IN ('text', 'markdown', 'event')",
    )

    # Add indexes for collaboration_messages
    op.create_index(
        "idx_collab_messages_session_id",
        "collaboration_messages",
        ["session_id", sa.text("created_at ASC")],
    )
    op.create_index(
        "idx_collab_messages_source_msg_id",
        "collaboration_messages",
        ["source_channel", "source_message_id"],
        postgresql_where=sa.text("source_message_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 8. Create collaboration_recommendations table
    # ------------------------------------------------------------------
    op.create_table(
        "collaboration_recommendations",
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
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("estimated_impact", sa.String(length=256), nullable=True),
        sa.Column(
            "reference_docs",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True),
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

    # Add check constraints for collaboration_recommendations
    op.create_check_constraint(
        "ck_collaboration_recommendations_priority",
        "collaboration_recommendations",
        "priority >= 0 AND priority <= 2",
    )
    op.create_check_constraint(
        "ck_collaboration_recommendations_status",
        "collaboration_recommendations",
        "status IN ('pending', 'adopted', 'ignored', 'modified')",
    )

    # Add indexes for collaboration_recommendations
    op.create_index(
        "idx_collab_recommendations_session_id",
        "collaboration_recommendations",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_collab_recommendations_status",
        "collaboration_recommendations",
        ["status", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    # Reverse order of upgrade()

    # 8. Drop collaboration_recommendations
    op.drop_index(
        "idx_collab_recommendations_status",
        table_name="collaboration_recommendations",
    )
    op.drop_index(
        "idx_collab_recommendations_session_id",
        table_name="collaboration_recommendations",
    )
    op.drop_constraint(
        "ck_collaboration_recommendations_status",
        "collaboration_recommendations",
        type_="check",
    )
    op.drop_constraint(
        "ck_collaboration_recommendations_priority",
        "collaboration_recommendations",
        type_="check",
    )
    op.drop_table("collaboration_recommendations")

    # 7. Drop collaboration_messages
    op.drop_index(
        "idx_collab_messages_source_msg_id",
        table_name="collaboration_messages",
    )
    op.drop_index(
        "idx_collab_messages_session_id",
        table_name="collaboration_messages",
    )
    op.drop_constraint(
        "ck_collaboration_messages_message_type",
        "collaboration_messages",
        type_="check",
    )
    op.drop_constraint(
        "ck_collaboration_messages_source_channel",
        "collaboration_messages",
        type_="check",
    )
    op.drop_table("collaboration_messages")

    # 6. Drop scenario_executions
    op.drop_index(
        "idx_scenario_executions_space_id",
        table_name="scenario_executions",
    )
    op.drop_index(
        "idx_scenario_executions_collab_session",
        table_name="scenario_executions",
    )
    op.drop_index(
        "idx_scenario_executions_status",
        table_name="scenario_executions",
    )
    op.drop_index(
        "idx_scenario_executions_scenario_id",
        table_name="scenario_executions",
    )
    op.drop_constraint(
        "ck_scenario_executions_status",
        "scenario_executions",
        type_="check",
    )
    op.drop_constraint(
        "ck_scenario_executions_trigger_type",
        "scenario_executions",
        type_="check",
    )
    op.drop_table("scenario_executions")

    # 5. Drop collaboration_sessions
    op.drop_index(
        "idx_collab_sessions_group_chat_id",
        table_name="collaboration_sessions",
    )
    op.drop_index(
        "idx_collab_sessions_space_id",
        table_name="collaboration_sessions",
    )
    op.drop_index(
        "idx_collab_sessions_status",
        table_name="collaboration_sessions",
    )
    op.drop_index(
        "idx_collab_sessions_scenario_id",
        table_name="collaboration_sessions",
    )
    op.drop_constraint(
        "ck_collaboration_sessions_status",
        "collaboration_sessions",
        type_="check",
    )
    op.drop_table("collaboration_sessions")

    # 4. Drop scenario_channels
    op.drop_table("scenario_channels")

    # 3. Drop scenario_knowledge_docs
    op.drop_table("scenario_knowledge_docs")

    # 2. Drop columns from scene_triggers
    op.drop_column("scene_triggers", "trigger_count")
    op.drop_column("scene_triggers", "last_triggered_at")
    op.drop_column("scene_triggers", "description")

    # 1. Drop columns from scenarios
    op.drop_index("idx_scenarios_template_id", table_name="scenarios")
    op.drop_index("idx_scenarios_scenario_type", table_name="scenarios")
    op.drop_constraint("ck_scenarios_scenario_type", "scenarios", type_="check")
    op.drop_column("scenarios", "collaboration_config")
    op.drop_column("scenarios", "enable_collaboration")
    op.drop_column("scenarios", "execution_timeout")
    op.drop_column("scenarios", "template_id")
    op.drop_column("scenarios", "nl_prompt")
    op.drop_column("scenarios", "scenario_type")
