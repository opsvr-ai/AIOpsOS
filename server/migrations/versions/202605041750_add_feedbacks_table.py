"""add feedbacks table

Revision ID: 202605041750
Revises: 202605041800
Create Date: 2026-05-04 17:50:00.000000

Creates the feedbacks table for user feedback submissions (bug reports,
feature requests, etc.). This migration was missing and is required
before the 202605091000 migration that adds the images column.

Note: This migration is inserted into the chain before 202605041800
to ensure proper ordering.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "202605041750"
down_revision: str | None = "0d5bb1cbc6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedbacks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("username", sa.String(256), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="待AI分析"),
        sa.Column("rating", sa.Integer, nullable=True),
        sa.Column("ai_analysis", sa.Text, nullable=True),
        sa.Column("resolved_version", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_feedbacks_user_id", "feedbacks", ["user_id"])
    op.create_index("idx_feedbacks_type", "feedbacks", ["type"])
    op.create_index("idx_feedbacks_status", "feedbacks", ["status"])


def downgrade() -> None:
    op.drop_index("idx_feedbacks_status")
    op.drop_index("idx_feedbacks_type")
    op.drop_index("idx_feedbacks_user_id")
    op.drop_table("feedbacks")
