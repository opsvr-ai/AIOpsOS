"""Add category and source_path columns to tools

Revision ID: 005_tool_category_source
Revises: 004_session_sleep_memory
Create Date: 2026-04-27
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "005_tool_category_source"
down_revision: Union[str, None] = "004_session_sleep_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tools", sa.Column("category", sa.String(128), nullable=True))
    op.add_column("tools", sa.Column("source_path", sa.String(1024), nullable=True))
    op.create_index("idx_tools_category", "tools", ["category"])


def downgrade() -> None:
    op.drop_index("idx_tools_category")
    op.drop_column("tools", "source_path")
    op.drop_column("tools", "category")
