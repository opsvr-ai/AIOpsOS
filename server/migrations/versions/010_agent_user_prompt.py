"""Add user_prompt column to agents and agent_versions."""

revision = "010_agent_user_prompt"
down_revision = "009_model_providers"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("agents", sa.Column("user_prompt", sa.Text(), nullable=True))
    op.add_column("agent_versions", sa.Column("user_prompt", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("agent_versions", "user_prompt")
    op.drop_column("agents", "user_prompt")
