"""drop memory_legacy_sync feature flag row

Revision ID: 202605041840
Revises: 202605041830
Create Date: 2026-05-04 18:40:00.000000

Phase M / Task 25.2 of the Agent Runtime Optimization & Evolution spec.

Task 25.2 removes the legacy in-request LLM extraction path from
``DatabaseMemoryProvider.sync_turn``. The ``memory_legacy_sync``
feature-flag row that used to gate the old path is no longer read by
any code; this migration drops it so operators don't see a stale knob
in the admin UI.

The row is only deleted if it exists — fresh installs (or repeated
applications) are no-ops. Downgrade re-inserts the row with its
last-known default (enabled=False, rollout_percent=0) so the migration
chain stays reversible (R-9.1).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202605041840"
down_revision: str | None = "202605041830"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FLAG_KEY = "memory_legacy_sync"


def upgrade() -> None:
    """Delete the ``memory_legacy_sync`` row if present."""
    op.execute(
        sa.text(
            "DELETE FROM runtime_feature_flags WHERE key = :k"
        ).bindparams(k=_FLAG_KEY)
    )


def downgrade() -> None:
    """Restore the flag row to its pre-removal default.

    The old code path gated on ``enabled=False, rollout_percent=0`` so
    downgrade recreates the row in the OFF state. Operators who had
    tuned the row can re-tune it via the admin UI after downgrade.
    """
    op.execute(
        sa.text(
            """
            INSERT INTO runtime_feature_flags (key, enabled, rollout_percent, data)
            VALUES (:k, false, 0, :d::jsonb)
            ON CONFLICT (key) DO NOTHING
            """
        ).bindparams(
            k=_FLAG_KEY,
            d='{"description": "Keep in-request sync_turn LLM extraction (flipped off by Phase E)."}',
        )
    )
