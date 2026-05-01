"""Change alerts.event_id from UUID to String

Revision ID: 007_event_id_string
Revises: 006_schedule_next_run
Create Date: 2026-04-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "007_event_id_string"
down_revision: Union[str, None] = "006_schedule_next_run"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("alerts", "event_id",
                    existing_type=sa.dialects.postgresql.UUID(),
                    type_=sa.String(256),
                    existing_nullable=True,
                    postgresql_using="event_id::varchar")


def downgrade() -> None:
    op.execute("DELETE FROM alerts WHERE event_id IS NOT NULL AND event_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'")
    op.alter_column("alerts", "event_id",
                    existing_type=sa.String(256),
                    type_=sa.dialects.postgresql.UUID(),
                    existing_nullable=True,
                    postgresql_using="event_id::uuid")
