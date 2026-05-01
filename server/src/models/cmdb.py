"""CMDB property graph models — unified abstraction for any CMDB system (iTop, ServiceNow, etc.)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
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
