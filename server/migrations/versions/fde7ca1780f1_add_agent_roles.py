"""add_agent_roles

Revision ID: fde7ca1780f1
Revises: c344dc0a7091
Create Date: 2026-04-30 11:08:41.361966

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'fde7ca1780f1'
down_revision: str | None = 'c344dc0a7091'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('viewable_roles', postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column('agents', sa.Column('editable_roles', postgresql.ARRAY(sa.String()), nullable=True))
    op.execute("UPDATE agents SET viewable_roles = '{}' WHERE viewable_roles IS NULL")
    op.execute("UPDATE agents SET editable_roles = '{}' WHERE editable_roles IS NULL")


def downgrade() -> None:
    op.drop_column('agents', 'editable_roles')
    op.drop_column('agents', 'viewable_roles')
