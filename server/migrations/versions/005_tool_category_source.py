"""Add category and source_path columns to tools

Revision ID: 005_tool_category_source
Revises: 004_session_sleep_memory
Create Date: 2026-04-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005_tool_category_source"
down_revision: str | None = "004_session_sleep_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tools", sa.Column("category", sa.String(128), nullable=True))
    op.add_column("tools", sa.Column("source_path", sa.String(1024), nullable=True))
    op.create_index("idx_tools_category", "tools", ["category"])


def downgrade() -> None:
    op.drop_index("idx_tools_category")
    op.drop_column("tools", "source_path")
    op.drop_column("tools", "category")
