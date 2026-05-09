"""add space_id to session_files

Revision ID: fb813315629
Revises: 5eb219b0b7f0
Create Date: 2026-05-04 14:30:00.000000

Idempotency note (deploy fix):
    ``session_files`` is not included in ``012_spaces``'s loop, so this
    migration normally owns the column. Guards still help when the column
    is added out-of-band (manual ALTER TABLE or Base.metadata.create_all
    run before alembic stamp, which the lifespan startup path does).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'fb813315629'
down_revision: str | None = '5eb219b0b7f0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "session_files"
_COLUMN = "space_id"
_FK_NAME = "fk_session_files_space_id"


def _has_column(insp, table: str, column: str) -> bool:
    return column in {c["name"] for c in insp.get_columns(table)}


def _space_fk(insp, table: str) -> dict | None:
    for fk in insp.get_foreign_keys(table):
        if (
            _COLUMN in (fk.get("constrained_columns") or [])
            and fk.get("referred_table") == "spaces"
        ):
            return fk
    return None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _TABLE not in set(insp.get_table_names()):
        return
    if not _has_column(insp, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.UUID(), nullable=True))
    insp = sa.inspect(op.get_bind())
    if _space_fk(insp, _TABLE) is None:
        op.create_foreign_key(
            _FK_NAME, _TABLE, "spaces",
            [_COLUMN], ["id"], ondelete="SET NULL",
        )
    # Backfill space_id from parent session — always safe to re-run.
    op.execute(
        "UPDATE session_files "
        "SET space_id = sessions.space_id "
        "FROM sessions "
        "WHERE session_files.session_id = sessions.id "
        "  AND session_files.space_id IS NULL"
    )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _TABLE not in set(insp.get_table_names()):
        return
    fk = _space_fk(insp, _TABLE)
    if fk is not None:
        op.drop_constraint(fk["name"] or _FK_NAME, _TABLE, type_="foreignkey")
    insp = sa.inspect(op.get_bind())
    if _has_column(insp, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
