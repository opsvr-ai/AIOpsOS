"""Add workflow linkage fields: action_type, execute_script, execution_log, linked_ticket_id, linked_session_id

Revision ID: 483a5a6b15c6
Revises: 67c348e05ff2
Create Date: 2026-05-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "483a5a6b15c6"
down_revision: str | None = "67c348e05ff2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflow_contexts", sa.Column("action_type", sa.String(32), server_default="create", nullable=True))
    op.add_column("workflow_contexts", sa.Column("execute_script", sa.Text(), nullable=True))
    op.add_column("workflow_contexts", sa.Column("execution_log", sa.Text(), nullable=True))
    op.add_column(
        "workflow_contexts",
        sa.Column("linked_ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("itsm_tickets.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "workflow_contexts",
        sa.Column("linked_session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_contexts", "linked_session_id")
    op.drop_column("workflow_contexts", "linked_ticket_id")
    op.drop_column("workflow_contexts", "execution_log")
    op.drop_column("workflow_contexts", "execute_script")
    op.drop_column("workflow_contexts", "action_type")
