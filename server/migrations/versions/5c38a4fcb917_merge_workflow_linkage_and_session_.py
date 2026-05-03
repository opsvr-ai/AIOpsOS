"""merge workflow_linkage and session_source_tracking

Revision ID: 5c38a4fcb917
Revises: 483a5a6b15c6, a7b1e3c4d5f6
Create Date: 2026-05-03 16:41:14.484036

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '5c38a4fcb917'
down_revision: str | None = ('483a5a6b15c6', 'a7b1e3c4d5f6')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
