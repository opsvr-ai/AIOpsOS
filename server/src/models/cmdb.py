"""CMDB property graph models — unified abstraction for any CMDB system (iTop, ServiceNow, etc.)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class CmdbNode(Base):
    __tablename__ = "cmdb_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ci_type: Mapped[str] = mapped_column(
        String(64), index=True
    )
    name: Mapped[str] = mapped_column(String(256), index=True)
    external_id: Mapped[str] = mapped_column(String(256), index=True)
    source: Mapped[str] = mapped_column(
        String(64), index=True
    )
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spaces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    __table_args__ = (
        Index("ix_cmdb_nodes_properties", "properties", postgresql_using="gin"),
        Index("ix_cmdb_nodes_external_src", "external_id", "source"),
    )


class CmdbEdge(Base):
    __tablename__ = "cmdb_edges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cmdb_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cmdb_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type: Mapped[str] = mapped_column(
        String(32), index=True
    )
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    source: Mapped[str] = mapped_column(String(64))
    datasource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    __table_args__ = (
        Index("ix_cmdb_edges_node_pair", "source_node_id", "target_node_id"),
    )


class CmdbSyncLog(Base):
    __tablename__ = "cmdb_sync_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    datasource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    nodes_created: Mapped[int] = mapped_column(Integer, default=0)
    nodes_updated: Mapped[int] = mapped_column(Integer, default=0)
    nodes_deleted: Mapped[int] = mapped_column(Integer, default=0)
    edges_count: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    errors_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_snapshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spaces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )


class CmdbMappingRule(Base):
    __tablename__ = "cmdb_mapping_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    datasource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    rule_content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spaces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )


class CmdbReviewItem(Base):
    __tablename__ = "cmdb_review_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sync_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cmdb_sync_logs.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    review_type: Mapped[str] = mapped_column(String(16))
    source_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    transformed_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    llm_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reviewer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spaces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
