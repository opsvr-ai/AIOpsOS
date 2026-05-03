"""Add next_run column to schedules

Revision ID: 006_schedule_next_run
Revises: 005_tool_category_source
Create Date: 2026-04-28
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006_schedule_next_run"
down_revision: str | None = "005_tool_category_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("next_run", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("schedules", "next_run")
