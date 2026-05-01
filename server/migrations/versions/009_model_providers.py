"""Add model_providers table and model_provider_id FK on agents."""

revision = "009_model_providers"
down_revision = "008_tool_search_idx"

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


def upgrade():
    op.create_table(
        "model_providers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("provider_type", sa.String(32), nullable=False),
        sa.Column("api_key", sa.String(512), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("is_default", sa.Boolean(), default=False),
        sa.Column("priority", sa.Integer(), default=0),
        sa.Column("config", JSONB(), default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "agents",
        sa.Column("model_provider_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agents_model_provider",
        "agents", "model_providers",
        ["model_provider_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_agents_model_provider", "agents", type_="foreignkey")
    op.drop_column("agents", "model_provider_id")
    op.drop_table("model_providers")
