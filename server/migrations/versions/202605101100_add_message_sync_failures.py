"""add message_sync_failures table

Revision ID: 202605101100
Revises: 202605101000
Create Date: 2026-05-10 11:00:00.000000

Task 12.3 of the Scenario Ops & Emergency Collaboration spec.

This migration adds the message_sync_failures table to track failed message
synchronization attempts and support manual retry operations.

Requirements:
- 9.6: Record sync failure reason and support manual retry
- 9.7: Avoid duplicate sync through message ID deduplication
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "202605101100"
down_revision: str | None = "202605101000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Create message_sync_failures table
    # ------------------------------------------------------------------
    op.create_table(
        "message_sync_failures",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("collaboration_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("collaboration_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_channel", sa.String(length=32), nullable=False),
        sa.Column("error_reason", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "error_details",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "max_retries",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("last_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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

    # Add check constraint for status
    op.create_check_constraint(
        "ck_message_sync_failures_status",
        "message_sync_failures",
        "status IN ('pending', 'retrying', 'resolved', 'abandoned')",
    )

    # Add check constraint for target_channel
    op.create_check_constraint(
        "ck_message_sync_failures_target_channel",
        "message_sync_failures",
        "target_channel IN ('wecom', 'email')",
    )

    # Add indexes for efficient queries
    op.create_index(
        "idx_msg_sync_failures_session_id",
        "message_sync_failures",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_msg_sync_failures_message_id",
        "message_sync_failures",
        ["message_id"],
    )
    op.create_index(
        "idx_msg_sync_failures_status",
        "message_sync_failures",
        ["status", sa.text("created_at DESC")],
    )
    # Index for finding pending failures for a message and channel (deduplication)
    op.create_index(
        "idx_msg_sync_failures_pending_lookup",
        "message_sync_failures",
        ["message_id", "target_channel", "status"],
        postgresql_where=sa.text("status IN ('pending', 'retrying')"),
    )

    # Add unique index on session_id + source_message_id for deduplication
    # This helps with requirement 9.7 - avoid duplicate sync through message ID
    op.create_index(
        "idx_collab_messages_dedup",
        "collaboration_messages",
        ["session_id", "source_channel", "source_message_id"],
        unique=True,
        postgresql_where=sa.text("source_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Drop the deduplication index on collaboration_messages
    op.drop_index(
        "idx_collab_messages_dedup",
        table_name="collaboration_messages",
    )

    # Drop indexes
    op.drop_index(
        "idx_msg_sync_failures_pending_lookup",
        table_name="message_sync_failures",
    )
    op.drop_index(
        "idx_msg_sync_failures_status",
        table_name="message_sync_failures",
    )
    op.drop_index(
        "idx_msg_sync_failures_message_id",
        table_name="message_sync_failures",
    )
    op.drop_index(
        "idx_msg_sync_failures_session_id",
        table_name="message_sync_failures",
    )

    # Drop check constraints
    op.drop_constraint(
        "ck_message_sync_failures_target_channel",
        "message_sync_failures",
        type_="check",
    )
    op.drop_constraint(
        "ck_message_sync_failures_status",
        "message_sync_failures",
        type_="check",
    )

    # Drop table
    op.drop_table("message_sync_failures")
