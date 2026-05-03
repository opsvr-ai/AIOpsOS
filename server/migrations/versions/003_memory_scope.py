"""Add scope, title, tags columns to agent_memories

Revision ID: 003_memory_scope
Revises: 002_knowledge_memory
Create Date: 2026-04-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "003_memory_scope"
down_revision: str | None = "002_knowledge_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_memories", sa.Column("scope", sa.String(16), nullable=False, server_default="personal"))
    op.add_column("agent_memories", sa.Column("title", sa.String(512), nullable=True))
    op.add_column("agent_memories", sa.Column("tags", JSONB, nullable=False, server_default="[]"))
    op.create_index("idx_memory_scope", "agent_memories", ["scope"])
    op.create_index("idx_memory_session", "agent_memories", ["session_id"])


def downgrade() -> None:
    op.drop_index("idx_memory_session")
    op.drop_index("idx_memory_scope")
    op.drop_column("agent_memories", "tags")
    op.drop_column("agent_memories", "title")
    op.drop_column("agent_memories", "scope")
