"""data_ingestion_add_log_itsm_cmdb_tables

Revision ID: f6aa826b2a89
Revises: c2a0e4a72cbc
Create Date: 2026-05-02 07:52:45.577403
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY


revision: str = 'f6aa826b2a89'
down_revision: Union[str, None] = 'c2a0e4a72cbc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # log_events — partitioned by ingested_at (hourly), 30-min TTL
    op.create_table(
        "log_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), index=True, nullable=True),
        sa.Column("service", sa.String(128), index=True, nullable=True),
        sa.Column("host", sa.String(128), index=True, nullable=True),
        sa.Column("level", sa.String(16), index=True, nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("raw", JSONB, nullable=True),
        sa.Column("datasource_id", UUID(as_uuid=True),
                  sa.ForeignKey("datasources.id", ondelete="SET NULL"), index=True, nullable=True),
    )
    op.create_index("ix_log_events_service_level_ingested", "log_events",
                    ["service", "level", "ingested_at"])
    op.create_index("ix_log_events_trace_id", "log_events", ["trace_id"])

    # itsm_tickets
    op.create_table(
        "itsm_tickets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(256), unique=True, nullable=False, index=True),
        sa.Column("ticket_type", sa.String(32), index=True, nullable=True),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("status", sa.String(32), index=True, nullable=True),
        sa.Column("priority", sa.String(16), nullable=True),
        sa.Column("affected_service", sa.String(128), index=True, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_data", JSONB, nullable=True),
        sa.Column("linked_alert_ids", ARRAY(UUID(as_uuid=True)), nullable=True),
        sa.Column("datasource_id", UUID(as_uuid=True),
                  sa.ForeignKey("datasources.id", ondelete="SET NULL"), index=True, nullable=True),
        sa.Column("space_id", UUID(as_uuid=True),
                  sa.ForeignKey("spaces.id", ondelete="SET NULL"), index=True, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=True),
    )

    # cmdb_nodes — property graph nodes
    op.create_table(
        "cmdb_nodes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ci_type", sa.String(64), index=True, nullable=True),
        sa.Column("name", sa.String(256), index=True, nullable=True),
        sa.Column("external_id", sa.String(256), index=True, nullable=True),
        sa.Column("source", sa.String(64), index=True, nullable=True),
        sa.Column("properties", JSONB, nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("datasource_id", UUID(as_uuid=True),
                  sa.ForeignKey("datasources.id", ondelete="SET NULL"), index=True, nullable=True),
        sa.Column("space_id", UUID(as_uuid=True),
                  sa.ForeignKey("spaces.id", ondelete="SET NULL"), index=True, nullable=True),
    )
    op.create_index("ix_cmdb_nodes_properties", "cmdb_nodes", ["properties"],
                    postgresql_using="gin")

    # cmdb_edges — property graph edges
    op.create_table(
        "cmdb_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_node_id", UUID(as_uuid=True),
                  sa.ForeignKey("cmdb_nodes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("target_node_id", UUID(as_uuid=True),
                  sa.ForeignKey("cmdb_nodes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("relation_type", sa.String(64), index=True, nullable=True),
        sa.Column("properties", JSONB, nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    # cmdb_sync_logs
    op.create_table(
        "cmdb_sync_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("datasource_id", UUID(as_uuid=True),
                  sa.ForeignKey("datasources.id", ondelete="SET NULL"), index=True, nullable=True),
        sa.Column("mode", sa.String(16), default="incremental"),
        sa.Column("status", sa.String(16), default="running"),
        sa.Column("nodes_created", sa.Integer, default=0),
        sa.Column("nodes_updated", sa.Integer, default=0),
        sa.Column("nodes_deleted", sa.Integer, default=0),
        sa.Column("edges_count", sa.Integer, default=0),
        sa.Column("review_count", sa.Integer, default=0),
        sa.Column("errors_detail", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("space_id", UUID(as_uuid=True),
                  sa.ForeignKey("spaces.id", ondelete="SET NULL"), index=True, nullable=True),
    )

    # cmdb_mapping_rules
    op.create_table(
        "cmdb_mapping_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("datasource_id", UUID(as_uuid=True),
                  sa.ForeignKey("datasources.id", ondelete="SET NULL"), index=True, nullable=True),
        sa.Column("version", sa.Integer, default=1),
        sa.Column("rule_content", JSONB, nullable=False),
        sa.Column("status", sa.String(16), default="draft"),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=True),
        sa.Column("space_id", UUID(as_uuid=True),
                  sa.ForeignKey("spaces.id", ondelete="SET NULL"), index=True, nullable=True),
    )

    # cmdb_review_items
    op.create_table(
        "cmdb_review_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sync_log_id", UUID(as_uuid=True),
                  sa.ForeignKey("cmdb_sync_logs.id", ondelete="SET NULL"), index=True, nullable=True),
        sa.Column("review_type", sa.String(16), nullable=True),
        sa.Column("source_data", JSONB, nullable=True),
        sa.Column("transformed_data", JSONB, nullable=True),
        sa.Column("llm_confidence", sa.Integer, nullable=True),
        sa.Column("llm_reason", sa.Text, nullable=True),
        sa.Column("diff_summary", JSONB, nullable=True),
        sa.Column("status", sa.String(16), default="pending"),
        sa.Column("reviewer", sa.String(128), nullable=True),
        sa.Column("review_note", sa.Text, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=True),
        sa.Column("space_id", UUID(as_uuid=True),
                  sa.ForeignKey("spaces.id", ondelete="SET NULL"), index=True, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("cmdb_review_items")
    op.drop_table("cmdb_mapping_rules")
    op.drop_table("cmdb_sync_logs")
    op.drop_table("cmdb_edges")
    op.drop_table("cmdb_nodes")
    op.drop_table("itsm_tickets")
    op.drop_table("log_events")
