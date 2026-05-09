"""add wiki_compile_log table

Revision ID: 202605041820
Revises: 202605041810
Create Date: 2026-05-04 18:20:00.000000

Adds the lightweight idempotency-log table that backs the Celery
``wiki.compile`` task (Phase F of the Agent Runtime Optimization &
Evolution spec).

Table ``wiki_compile_log``:
  * raw_path         TEXT PRIMARY KEY
  * raw_sha256       VARCHAR(64) NOT NULL
  * last_compiled_at TIMESTAMPTZ NOT NULL DEFAULT now()
  * wiki_path        TEXT           (nullable — first produced wiki page)
  * created_at       TIMESTAMPTZ NOT NULL DEFAULT now()

Both upgrade() and downgrade() are exactly reversible.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202605041820"
down_revision: str | None = "202605041810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wiki_compile_log",
        sa.Column("raw_path", sa.Text(), primary_key=True),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "last_compiled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("wiki_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("wiki_compile_log")
