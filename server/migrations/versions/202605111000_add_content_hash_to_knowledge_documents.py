"""Add content_hash column to knowledge_documents table

Revision ID: 202605111000
Revises: 202605101000_scenario_ops_emergency_collab
Create Date: 2026-05-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605111000"
down_revision: str | None = "202605101200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add content_hash column to knowledge_documents table
    op.add_column(
        "knowledge_documents",
        sa.Column("content_hash", sa.String(64), nullable=True),
    )
    # Add unique constraint on content_hash
    op.create_unique_constraint(
        "uq_knowledge_documents_content_hash",
        "knowledge_documents",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_knowledge_documents_content_hash",
        "knowledge_documents",
        type_="unique",
    )
    op.drop_column("knowledge_documents", "content_hash")
