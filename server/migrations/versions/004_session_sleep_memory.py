"""Add sleep_status, memory_status, auto_consolidate, last_active_at to sessions

Revision ID: 004_session_sleep_memory
Revises: 003_memory_scope
Create Date: 2026-04-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004_session_sleep_memory"
down_revision: str | None = "003_memory_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("sleep_status", sa.String(16), nullable=False, server_default="awake"))
    op.add_column("sessions", sa.Column("memory_status", sa.String(16), nullable=False, server_default="unconsolidated"))
    op.add_column("sessions", sa.Column("auto_consolidate", sa.Boolean, nullable=False, server_default="true"))
    op.add_column("sessions", sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("idx_session_sleep_memory", "sessions", ["sleep_status", "memory_status"])


def downgrade() -> None:
    op.drop_index("idx_session_sleep_memory")
    op.drop_column("sessions", "last_active_at")
    op.drop_column("sessions", "auto_consolidate")
    op.drop_column("sessions", "memory_status")
    op.drop_column("sessions", "sleep_status")
