"""Add next_run column to schedules

Revision ID: 006_schedule_next_run
Revises: 005_tool_category_source
Create Date: 2026-04-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "006_schedule_next_run"
down_revision: Union[str, None] = "005_tool_category_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("next_run", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("schedules", "next_run")
