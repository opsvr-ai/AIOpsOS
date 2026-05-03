"""Add user_prompt column to agents and agent_versions."""

revision = "010_agent_user_prompt"
down_revision = "009_model_providers"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column("agents", sa.Column("user_prompt", sa.Text(), nullable=True))
    # agent_versions table may not exist in all deployment environments
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    if "agent_versions" in inspector.get_table_names():
        op.add_column("agent_versions", sa.Column("user_prompt", sa.Text(), nullable=True))


def downgrade():
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    if "agent_versions" in inspector.get_table_names():
        op.drop_column("agent_versions", "user_prompt")
    op.drop_column("agents", "user_prompt")
