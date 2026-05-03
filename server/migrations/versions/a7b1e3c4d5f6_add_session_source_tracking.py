"""add_session_source_tracking

Revision ID: a7b1e3c4d5f6
Revises: 8481abf828c9
Create Date: 2026-05-03 08:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a7b1e3c4d5f6'
down_revision: Union[str, None] = '8481abf828c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("source_platform", sa.String(32), nullable=True))
    op.add_column("sessions", sa.Column("source_chat_id", sa.String(256), nullable=True))
    op.create_index("ix_sessions_source", "sessions", ["source_platform", "source_chat_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_source", table_name="sessions")
    op.drop_column("sessions", "source_chat_id")
    op.drop_column("sessions", "source_platform")
