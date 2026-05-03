"""add_session_source_tracking

Revision ID: a7b1e3c4d5f6
Revises: 8481abf828c9
Create Date: 2026-05-03 08:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7b1e3c4d5f6'
down_revision: str | None = '8481abf828c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("source_platform", sa.String(32), nullable=True))
    op.add_column("sessions", sa.Column("source_chat_id", sa.String(256), nullable=True))
    op.create_index("ix_sessions_source", "sessions", ["source_platform", "source_chat_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_source", table_name="sessions")
    op.drop_column("sessions", "source_chat_id")
    op.drop_column("sessions", "source_platform")
