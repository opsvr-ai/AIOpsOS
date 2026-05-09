"""add_is_builtin_to_agents_and_tools

Revision ID: 716efe2f0c86
Revises: 61432a83b688
Create Date: 2026-05-01 07:36:53.798596

Idempotency note (deploy fix):
    ``src.main._init_database`` calls ``Base.metadata.create_all`` on
    startup *before* ``alembic upgrade head`` (see Dockerfile.server CMD).
    The ORM model for :class:`Agent` / :class:`Tool` already declares
    ``is_builtin`` so ``create_all`` creates the column; then alembic
    would try to add it again. Guards below make the add_column + alter
    pair idempotent.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '716efe2f0c86'
down_revision: str | None = '61432a83b688'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TARGETS = ("agents", "tools")
_COLUMN = "is_builtin"


def _column_spec(insp, table: str, column: str) -> dict | None:
    for c in insp.get_columns(table):
        if c["name"] == column:
            return c
    return None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in _TARGETS:
        if _column_spec(insp, table, _COLUMN) is None:
            op.add_column(
                table,
                sa.Column(
                    _COLUMN, sa.Boolean(),
                    nullable=True, server_default=sa.text("false"),
                ),
            )
        op.execute(sa.text(
            f"UPDATE {table} SET {_COLUMN} = false WHERE {_COLUMN} IS NULL"
        ))
        # Only alter to NOT NULL if not already — some paths leave the
        # column in the "create_all + server_default" state where it is
        # already NOT NULL because the Python default fired.
        insp = sa.inspect(bind)
        spec = _column_spec(insp, table, _COLUMN)
        if spec is not None and spec.get("nullable"):
            op.alter_column(table, _COLUMN, nullable=False, server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in _TARGETS:
        if _column_spec(insp, table, _COLUMN) is not None:
            op.drop_column(table, _COLUMN)
