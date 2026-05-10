"""add images column to feedbacks table

Revision ID: 202605091000
Revises: 202605041840
Create Date: 2026-05-09 10:00:00.000000

Task 1.1 of the Feedback Image Upload spec.

Adds an ``images`` JSONB column to the ``feedbacks`` table to store
an array of image URLs associated with each feedback submission.
Users can attach up to 5 images when submitting bug reports or
feature requests.

Column:
  * ``images`` JSONB NOT NULL DEFAULT '[]'::jsonb

Index:
  * ``idx_feedbacks_has_images`` - partial index for queries filtering
    feedbacks that have attached images (images != '[]'::jsonb)

Requirements: 5.4
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "202605091000"
down_revision: str | None = "202605041840"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add the images column with JSONB type and default empty array
    op.add_column(
        "feedbacks",
        sa.Column(
            "images",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
    )

    # 2. Create index for queries filtering by has-images
    # This is a partial index that only includes rows where images is not empty
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_feedbacks_has_images
            ON feedbacks ((images != '[]'::jsonb))
            WHERE images != '[]'::jsonb
            """
        )
    )


def downgrade() -> None:
    # Drop the index first
    op.execute(sa.text("DROP INDEX IF EXISTS idx_feedbacks_has_images"))
    # Then drop the column
    op.drop_column("feedbacks", "images")
