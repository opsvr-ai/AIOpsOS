"""Add turn_count and skill_review_due to sessions for periodic skill review."""

revision = "011_session_review_flags"
down_revision = "010_agent_user_prompt"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column("sessions", sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sessions", sa.Column("skill_review_due", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade():
    op.drop_column("sessions", "skill_review_due")
    op.drop_column("sessions", "turn_count")
