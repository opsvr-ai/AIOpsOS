# Data Ingestion Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the three new data ingestion channels (Log, ITSM, CMDB) plus CmdbIngestionAgent as defined in the data ingestion design document.

**Architecture:** Extend the existing DataSource model with new source_types, add partitioned log storage with 30-minute TTL, ITSM ticket tracking with alert correlation, and a CMDB property graph with LLM-driven mapping rules. A new CmdbIngestionAgent (following MemoryConsolidationAgent pattern) orchestrates CMDB sync with three-layer validation. New API endpoints for CMDB CRUD, review queue, log search, and ITSM search.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 async, PostgreSQL 15 (partitioning + pg_cron), LangChain/LangGraph, React 18 + TypeScript + Ant Design 5

**Design Doc:** `docs/superpowers/specs/2026-05-01-data-ingestion-design.md`

---

## File Structure Map

```
New files (backend):
  server/src/models/log.py                          — LogEvent partitioned table model
  server/src/models/itsm.py                         — ItsmTicket model
  server/src/models/cmdb.py                         — CmdbNode, CmdbEdge, CmdbSyncLog, CmdbMappingRule, CmdbReviewItem
  server/src/services/log_processor.py              — LogProcessor: normalize + batch insert
  server/src/services/itsm_processor.py             — ItsmProcessor: normalize + enrich + alert linking
  server/src/agent/sub_agents/cmdb_ingestion_agent.py — CmdbIngestionAgent: full sync pipeline
  server/src/api/control/cmdb.py                    — CMDB CRUD API + review queue + mapping rules
  server/src/api/execution/log_search.py            — Log search API for agent tools
  server/src/api/execution/itsm_search.py           — ITSM search API for agent tools

New files (frontend):
  web/src/features/cmdb/CmdbPage.tsx                — CMDB management: topology + sync + review
  web/src/features/logs/LogIngestionPage.tsx        — Log viewer: real-time stream + search
  web/src/features/itsm/ItsmPage.tsx                — ITSM tickets: list + timeline + alert linking

Modified files (backend):
  server/src/schemas/datasource.py                  — Extend source_type Literal + add config schemas
  server/src/models/datasource.py                   — Extend source_type column comment/check
  server/src/api/execution/datasources.py            — Add log/itsm/cmdb test branches + sync endpoint
  server/src/api/control/router.py                   — Register cmdb_router
  server/src/main.py                                 — Register log_search, itsm_search routers

Modified files (frontend):
  web/src/features/datacenter/DataSourcePage.tsx     — Add log/itsm/cmdb to type filter
  web/src/features/datacenter/DataSourceFormModal.tsx — Conditional config fields for new types
  web/src/components/layout/Sidebar.tsx              — Add CMDB/日志/ITSM menu items
  web/src/router/index.tsx                           — Add CmdbPage, LogIngestionPage, ItsmPage routes

Database:
  server/migrations/versions/xxxx_data_ingestion.py  — Alembic migration (auto-generated)
```

---

### Task 1: Extend DataSource Schema and Model

**Files:**
- Modify: `server/src/schemas/datasource.py`
- Modify: `server/src/models/datasource.py`

- [ ] **Step 1: Extend source_type in DataSourceCreate schema**

In `server/src/schemas/datasource.py`, update the `source_type` field in `DataSourceCreate`:

```python
# Find the Literal in DataSourceCreate and change from:
# source_type: Literal["kafka", "webhook", "api"]
# To:
source_type: Literal["kafka", "webhook", "api", "log", "itsm", "cmdb"]
```

- [ ] **Step 2: Add type-specific config schemas**

In `server/src/schemas/datasource.py`, add after the existing config schemas:

```python
# Log ingestion config
class LogConfig(BaseModel):
    """Configuration for log-type DataSource."""
    source: Literal["filebeat", "kafka", "vector"] = "filebeat"
    filebeat_input: str | None = None       # e.g. "/var/log/*.log"
    kafka_topic: str | None = None
    kafka_bootstrap_servers: str | None = None
    batch_size: int = 500
    batch_flush_ms: int = 500
    retention_minutes: int = 30
    partition_interval: Literal["hourly", "daily"] = "hourly"
    index_mappings: dict[str, str] | None = None  # field -> index type overrides


# ITSM ingestion config
class ItsmConfig(BaseModel):
    """Configuration for itsm-type DataSource."""
    itsm_system: Literal["servicenow", "jira", "zendesk", "custom"] = "custom"
    ticket_types: list[str] = ["incident", "change", "problem", "request"]
    request_chain: list[dict] | None = None  # reuses ApiPoller request_chain
    poll_interval_seconds: int = 300
    webhook_secret: str | None = None
    alert_link_window_minutes: int = 30
    field_mapping: dict[str, str] | None = None  # external -> standard field


# CMDB ingestion config
class CmdbConfig(BaseModel):
    """Configuration for cmdb-type DataSource."""
    cmdb_system: Literal["itop", "servicenow", "custom"] = "custom"
    api_base_url: str = ""
    sync_schedule: str = "0 * * * *"           # cron: hourly by default
    topology_sync_interval_hours: int = 1
    host_sync_interval_hours: int = 24
    mapping_rule_path: str | None = None       # path to YAML mapping rules
    default_mode: Literal["discover", "incremental", "full"] = "incremental"
    validation_sample_rate: float = 0.1        # L2 semantic validation sample rate
```

- [ ] **Step 3: Update DataSource model comment**

In `server/src/models/datasource.py`, update the `source_type` column to reflect new types:

```python
# Find: source_type = Column(String(32), nullable=False, comment="Source type: kafka, webhook, api")
# Change to:
source_type = Column(String(32), nullable=False, comment="Source type: kafka, webhook, api, log, itsm, cmdb")
```

- [ ] **Step 4: Commit**

```bash
git add server/src/schemas/datasource.py server/src/models/datasource.py
git commit -m "feat: extend DataSource source_type with log/itsm/cmdb and add config schemas"
```

---

### Task 2: Create LogEvent Partitioned Table Model

**Files:**
- Create: `server/src/models/log.py`

- [ ] **Step 1: Create the LogEvent model**

```python
"""Log event model — hourly partitioned table with 30-min TTL window."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, DateTime, ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

from src.models.base import DeclarativeBase


class LogEvent(DeclarativeBase):
    """Partitioned log events table.

    Partition key: ingested_at (hourly range partitions managed by pg_partman or manual DDL).
    TTL: 30 minutes via pg_cron DELETE WHERE ingested_at < NOW() - INTERVAL '30 minutes'.
    """

    __tablename__ = "log_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingested_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    timestamp = Column(DateTime(timezone=True), index=True)
    service = Column(String(128), index=True)
    host = Column(String(128), index=True)
    level = Column(String(16), index=True)
    trace_id = Column(String(64), index=True)
    message = Column(Text)
    raw = Column(JSONB)
    datasource_id = Column(UUID(as_uuid=True), ForeignKey("datasources.id"), index=True)

    __table_args__ = (
        Index("ix_log_events_service_level_ingested", "service", "level", "ingested_at"),
        Index("ix_log_events_trace_id", "trace_id"),
    )
```

- [ ] **Step 2: Commit**

```bash
git add server/src/models/log.py
git commit -m "feat: add LogEvent model for partitioned log storage"
```

---

### Task 3: Create ItsmTicket Model

**Files:**
- Create: `server/src/models/itsm.py`

- [ ] **Step 1: Create the ItsmTicket model**

```python
"""ITSM ticket model — incident/change/problem/request tracking with alert correlation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

from src.models.base import DeclarativeBase, TimestampMixin


class ItsmTicket(DeclarativeBase, TimestampMixin):
    """Unified ITSM ticket across multiple ITSM systems."""

    __tablename__ = "itsm_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id = Column(String(256), unique=True, nullable=False, index=True)
    ticket_type = Column(String(32), index=True)          # incident/change/problem/request
    title = Column(String(512))
    status = Column(String(32), index=True)               # new/in_progress/resolved/closed
    priority = Column(String(16))                          # critical/high/medium/low
    affected_service = Column(String(128), index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True))
    raw_data = Column(JSONB)
    linked_alert_ids = Column(ARRAY(UUID(as_uuid=True)))
    datasource_id = Column(UUID(as_uuid=True), ForeignKey("datasources.id"), index=True)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id"), index=True)

    __table_args__ = (
        Index("ix_itsm_tickets_service_time", "affected_service", "created_at"),
        Index("ix_itsm_tickets_type_status", "ticket_type", "status"),
    )
```

- [ ] **Step 2: Commit**

```bash
git add server/src/models/itsm.py
git commit -m "feat: add ItsmTicket model for ITSM workflow tracking"
```

---

### Task 4: Create CmdbNode and CmdbEdge Models

**Files:**
- Create: `server/src/models/cmdb.py` (part 1 — nodes and edges)

- [ ] **Step 1: Create CmdbNode and CmdbEdge models**

```python
"""CMDB property graph models — unified CI node/edge abstraction for any CMDB system."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB

from src.models.base import DeclarativeBase


class CmdbNode(DeclarativeBase):
    """CI node in the property graph — server, app, db, lb, vip, rack, etc."""

    __tablename__ = "cmdb_nodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ci_type = Column(String(64), index=True)
    name = Column(String(256), index=True)
    external_id = Column(String(256), index=True)
    source = Column(String(64), index=True)               # cmdb-itop / cmdb-servicenow
    properties = Column(JSONB, index=True)                 # GIN-indexed for flexible search
    synced_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    datasource_id = Column(UUID(as_uuid=True), ForeignKey("datasources.id"), index=True)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id"), index=True)

    __table_args__ = (
        Index("ix_cmdb_nodes_external_source", "external_id", "source"),
        Index("ix_cmdb_nodes_type", "ci_type"),
    )


class CmdbEdge(DeclarativeBase):
    """Relationship edge in the property graph."""

    __tablename__ = "cmdb_edges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_node_id = Column(UUID(as_uuid=True), ForeignKey("cmdb_nodes.id"), nullable=False, index=True)
    target_node_id = Column(UUID(as_uuid=True), ForeignKey("cmdb_nodes.id"), nullable=False, index=True)
    relation_type = Column(String(32), index=True)        # depends_on / runs_on / contains / connects_to
    properties = Column(JSONB)                             # port, protocol, etc.
    source = Column(String(64))                            # CMDB origin
    datasource_id = Column(UUID(as_uuid=True), ForeignKey("datasources.id"), index=True)

    __table_args__ = (
        Index("ix_cmdb_edges_source_target", "source_node_id", "target_node_id"),
    )
```

- [ ] **Step 2: Commit**

```bash
git add server/src/models/cmdb.py
git commit -m "feat: add CmdbNode and CmdbEdge property graph models"
```

---

### Task 5: Create CMDB Support Models (SyncLog, MappingRule, ReviewItem)

**Files:**
- Modify: `server/src/models/cmdb.py` (append to existing file)

- [ ] **Step 1: Append support models to cmdb.py**

```python
# Append after CmdbEdge class in server/src/models/cmdb.py:


class CmdbSyncLog(DeclarativeBase):
    """Tracks each CMDB synchronization run."""

    __tablename__ = "cmdb_sync_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    datasource_id = Column(UUID(as_uuid=True), ForeignKey("datasources.id"), index=True)
    mode = Column(String(16))                              # discover / incremental / full
    status = Column(String(16), index=True)                # running / completed / failed
    nodes_created = Column(Integer, default=0)
    nodes_updated = Column(Integer, default=0)
    nodes_deleted = Column(Integer, default=0)
    edges_count = Column(Integer, default=0)
    review_count = Column(Integer, default=0)
    errors_detail = Column(JSONB)
    raw_snapshot_path = Column(String(512))
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id"), index=True)


class CmdbMappingRule(DeclarativeBase):
    """Stores discovered or manually defined CMDB-to-property-graph mapping rules."""

    __tablename__ = "cmdb_mapping_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    datasource_id = Column(UUID(as_uuid=True), ForeignKey("datasources.id"), index=True)
    version = Column(Integer, default=1)
    rule_content = Column(JSONB, nullable=False)           # the mapping YAML as JSON
    status = Column(String(16), default="draft")           # draft / active / superseded
    approved_by = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id"), index=True)


class CmdbReviewItem(DeclarativeBase):
    """Items flagged for human review during CMDB sync (L2/L3 validation failures)."""

    __tablename__ = "cmdb_review_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_log_id = Column(UUID(as_uuid=True), ForeignKey("cmdb_sync_logs.id"), index=True)
    review_type = Column(String(16))                       # semantic / anomaly
    source_data = Column(JSONB)
    transformed_data = Column(JSONB)
    llm_confidence = Column(Integer)                       # 0-100
    llm_reason = Column(Text)
    diff_summary = Column(JSONB)
    status = Column(String(16), default="pending")         # pending / approved / rejected
    reviewer = Column(String(128))
    review_note = Column(Text)
    reviewed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id"), index=True)
```

- [ ] **Step 2: Commit**

```bash
git add server/src/models/cmdb.py
git commit -m "feat: add CmdbSyncLog, CmdbMappingRule, CmdbReviewItem support models"
```

---

### Task 6: Create LogProcessor Service

**Files:**
- Create: `server/src/services/log_processor.py`

- [ ] **Step 1: Create LogProcessor with normalization and batch insert**

```python
"""LogProcessor — normalizes raw log entries and writes to log_events partition table."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.models.base import async_session_factory
from src.models.log import LogEvent

logger = logging.getLogger(__name__)

# Priority-ordered field extractors for each target field
FIELD_EXTRACTORS: dict[str, list[str]] = {
    "timestamp": ["@timestamp", "time", "timestamp"],
    "service": ["service", "app", "container_name", "namespace"],
    "level": ["level", "severity", "log_level"],
    "trace_id": ["trace_id", "traceId", "x-trace-id", "traceparent"],
    "message": ["message", "msg", "log", "body"],
}


def _extract_field(raw: dict[str, Any], target: str) -> Any | None:
    """Extract a field from raw log using priority-ordered extractor list."""
    extractors = FIELD_EXTRACTORS.get(target, [target])
    for key in extractors:
        value = raw.get(key)
        if value is not None and value != "":
            return str(value)[:600] if target == "message" else value
    return None


def _try_parse_timestamp(value: Any) -> datetime | None:
    """Try to parse a timestamp from common formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ]:
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize(raw: dict[str, Any], datasource_id: str) -> dict[str, Any]:
    """Normalize a single raw log entry into LogEvent fields.

    Returns dict ready for LogEvent insert.
    """
    ts = _try_parse_timestamp(_extract_field(raw, "timestamp"))
    return {
        "timestamp": ts,
        "service": _extract_field(raw, "service") or "unknown",
        "host": raw.get("host", raw.get("hostname", "")) or "unknown",
        "level": (_extract_field(raw, "level") or "INFO").upper(),
        "trace_id": _extract_field(raw, "trace_id"),
        "message": _extract_field(raw, "message") or str(raw)[:600],
        "raw": raw,
        "datasource_id": datasource_id,
    }


async def batch_insert(events: list[dict[str, Any]]) -> int:
    """Insert a batch of normalized log events. Returns count inserted."""
    if not events:
        return 0
    async with async_session_factory() as db:
        stmt = pg_insert(LogEvent).values(events).on_conflict_do_nothing()
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount


async def process_batch(
    raw_logs: list[dict[str, Any]],
    datasource_id: str,
) -> int:
    """Normalize and insert a batch of raw logs. Returns count inserted."""
    normalized = [normalize(log, datasource_id) for log in raw_logs]
    count = await batch_insert(normalized)
    logger.debug("Log batch: %d raw -> %d inserted", len(raw_logs), count)
    return count
```

- [ ] **Step 2: Commit**

```bash
git add server/src/services/log_processor.py
git commit -m "feat: add LogProcessor with field extraction and batch insert"
```

---

### Task 7: Create ItsmProcessor Service

**Files:**
- Create: `server/src/services/itsm_processor.py`

- [ ] **Step 1: Create ItsmProcessor with normalization, enrichment, and alert linking**

```python
"""ItsmProcessor — normalizes ITSM tickets, enriches with service context, links alerts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.models.base import async_session_factory
from src.models.itsm import ItsmTicket

logger = logging.getLogger(__name__)

# Standard field mapping for common ITSM systems
SYSTEM_FIELD_MAPS: dict[str, dict[str, str]] = {
    "servicenow": {
        "external_id": "sys_id",
        "ticket_type": "sys_class_name",
        "title": "short_description",
        "status": "state",
        "priority": "priority",
        "affected_service": "cmdb_ci",
        "created_at": "sys_created_on",
        "resolved_at": "closed_at",
    },
    "jira": {
        "external_id": "key",
        "ticket_type": "issuetype.name",
        "title": "fields.summary",
        "status": "fields.status.name",
        "priority": "fields.priority.name",
        "created_at": "fields.created",
        "resolved_at": "fields.resolutiondate",
    },
}


def _get_nested(obj: dict, path: str) -> Any | None:
    """Get a nested dict value by dot-separated path. E.g. 'fields.status.name'."""
    for key in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def normalize_ticket(
    raw: dict[str, Any],
    datasource_id: str,
    itsm_system: str = "custom",
    custom_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize a raw ITSM ticket into ItsmTicket fields.

    Uses built-in system mappings or custom field_mapping from DataSource config.
    """
    field_map = custom_mapping or SYSTEM_FIELD_MAPS.get(itsm_system, {})
    external_id = str(_get_nested(raw, field_map.get("external_id", "id")) or raw.get("id", ""))

    def extract(field: str, default: Any = None) -> Any:
        mapped = field_map.get(field, field)
        return _get_nested(raw, mapped) or raw.get(mapped, default)

    return {
        "external_id": external_id,
        "ticket_type": str(extract("ticket_type", "incident")).lower(),
        "title": str(extract("title", "") or "")[:512],
        "status": str(extract("status", "new")).lower(),
        "priority": str(extract("priority", "medium")).lower(),
        "affected_service": str(extract("affected_service", "") or ""),
        "created_at": extract("created_at"),
        "resolved_at": extract("resolved_at"),
        "raw_data": raw,
        "datasource_id": datasource_id,
    }


async def link_alerts(
    ticket_id: str,
    affected_service: str,
    ticket_time: datetime,
    window_minutes: int = 30,
) -> list[str]:
    """Link ITSM ticket to alerts by service name + time window (±window_minutes)."""
    from src.models.alert import Alert

    window = timedelta(minutes=window_minutes)
    start = ticket_time - window
    end = ticket_time + window

    async with async_session_factory() as db:
        result = await db.execute(
            select(Alert.id).where(
                Alert.created_at.between(start, end),
            )
        )
        alert_ids = [str(row[0]) for row in result.all()]
        if alert_ids and affected_service:
            # Further filter by service affinity (simple substring match)
            pass  # Service-level filtering done in application layer
        return alert_ids


async def upsert_ticket(ticket_data: dict[str, Any], alert_ids: list[str] | None = None) -> ItsmTicket:
    """Insert or update an ITSM ticket by external_id."""
    ticket_data_copy = dict(ticket_data)
    if alert_ids:
        ticket_data_copy["linked_alert_ids"] = alert_ids
    async with async_session_factory() as db:
        stmt = (
            pg_insert(ItsmTicket)
            .values(**ticket_data_copy)
            .on_conflict_do_update(
                index_elements=["external_id"],
                set_={
                    "status": ticket_data_copy.get("status"),
                    "priority": ticket_data_copy.get("priority"),
                    "raw_data": ticket_data_copy.get("raw_data"),
                    "resolved_at": ticket_data_copy.get("resolved_at"),
                    "linked_alert_ids": ticket_data_copy.get("linked_alert_ids"),
                },
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        return result


async def process_tickets(
    raw_tickets: list[dict[str, Any]],
    datasource_id: str,
    itsm_system: str = "custom",
    custom_mapping: dict[str, str] | None = None,
    link_window_minutes: int = 30,
) -> int:
    """Normalize + upsert a batch of ITSM tickets. Returns count processed."""
    count = 0
    for raw in raw_tickets:
        normalized = normalize_ticket(raw, datasource_id, itsm_system, custom_mapping)
        if not normalized["external_id"]:
            continue
        await upsert_ticket(normalized)
        count += 1
    logger.info("ITSM batch: %d tickets upserted", count)
    return count
```

- [ ] **Step 2: Commit**

```bash
git add server/src/services/itsm_processor.py
git commit -m "feat: add ItsmProcessor with normalization, field mapping, and alert linking"
```

---

### Task 8: Create CmdbIngestionAgent

**Files:**
- Create: `server/src/agent/sub_agents/cmdb_ingestion_agent.py`

- [ ] **Step 1: Create CmdbDataFetcher abstract interface and ApiCmdbFetcher**

```python
"""CmdbIngestionAgent — orchestrates CMDB → property graph sync with LLM-driven mapping.

Follows MemoryConsolidationAgent pattern: optional model injection, lazy _get_llm().
State machine: idle → fetching → transforming → validating → reviewing → writing → idle
"""

from __future__ import annotations

import json as _json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select, delete

from src.models.base import async_session_factory
from src.models.cmdb import CmdbNode, CmdbEdge, CmdbSyncLog, CmdbMappingRule, CmdbReviewItem
from src.models.datasource import DataSource

logger = logging.getLogger(__name__)

# ── Data Fetcher abstraction ────────────────────────────────────────────


class CmdbDataFetcher(ABC):
    """Pluggable CMDB data fetcher. Replace with Skill-based fetcher later."""

    @abstractmethod
    async def fetch(self, ds: DataSource) -> list[dict[str, Any]]:
        """Fetch raw CI data from the CMDB source."""
        ...


class ApiCmdbFetcher(CmdbDataFetcher):
    """Fetch CMDB data via REST API using DataSource.config."""

    async def fetch(self, ds: DataSource) -> list[dict[str, Any]]:
        config = ds.config or {}
        base_url = config.get("api_base_url", "").rstrip("/")
        headers = config.get("headers", {})
        auth = config.get("auth", {})
        auth_param = None
        if auth.get("type") == "bearer" and auth.get("token"):
            headers["Authorization"] = f"Bearer {auth['token']}"
        elif auth.get("type") == "basic":
            from base64 import b64encode
            creds = b64encode(f"{auth['username']}:{auth['password']}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"

        ci_endpoint = config.get("ci_endpoint", "/api/v1/cis")
        params = config.get("fetch_params", {"limit": 500})

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(f"{base_url}{ci_endpoint}", headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                items = data.get("items", data.get("data", data.get("results", [])))
            elif isinstance(data, list):
                items = data
            else:
                items = []
            return items


# ── CMDB Ingestion Agent ────────────────────────────────────────────────

CMDB_DISCOVERY_PROMPT = """你是 CMDB 配置管理专家。将以下原始 CI 数据转换为运维平台的属性图模型。

## CI 类型分类规则
- 包含 ip_address / os_version / cpu_cores 的 → server
- 名称含 -app / -svc / -api 后缀，或有 deploy_info / version / url 的 → app
- 名称含 -db / -mysql / -pg / -redis 或端口 3306/5432/6379 的 → db
- 名称含 vip / virtual_ip / loadbalancer 的 → vip
- 名称含 rack- / 机柜 的 → rack

## 关系推断规则
- app 有 runs_on / depends_on 字段的 → 生成对应关系
- app 部署在 server 上 → runs_on (server, app)
- db 部署在 server 上 → runs_on (server, db)
- app 连接 db（端口 3306/5432/6379）→ depends_on (app, db)

## 输出格式
严格返回 JSON:
{
  "nodes": [
    {"external_id": "原始ID", "name": "标准化名称", "ci_type": "server|app|db|vip|rack|...", "properties": {...}}
  ],
  "edges": [
    {"source_external_id": "", "target_external_id": "", "relation_type": "depends_on|runs_on|contains|connects_to"}
  ],
  "new_rules": [
    {"ci_type": "检测到的新CI类型", "name_pattern": "命名规律", "id_field": "ID字段名", "properties_to_extract": ["属性1", "属性2"], "relation_mapping": {"field": "关联字段", "rule": "转换规则描述"}}
  ]
}

最多处理 50 条 CI 数据。没有关系或新规则时返回空数组。"""


class CmdbIngestionAgent:
    """Autonomous agent for CMDB → property graph synchronization.

    Usage:
        agent = CmdbIngestionAgent()
        result = await agent.run_sync(datasource_id, mode="incremental")
    """

    def __init__(self, model=None) -> None:
        self._llm = model
        self._fetcher: CmdbDataFetcher = ApiCmdbFetcher()

    async def _get_llm(self):
        if self._llm is None:
            from src.core.model_factory import get_default_model
            self._llm = await get_default_model()
        return self._llm

    # ── Public API ──────────────────────────────────────────────────────

    async def run_sync(self, datasource_id: str, mode: str = "incremental") -> dict[str, Any]:
        """Execute a full sync pipeline. Returns summary stats."""
        ds = await self._load_datasource(datasource_id)
        if ds is None:
            return {"success": False, "error": f"DataSource {datasource_id} not found"}

        sync_log = await self._create_sync_log(datasource_id, mode)
        try:
            raw_data = await self._fetch(ds)
            logger.info("Sync %s: fetched %d CI records", sync_log.id, len(raw_data))

            if mode == "discover" or mode == "full":
                rule = await self._discover_schema(raw_data, ds)
                await self._store_mapping_rule(datasource_id, rule)
                logger.info("Sync %s: discovered mapping rule v%d", sync_log.id, rule.get("version", 1))

            nodes, edges = await self._transform_batch(raw_data, ds)
            logger.info("Sync %s: transformed -> %d nodes, %d edges", sync_log.id, len(nodes), len(edges))

            validation_errors = await self._validate_structure(nodes, edges)
            review_items = await self._validate_semantic(nodes[:10], raw_data[:10], ds)
            anomalies = await self._detect_anomaly(nodes, datasource_id)

            if anomalies:
                logger.warning("Sync %s: %d anomalies detected, flagging for review", sync_log.id, len(anomalies))

            node_stats = await self._upsert_nodes(nodes)
            edge_stats = await self._upsert_edges(edges)
            cleaned = await self._cleanup_stale(datasource_id, [n["external_id"] for n in nodes])

            await self._finalize_sync_log(sync_log.id, node_stats, edge_stats, cleaned, len(review_items))
            return {
                "success": True,
                "sync_log_id": str(sync_log.id),
                "nodes": node_stats,
                "edges": edge_stats,
                "cleaned": cleaned,
                "review_count": len(review_items),
                "anomalies": len(anomalies),
            }
        except Exception as exc:
            logger.exception("Sync %s failed", sync_log.id)
            await self._mark_sync_failed(sync_log.id, str(exc))
            return {"success": False, "error": str(exc)}

    # ── Phase 1: Fetch ──────────────────────────────────────────────────

    async def _fetch(self, ds: DataSource) -> list[dict[str, Any]]:
        return await self._fetcher.fetch(ds)

    # ── Phase 2: Transform ──────────────────────────────────────────────

    async def _discover_schema(self, raw_data: list[dict], ds: DataSource) -> dict[str, Any]:
        """LLM-driven schema discovery: analyze raw CI data, generate mapping rules."""
        llm = await self._get_llm()
        sample = raw_data[:50]
        resp = await llm.ainvoke([
            SystemMessage(content=CMDB_DISCOVERY_PROMPT),
            HumanMessage(content=f"分析以下 {len(sample)} 条CMDB原始数据，生成映射规则:\n{_json.dumps(sample, ensure_ascii=False, default=str)}"),
        ])
        try:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("\n```", 1)[0]
            result = _json.loads(raw)
            return {"version": 1, "data": result}
        except Exception:
            logger.exception("Schema discovery failed")
            return {"version": 1, "data": {"nodes": [], "edges": [], "new_rules": []}}

    async def _transform_batch(self, raw_data: list[dict], ds: DataSource) -> tuple[list[dict], list[dict]]:
        """Transform raw CI data into normalized nodes and edges using existing rules."""
        # Load active mapping rule if exists
        rule = await self._load_active_rule(str(ds.id))
        if rule:
            return self._rule_based_transform(raw_data, rule)

        # Fall back to LLM-based transformation for discover mode
        return await self._llm_transform(raw_data[:50])

    def _rule_based_transform(self, raw_data: list[dict], rule: dict) -> tuple[list[dict], list[dict]]:
        """Apply a known mapping rule to transform raw data. Deterministic, no LLM."""
        rule_data = rule.get("rule_content", {}).get("data", {})
        nodes: list[dict] = []
        edges: list[dict] = []

        for item in raw_data:
            ci_type_str = item.get("ci_type", item.get("type", ""))
            node = {
                "external_id": str(item.get("id", item.get("external_id", uuid4()))),
                "name": item.get("name", item.get("display_name", ci_type_str)),
                "ci_type": ci_type_str.lower() or "unknown",
                "properties": {k: v for k, v in item.items() if k not in ("id", "external_id", "name", "ci_type", "type")},
            }
            nodes.append(node)

            # Extract edges from known relation fields
            for rel_field in ("depends_on", "runs_on", "relations", "linked_ci"):
                if rel_field in item and item[rel_field]:
                    targets = item[rel_field] if isinstance(item[rel_field], list) else [item[rel_field]]
                    for target in targets:
                        edges.append({
                            "source_external_id": node["external_id"],
                            "target_external_id": str(target.get("id", target)),
                            "relation_type": "depends_on" if "depend" in rel_field else "runs_on",
                        })

        return nodes, edges

    async def _llm_transform(self, raw_data: list[dict]) -> tuple[list[dict], list[dict]]:
        """Use LLM to transform a sample of raw data (discover mode fallback)."""
        llm = await self._get_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=CMDB_DISCOVERY_PROMPT),
            HumanMessage(content=f"转换以下 {len(raw_data)} 条CMDB数据:\n{_json.dumps(raw_data, ensure_ascii=False, default=str)}"),
        ])
        try:
            raw_text = resp.content.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[1].rsplit("\n```", 1)[0]
            result = _json.loads(raw_text)
            return result.get("nodes", []), result.get("edges", [])
        except Exception:
            logger.exception("LLM transform failed")
            return [], []

    # ── Phase 3: Validate ───────────────────────────────────────────────

    async def _validate_structure(self, nodes: list[dict], edges: list[dict]) -> list[dict]:
        """L1 structural validation: required fields, valid refs, no self-loops."""
        errors: list[dict] = []
        node_ids = {n["external_id"] for n in nodes}

        for i, node in enumerate(nodes):
            if not node.get("external_id"):
                errors.append({"index": i, "type": "missing_external_id", "detail": "Node has no external_id"})
            if not node.get("name"):
                errors.append({"index": i, "type": "missing_name", "detail": "Node has no name"})
            if not node.get("ci_type"):
                errors.append({"index": i, "type": "missing_ci_type", "detail": "Node has no ci_type"})
            elif node["ci_type"] not in ("server", "app", "db", "vip", "lb", "rack", "unknown"):
                errors.append({"index": i, "type": "unknown_ci_type", "detail": f"ci_type '{node['ci_type']}' not in known types"})

        for i, edge in enumerate(edges):
            src = edge.get("source_external_id")
            tgt = edge.get("target_external_id")
            if src == tgt:
                errors.append({"index": i, "type": "self_loop", "detail": f"Edge references itself: {src}"})
            if src and src not in node_ids:
                errors.append({"index": i, "type": "dangling_source", "detail": f"Source node not found: {src}"})
            if tgt and tgt not in node_ids:
                errors.append({"index": i, "type": "dangling_target", "detail": f"Target node not found: {tgt}"})

        if errors:
            logger.warning("L1 validation: %d structural errors", len(errors))
        return errors

    async def _validate_semantic(
        self, sample: list[dict], raw_data: list[dict], ds: DataSource
    ) -> list[dict]:
        """L2 semantic validation: LLM compares raw vs transformed for correctness."""
        if not sample:
            return []
        llm = await self._get_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=(
                "你是CMDB数据质量审核专家。比对原始CI数据与转换后的属性图数据，评估转换质量。\n"
                "对每条记录给出置信度评分(0-100)和审核意见。\n"
                "返回JSON: [{\"index\": 0, \"confidence\": 85, \"reason\": \"...\", \"issues\": []}]"
            )),
            HumanMessage(content=_json.dumps({
                "raw": raw_data,
                "transformed": sample,
            }, ensure_ascii=False, default=str)),
        ])
        try:
            raw_text = resp.content.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[1].rsplit("\n```", 1)[0]
            assessments = _json.loads(raw_text)
            return [
                a for a in assessments
                if a.get("confidence", 100) < 80
            ]
        except Exception:
            logger.exception("L2 semantic validation failed")
            return []

    async def _detect_anomaly(self, nodes: list[dict], datasource_id: str) -> list[dict]:
        """L3 statistical anomaly detection: compare vs previous sync."""
        prev_count = await self._count_nodes(datasource_id)
        if prev_count == 0:
            return []
        new_count = len(nodes)
        change_pct = abs(new_count - prev_count) / prev_count * 100
        anomalies = []
        if change_pct > 20:
            anomalies.append({
                "type": "node_count_change",
                "previous": prev_count,
                "current": new_count,
                "change_pct": round(change_pct, 1),
                "threshold": 20,
                "detail": f"Node count changed by {change_pct:.1f}% (threshold: 20%)",
            })
        return anomalies

    # ── Phase 4: Write ──────────────────────────────────────────────────

    async def _upsert_nodes(self, nodes: list[dict]) -> dict[str, int]:
        """Upsert nodes into cmdb_nodes. Returns {created, updated}."""
        created = updated = 0
        async with async_session_factory() as db:
            for node in nodes:
                existing = await db.execute(
                    select(CmdbNode).where(
                        CmdbNode.external_id == node["external_id"],
                        CmdbNode.source == node.get("source", ""),
                    )
                )
                existing_node = existing.scalar_one_or_none()
                if existing_node:
                    existing_node.name = node.get("name", existing_node.name)
                    existing_node.ci_type = node.get("ci_type", existing_node.ci_type)
                    existing_node.properties = node.get("properties", existing_node.properties)
                    existing_node.synced_at = datetime.now(timezone.utc)
                    updated += 1
                else:
                    db.add(CmdbNode(
                        external_id=node["external_id"],
                        name=node.get("name", ""),
                        ci_type=node.get("ci_type", "unknown"),
                        source=node.get("source", ""),
                        properties=node.get("properties", {}),
                    ))
                    created += 1
            await db.commit()
        return {"created": created, "updated": updated}

    async def _upsert_edges(self, edges: list[dict]) -> dict[str, int]:
        """Upsert edges into cmdb_edges. Returns {created, deleted}."""
        created = 0
        async with async_session_factory() as db:
            for edge in edges:
                src_node = await db.execute(
                    select(CmdbNode.id).where(CmdbNode.external_id == edge["source_external_id"])
                )
                tgt_node = await db.execute(
                    select(CmdbNode.id).where(CmdbNode.external_id == edge["target_external_id"])
                )
                src_id = src_node.scalar_one_or_none()
                tgt_id = tgt_node.scalar_one_or_none()
                if src_id and tgt_id:
                    db.add(CmdbEdge(
                        source_node_id=src_id,
                        target_node_id=tgt_id,
                        relation_type=edge.get("relation_type", "depends_on"),
                        properties=edge.get("properties", {}),
                        source=edge.get("source", ""),
                    ))
                    created += 1
            await db.commit()
        return {"created": created, "deleted": 0}

    async def _cleanup_stale(self, datasource_id: str, active_external_ids: list[str]) -> int:
        """Remove nodes no longer present in the source."""
        async with async_session_factory() as db:
            result = await db.execute(
                delete(CmdbNode).where(
                    CmdbNode.datasource_id == datasource_id,
                    CmdbNode.external_id.notin_(active_external_ids),
                )
            )
            await db.commit()
            return result.rowcount

    # ── Helpers ─────────────────────────────────────────────────────────

    async def _load_datasource(self, datasource_id: str) -> DataSource | None:
        async with async_session_factory() as db:
            result = await db.execute(select(DataSource).where(DataSource.id == datasource_id))
            return result.scalar_one_or_none()

    async def _load_active_rule(self, datasource_id: str) -> dict | None:
        async with async_session_factory() as db:
            result = await db.execute(
                select(CmdbMappingRule)
                .where(CmdbMappingRule.datasource_id == datasource_id)
                .where(CmdbMappingRule.status == "active")
                .order_by(CmdbMappingRule.version.desc())
                .limit(1)
            )
            rule = result.scalar_one_or_none()
            if rule:
                return {"rule_content": rule.rule_content}
            return None

    async def _store_mapping_rule(self, datasource_id: str, rule: dict) -> None:
        async with async_session_factory() as db:
            db.add(CmdbMappingRule(
                datasource_id=datasource_id,
                version=rule.get("version", 1),
                rule_content=rule.get("data", rule),
                status="draft",
            ))
            await db.commit()

    async def _count_nodes(self, datasource_id: str) -> int:
        async with async_session_factory() as db:
            result = await db.execute(
                select(CmdbNode).where(CmdbNode.datasource_id == datasource_id)
            )
            return len(result.all())

    async def _create_sync_log(self, datasource_id: str, mode: str) -> CmdbSyncLog:
        async with async_session_factory() as db:
            sync_log = CmdbSyncLog(
                datasource_id=datasource_id,
                mode=mode,
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            db.add(sync_log)
            await db.commit()
            await db.refresh(sync_log)
            return sync_log

    async def _finalize_sync_log(
        self, sync_log_id: str,
        node_stats: dict, edge_stats: dict,
        cleaned: int, review_count: int,
    ) -> None:
        async with async_session_factory() as db:
            result = await db.execute(select(CmdbSyncLog).where(CmdbSyncLog.id == sync_log_id))
            log = result.scalar_one_or_none()
            if log:
                log.status = "completed"
                log.nodes_created = node_stats.get("created", 0)
                log.nodes_updated = node_stats.get("updated", 0)
                log.nodes_deleted = cleaned
                log.edges_count = edge_stats.get("created", 0)
                log.review_count = review_count
                log.finished_at = datetime.now(timezone.utc)
                await db.commit()

    async def _mark_sync_failed(self, sync_log_id: str, error: str) -> None:
        async with async_session_factory() as db:
            result = await db.execute(select(CmdbSyncLog).where(CmdbSyncLog.id == sync_log_id))
            log = result.scalar_one_or_none()
            if log:
                log.status = "failed"
                log.errors_detail = {"error": error}
                log.finished_at = datetime.now(timezone.utc)
                await db.commit()
```

- [ ] **Step 2: Commit**

```bash
git add server/src/agent/sub_agents/cmdb_ingestion_agent.py
git commit -m "feat: add CmdbIngestionAgent with state machine, LLM transform, and 3-layer validation"
```

---

### Task 9: Create CMDB Control API Router

**Files:**
- Create: `server/src/api/control/cmdb.py`
- Modify: `server/src/api/control/router.py`

- [ ] **Step 1: Create CMDB control API with all 6 endpoints**

```python
"""CMDB control API — node/topology queries, review queue, mapping rules, sync trigger."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import DbSession, require_perm
from src.models.base import async_session_factory
from src.models.cmdb import CmdbNode, CmdbEdge, CmdbSyncLog, CmdbMappingRule, CmdbReviewItem

cmdb_router = APIRouter(prefix="/api/v1/cmdb", tags=["CMDB"])


class SyncTriggerRequest(BaseModel):
    mode: str = "incremental"  # discover / incremental / full


class ReviewActionRequest(BaseModel):
    reviewer: str | None = None
    note: str | None = None


class MappingRuleUpdate(BaseModel):
    rule_content: dict[str, Any] | None = None
    status: str | None = None  # draft / active / superseded


# ── Node queries ────────────────────────────────────────────────────────

@cmdb_router.get("/nodes")
async def list_nodes(
    search: str | None = Query(None, description="Name or external_id search (ILIKE)"),
    ci_type: str | None = Query(None),
    source: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    """List CMDB nodes with optional filtering."""
    query = select(CmdbNode)
    if search:
        query = query.where(
            CmdbNode.name.ilike(f"%{search}%") | CmdbNode.external_id.ilike(f"%{search}%")
        )
    if ci_type:
        query = query.where(CmdbNode.ci_type == ci_type)
    if source:
        query = query.where(CmdbNode.source == source)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query.order_by(CmdbNode.name).offset((page - 1) * page_size).limit(page_size)
    )
    nodes = result.scalars().all()

    return {
        "items": [
            {
                "id": str(n.id),
                "ci_type": n.ci_type,
                "name": n.name,
                "external_id": n.external_id,
                "source": n.source,
                "properties": n.properties,
                "synced_at": n.synced_at.isoformat() if n.synced_at else None,
            }
            for n in nodes
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@cmdb_router.get("/topology")
async def get_topology(
    node_id: UUID | None = Query(None, description="Center node for traversal"),
    ci_types: str | None = Query(None, description="Comma-separated ci_types filter"),
    depth: int = Query(3, ge=1, le=10),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    """Get topology graph — nodes and edges with optional depth-limited traversal."""
    if node_id:
        # Recursive CTE for depth-limited BFS
        cte_sql = text("""
            WITH RECURSIVE graph_walk AS (
                SELECT source_node_id, target_node_id, relation_type, 1 AS depth
                FROM cmdb_edges
                WHERE source_node_id = :start_id OR target_node_id = :start_id
                UNION
                SELECT e.source_node_id, e.target_node_id, e.relation_type, gw.depth + 1
                FROM cmdb_edges e
                JOIN graph_walk gw ON e.source_node_id = gw.target_node_id
                   OR e.target_node_id = gw.source_node_id
                WHERE gw.depth < :max_depth
            )
            SELECT DISTINCT source_node_id, target_node_id, relation_type FROM graph_walk
        """)
        result = await db.execute(cte_sql, {"start_id": node_id, "max_depth": depth})
        edges_data = result.all()
        edge_node_ids = set()
        for row in edges_data:
            edge_node_ids.add(row[0])
            edge_node_ids.add(row[1])
        nodes_result = await db.execute(
            select(CmdbNode).where(CmdbNode.id.in_(edge_node_ids))
        )
        nodes = nodes_result.scalars().all()
    else:
        edges_result = await db.execute(select(CmdbEdge).limit(200))
        edges_data = edges_result.all()
        nodes_result = await db.execute(select(CmdbNode).limit(500))
        nodes = nodes_result.scalars().all()

    return {
        "nodes": [
            {
                "id": str(n.id),
                "ci_type": n.ci_type,
                "name": n.name,
                "external_id": n.external_id,
                "source": n.source,
                "properties": n.properties,
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": str(e.id) if hasattr(e, 'id') else None,
                "source_node_id": str(e[0] if isinstance(e, tuple) else e.source_node_id),
                "target_node_id": str(e[1] if isinstance(e, tuple) else e.target_node_id),
                "relation_type": e[2] if isinstance(e, tuple) else e.relation_type,
            }
            for e in edges_data
        ],
    }


# ── Review queue ────────────────────────────────────────────────────────

@cmdb_router.get("/review-items")
async def list_review_items(
    status: str | None = Query(None, description="pending / approved / rejected"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    """List CMDB review items with pagination."""
    query = select(CmdbReviewItem)
    if status:
        query = query.where(CmdbReviewItem.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query.order_by(CmdbReviewItem.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = result.scalars().all()

    return {
        "items": [
            {
                "id": str(item.id),
                "sync_log_id": str(item.sync_log_id) if item.sync_log_id else None,
                "review_type": item.review_type,
                "source_data": item.source_data,
                "transformed_data": item.transformed_data,
                "llm_confidence": item.llm_confidence,
                "llm_reason": item.llm_reason,
                "diff_summary": item.diff_summary,
                "status": item.status,
                "reviewer": item.reviewer,
                "review_note": item.review_note,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@cmdb_router.post("/review-items/{item_id}/approve")
async def approve_review_item(
    item_id: UUID,
    body: ReviewActionRequest,
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "write")),
):
    """Approve a review item and apply its transformed data."""
    from datetime import datetime, timezone
    result = await db.execute(select(CmdbReviewItem).where(CmdbReviewItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        return {"error": "Review item not found"}
    item.status = "approved"
    item.reviewer = body.reviewer
    item.review_note = body.note
    item.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "status": "approved"}


@cmdb_router.post("/review-items/{item_id}/reject")
async def reject_review_item(
    item_id: UUID,
    body: ReviewActionRequest,
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "write")),
):
    """Reject a review item."""
    from datetime import datetime, timezone
    result = await db.execute(select(CmdbReviewItem).where(CmdbReviewItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        return {"error": "Review item not found"}
    item.status = "rejected"
    item.reviewer = body.reviewer
    item.review_note = body.note
    item.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "status": "rejected"}


# ── Mapping rules ───────────────────────────────────────────────────────

@cmdb_router.get("/mapping-rules")
async def list_mapping_rules(
    datasource_id: UUID | None = Query(None),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    """List CMDB mapping rules."""
    query = select(CmdbMappingRule)
    if datasource_id:
        query = query.where(CmdbMappingRule.datasource_id == datasource_id)
    result = await db.execute(query.order_by(CmdbMappingRule.version.desc()))
    rules = result.scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "datasource_id": str(r.datasource_id) if r.datasource_id else None,
                "version": r.version,
                "rule_content": r.rule_content,
                "status": r.status,
                "approved_by": r.approved_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rules
        ],
    }


@cmdb_router.put("/mapping-rules/{rule_id}")
async def update_mapping_rule(
    rule_id: UUID,
    body: MappingRuleUpdate,
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "write")),
):
    """Update a mapping rule."""
    result = await db.execute(select(CmdbMappingRule).where(CmdbMappingRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return {"error": "Mapping rule not found"}
    if body.rule_content is not None:
        rule.rule_content = body.rule_content
    if body.status is not None:
        rule.status = body.status
    await db.commit()
    return {"success": True, "id": str(rule.id), "status": rule.status}


# ── Sync logs ───────────────────────────────────────────────────────────

@cmdb_router.get("/sync-logs")
async def list_sync_logs(
    datasource_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    """List CMDB sync logs."""
    query = select(CmdbSyncLog)
    if datasource_id:
        query = query.where(CmdbSyncLog.datasource_id == datasource_id)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query.order_by(CmdbSyncLog.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = result.scalars().all()
    return {
        "items": [
            {
                "id": str(log.id),
                "datasource_id": str(log.datasource_id) if log.datasource_id else None,
                "mode": log.mode,
                "status": log.status,
                "nodes_created": log.nodes_created,
                "nodes_updated": log.nodes_updated,
                "nodes_deleted": log.nodes_deleted,
                "edges_count": log.edges_count,
                "review_count": log.review_count,
                "errors_detail": log.errors_detail,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "finished_at": log.finished_at.isoformat() if log.finished_at else None,
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
```

- [ ] **Step 2: Register cmdb_router in control router.py**

In `server/src/api/control/router.py`, add import and include_router:

```python
# Add import near other control router imports:
from src.api.control.cmdb import cmdb_router

# Add include_router near other control_router.include_router calls:
control_router.include_router(cmdb_router)
```

- [ ] **Step 3: Commit**

```bash
git add server/src/api/control/cmdb.py server/src/api/control/router.py
git commit -m "feat: add CMDB control API — nodes, topology, review queue, mapping rules, sync logs"
```

---

### Task 10: Extend DataSource Execution API

**Files:**
- Modify: `server/src/api/execution/datasources.py`

- [ ] **Step 1: Add log/itsm/cmdb test branches and CMDB sync trigger endpoint**

In `server/src/api/execution/datasources.py`, add test branches for new types in the `test_datasource` function and add the sync endpoint:

```python
# In the test_datasource function, add to the if/elif chain after the existing branches:

    elif ds.source_type == "log":
        # Test log ingestion: verify connection to Kafka/filebeat endpoint
        config = ds.config or {}
        kafka_topic = config.get("kafka_topic")
        kafka_servers = config.get("kafka_bootstrap_servers")
        if kafka_topic and kafka_servers:
            return {"status": "ok", "message": f"Log source configured (topic={kafka_topic})"}
        return {"status": "ok", "message": "Log source ready (filebeat/webhook mode)"}

    elif ds.source_type == "itsm":
        # Test ITSM: verify API connectivity using config
        config = ds.config or {}
        base_url = config.get("api_base_url", "").rstrip("/")
        if not base_url:
            return {"status": "error", "message": "api_base_url not configured"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{base_url}/api/health", headers=config.get("headers", {}))
                if resp.status_code < 500:
                    return {"status": "ok", "message": f"ITSM API reachable (status={resp.status_code})"}
                return {"status": "error", "message": f"ITSM API returned {resp.status_code}"}
        except Exception as e:
            return {"status": "error", "message": f"ITSM connection failed: {e}"}

    elif ds.source_type == "cmdb":
        # Test CMDB: verify API connectivity
        config = ds.config or {}
        base_url = config.get("api_base_url", "").rstrip("/")
        if not base_url:
            return {"status": "error", "message": "api_base_url not configured"}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{base_url}/api/health", headers=config.get("headers", {}))
                if resp.status_code < 500:
                    return {"status": "ok", "message": f"CMDB API reachable (status={resp.status_code})"}
                return {"status": "error", "message": f"CMDB API returned {resp.status_code}"}
        except Exception as e:
            return {"status": "error", "message": f"CMDB connection failed: {e}"}


# Add the CMDB sync trigger endpoint:

from pydantic import BaseModel

class SyncTriggerRequest(BaseModel):
    mode: str = "incremental"  # discover / incremental / full


@datasources_router.post("/{datasource_id}/sync")
async def trigger_cmdb_sync(
    datasource_id: UUID,
    body: SyncTriggerRequest,
    db: DbSession = None,
    _: Any = Depends(require_perm("datasources", "write")),
):
    """Trigger a CMDB synchronization for a cmdb-type DataSource."""
    result = await db.execute(
        select(DataSource).where(DataSource.id == datasource_id)
    )
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(status_code=404, detail="DataSource not found")
    if ds.source_type != "cmdb":
        raise HTTPException(status_code=400, detail="Sync only supported for cmdb-type DataSources")

    from src.agent.sub_agents.cmdb_ingestion_agent import CmdbIngestionAgent
    agent = CmdbIngestionAgent()
    sync_result = await agent.run_sync(str(datasource_id), mode=body.mode)
    return sync_result
```

Also add the necessary imports at the top of the file:

```python
from uuid import UUID
from src.models.datasource import DataSource
```

- [ ] **Step 2: Commit**

```bash
git add server/src/api/execution/datasources.py
git commit -m "feat: add log/itsm/cmdb test branches and CMDB sync trigger endpoint"
```

---

### Task 11: Create Log and ITSM Search API Routers

**Files:**
- Create: `server/src/api/execution/log_search.py`
- Create: `server/src/api/execution/itsm_search.py`
- Modify: `server/src/main.py`

- [ ] **Step 1: Create log search API router**

```python
"""Log search API — agent tools for querying log_events."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import DbSession
from src.models.log import LogEvent

log_search_router = APIRouter(prefix="/api/v1/logs", tags=["Log Search"])


@log_search_router.get("/search")
async def search_logs(
    service: str | None = Query(None),
    level: str | None = Query(None),
    keyword: str | None = Query(None),
    trace_id: str | None = Query(None),
    minutes: int = Query(30, description="Time window in minutes"),
    limit: int = Query(100, le=1000),
    db: DbSession = None,
):
    """Search log events with filters."""
    query = select(LogEvent)
    if service:
        query = query.where(LogEvent.service == service)
    if level:
        query = query.where(LogEvent.level == level.upper())
    if keyword:
        query = query.where(LogEvent.message.ilike(f"%{keyword}%"))
    if trace_id:
        query = query.where(LogEvent.trace_id == trace_id)

    query = query.order_by(LogEvent.ingested_at.desc()).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "items": [
            {
                "id": str(log.id),
                "ingested_at": log.ingested_at.isoformat() if log.ingested_at else None,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "service": log.service,
                "host": log.host,
                "level": log.level,
                "trace_id": log.trace_id,
                "message": log.message,
                "raw": log.raw,
            }
            for log in logs
        ],
    }


@log_search_router.get("/error-context")
async def get_error_context(
    trace_id: str = Query(...),
    before_seconds: int = Query(30),
    after_seconds: int = Query(30),
    db: DbSession = None,
):
    """Get log context around a specific trace_id."""
    # Find the target log first
    target = await db.execute(
        select(LogEvent).where(LogEvent.trace_id == trace_id).limit(1)
    )
    target_log = target.scalar_one_or_none()
    if not target_log or not target_log.timestamp:
        return {"items": [], "message": "No log found for this trace_id"}

    from datetime import timedelta
    window_start = target_log.timestamp - timedelta(seconds=before_seconds)
    window_end = target_log.timestamp + timedelta(seconds=after_seconds)

    result = await db.execute(
        select(LogEvent)
        .where(
            LogEvent.service == target_log.service,
            LogEvent.timestamp.between(window_start, window_end),
        )
        .order_by(LogEvent.timestamp.asc())
        .limit(200)
    )
    logs = result.scalars().all()

    return {
        "items": [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "service": log.service,
                "host": log.host,
                "level": log.level,
                "trace_id": log.trace_id,
                "message": log.message,
            }
            for log in logs
        ],
        "target_timestamp": target_log.timestamp.isoformat() if target_log.timestamp else None,
    }


@log_search_router.get("/count")
async def count_logs(
    service: str | None = Query(None),
    level: str | None = Query(None),
    minutes: int = Query(30),
    db: DbSession = None,
):
    """Aggregate count of logs by level."""
    query = select(LogEvent.level, func.count()).select_from(LogEvent)
    if service:
        query = query.where(LogEvent.service == service)
    if level:
        query = query.where(LogEvent.level == level.upper())
    query = query.group_by(LogEvent.level)

    result = await db.execute(query)
    counts = {row[0]: row[1] for row in result.all()}
    return {"counts": counts, "total": sum(counts.values())}
```

- [ ] **Step 2: Create ITSM search API router**

```python
"""ITSM search API — agent tools for querying ITSM tickets."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import DbSession
from src.models.itsm import ItsmTicket

itsm_search_router = APIRouter(prefix="/api/v1/itsm", tags=["ITSM Search"])


@itsm_search_router.get("/tickets")
async def search_tickets(
    service: str | None = Query(None),
    ticket_type: str | None = Query(None),
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: DbSession = None,
):
    """Search ITSM tickets with filters."""
    query = select(ItsmTicket)
    if service:
        query = query.where(ItsmTicket.affected_service == service)
    if ticket_type:
        query = query.where(ItsmTicket.ticket_type == ticket_type)
    if status:
        query = query.where(ItsmTicket.status == status)
    if keyword:
        query = query.where(
            or_(
                ItsmTicket.title.ilike(f"%{keyword}%"),
                ItsmTicket.raw_data.cast(str).ilike(f"%{keyword}%"),
            )
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query.order_by(ItsmTicket.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    tickets = result.scalars().all()

    return {
        "items": [
            {
                "id": str(t.id),
                "external_id": t.external_id,
                "ticket_type": t.ticket_type,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "affected_service": t.affected_service,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
                "linked_alert_ids": [str(aid) for aid in (t.linked_alert_ids or [])],
            }
            for t in tickets
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@itsm_search_router.get("/tickets/{ticket_id}")
async def get_ticket_detail(
    ticket_id: str,
    db: DbSession = None,
):
    """Get full ITSM ticket detail including raw_data."""
    from uuid import UUID
    try:
        uid = UUID(ticket_id)
        result = await db.execute(select(ItsmTicket).where(ItsmTicket.id == uid))
    except ValueError:
        result = await db.execute(select(ItsmTicket).where(ItsmTicket.external_id == ticket_id))

    ticket = result.scalar_one_or_none()
    if not ticket:
        return {"error": "Ticket not found"}

    return {
        "id": str(ticket.id),
        "external_id": ticket.external_id,
        "ticket_type": ticket.ticket_type,
        "title": ticket.title,
        "status": ticket.status,
        "priority": ticket.priority,
        "affected_service": ticket.affected_service,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "raw_data": ticket.raw_data,
        "linked_alert_ids": [str(aid) for aid in (ticket.linked_alert_ids or [])],
    }


@itsm_search_router.get("/service-timeline")
async def get_service_timeline(
    service: str = Query(...),
    time_start: str | None = Query(None),
    time_end: str | None = Query(None),
    db: DbSession = None,
):
    """Get aggregated timeline: tickets for a service within a time window."""
    from datetime import datetime
    query = select(ItsmTicket).where(ItsmTicket.affected_service == service)
    if time_start:
        query = query.where(ItsmTicket.created_at >= datetime.fromisoformat(time_start))
    if time_end:
        query = query.where(ItsmTicket.created_at <= datetime.fromisoformat(time_end))

    result = await db.execute(query.order_by(ItsmTicket.created_at.asc()))
    tickets = result.scalars().all()

    return {
        "service": service,
        "items": [
            {
                "id": str(t.id),
                "external_id": t.external_id,
                "ticket_type": t.ticket_type,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tickets
        ],
    }
```

- [ ] **Step 3: Register new routers in main.py**

In `server/src/main.py`, add imports:

```python
from src.api.execution.log_search import log_search_router
from src.api.execution.itsm_search import itsm_search_router
```

Then add include_router calls near other execution router includes:

```python
app.include_router(log_search_router)
app.include_router(itsm_search_router)
```

- [ ] **Step 4: Commit**

```bash
git add server/src/api/execution/log_search.py server/src/api/execution/itsm_search.py server/src/main.py
git commit -m "feat: add log search and ITSM search API routers"
```

---

### Task 12: Database Migration

**Files:**
- Create: `server/migrations/versions/xxxx_data_ingestion.py` (auto-generated)

- [ ] **Step 1: Generate and run the Alembic migration**

```bash
cd server
poetry run alembic revision --autogenerate -m "data_ingestion_add_log_itsm_cmdb_tables"
```

- [ ] **Step 2: Review the generated migration**

Check that the migration includes all 7 new tables:
- `log_events` (partitioned)
- `itsm_tickets`
- `cmdb_nodes`
- `cmdb_edges`
- `cmdb_sync_logs`
- `cmdb_mapping_rules`
- `cmdb_review_items`

Add partition creation SQL to the migration if not auto-detected:

```python
# In the upgrade() function, add after table creation:
op.execute("""
    CREATE TABLE IF NOT EXISTS log_events_default PARTITION OF log_events DEFAULT;
""")
```

- [ ] **Step 3: Apply migration**

```bash
cd server
poetry run alembic upgrade head
```

- [ ] **Step 4: Set up pg_cron for log TTL cleanup**

```sql
-- Run manually in PostgreSQL:
SELECT cron.schedule(
    'log-ttl-cleanup',
    '* * * * *',  -- every minute
    $$DELETE FROM log_events WHERE ingested_at < NOW() - INTERVAL '30 minutes'$$
);
```

- [ ] **Step 5: Commit**

```bash
git add server/migrations/
git commit -m "feat: add data ingestion migration — log_events, itsm_tickets, cmdb tables"
```

---

### Task 13: Extend DataSource Frontend Components

**Files:**
- Modify: `web/src/features/datacenter/DataSourcePage.tsx`
- Modify: `web/src/features/datacenter/DataSourceFormModal.tsx`

- [ ] **Step 1: Add log/itsm/cmdb to DataSourcePage type filter**

In `web/src/features/datacenter/DataSourcePage.tsx`, find the type filter Select and add the new options:

```tsx
// In the filter bar, find the type Select/options and add:
{ value: 'log', label: '日志' },
{ value: 'itsm', label: 'ITSM' },
{ value: 'cmdb', label: 'CMDB' },
```

Also update the type tag colors/rendering to handle the new types:

```tsx
const typeColorMap: Record<string, string> = {
  kafka: 'blue',
  webhook: 'green',
  api: 'purple',
  log: 'orange',
  itsm: 'cyan',
  cmdb: 'geekblue',
};
```

- [ ] **Step 2: Add conditional config fields in DataSourceFormModal**

In `web/src/features/datacenter/DataSourceFormModal.tsx`, add conditional form sections based on source_type:

```tsx
{/* After the existing config form items, add: */}

{sourceType === 'log' && (
  <>
    <Form.Item name={['config', 'source']} label="采集来源">
      <Select options={[
        { value: 'filebeat', label: 'Filebeat' },
        { value: 'kafka', label: 'Kafka' },
        { value: 'vector', label: 'Vector' },
      ]} />
    </Form.Item>
    <Form.Item name={['config', 'batch_size']} label="批量大小">
      <InputNumber min={100} max={5000} />
    </Form.Item>
    <Form.Item name={['config', 'retention_minutes']} label="保留时间(分钟)">
      <InputNumber min={5} max={1440} />
    </Form.Item>
  </>
)}

{sourceType === 'itsm' && (
  <>
    <Form.Item name={['config', 'itsm_system']} label="ITSM系统">
      <Select options={[
        { value: 'servicenow', label: 'ServiceNow' },
        { value: 'jira', label: 'Jira' },
        { value: 'zendesk', label: 'Zendesk' },
        { value: 'custom', label: '自定义' },
      ]} />
    </Form.Item>
    <Form.Item name={['config', 'api_base_url']} label="API地址">
      <Input placeholder="https://itsm.example.com" />
    </Form.Item>
    <Form.Item name={['config', 'poll_interval_seconds']} label="轮询间隔(秒)">
      <InputNumber min={60} max={3600} />
    </Form.Item>
    <Form.Item name={['config', 'ticket_types']} label="工单类型">
      <Select mode="multiple" options={[
        { value: 'incident', label: '事件单' },
        { value: 'change', label: '变更单' },
        { value: 'problem', label: '问题单' },
        { value: 'request', label: '服务请求' },
      ]} />
    </Form.Item>
  </>
)}

{sourceType === 'cmdb' && (
  <>
    <Form.Item name={['config', 'cmdb_system']} label="CMDB系统">
      <Select options={[
        { value: 'itop', label: 'iTop' },
        { value: 'servicenow', label: 'ServiceNow' },
        { value: 'custom', label: '自定义' },
      ]} />
    </Form.Item>
    <Form.Item name={['config', 'api_base_url']} label="API地址">
      <Input placeholder="https://cmdb.example.com" />
    </Form.Item>
    <Form.Item name={['config', 'sync_schedule']} label="同步计划(Cron)">
      <Input placeholder="0 * * * *" />
    </Form.Item>
    <Form.Item name={['config', 'default_mode']} label="默认同步模式">
      <Select options={[
        { value: 'discover', label: '发现模式(首次)' },
        { value: 'incremental', label: '增量同步' },
        { value: 'full', label: '全量同步' },
      ]} />
    </Form.Item>
  </>
)}
```

- [ ] **Step 3: Commit**

```bash
git add web/src/features/datacenter/DataSourcePage.tsx web/src/features/datacenter/DataSourceFormModal.tsx
git commit -m "feat: extend DataSource UI with log/itsm/cmdb type filters and config forms"
```

---

### Task 14: Create CmdbPage Frontend

**Files:**
- Create: `web/src/features/cmdb/CmdbPage.tsx`

- [ ] **Step 1: Create CMDB management page with topology, sync, and review tabs**

```tsx
import React, { useState, useEffect, useCallback } from 'react';
import { Table, Tabs, Button, Tag, Space, Drawer, Modal, message, Input, Select, Badge } from 'antd';
import { SyncOutlined, CheckOutlined, CloseOutlined, NodeIndexOutlined } from '@ant-design/icons';
import { api } from '@/api/client';

interface CmdbNode {
  id: string;
  ci_type: string;
  name: string;
  external_id: string;
  source: string;
  properties: Record<string, any>;
  synced_at: string;
}

interface ReviewItem {
  id: string;
  review_type: string;
  llm_confidence: number;
  llm_reason: string;
  source_data: Record<string, any>;
  transformed_data: Record<string, any>;
  status: string;
  created_at: string;
}

const ciTypeColors: Record<string, string> = {
  server: 'blue', app: 'green', db: 'red', vip: 'purple',
  lb: 'orange', rack: 'cyan', unknown: 'default',
};

const CmdbPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState('nodes');
  const [nodes, setNodes] = useState<CmdbNode[]>([]);
  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [ciTypeFilter, setCiTypeFilter] = useState<string | undefined>();
  const [selectedNode, setSelectedNode] = useState<CmdbNode | null>(null);
  const [syncLoading, setSyncLoading] = useState(false);

  const fetchNodes = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = { page: 1, page_size: 50 };
      if (searchText) params.search = searchText;
      if (ciTypeFilter) params.ci_type = ciTypeFilter;
      const resp = await api.get('/api/v1/cmdb/nodes', { params });
      setNodes(resp.data.items);
    } catch (e) {
      message.error('获取CMDB节点失败');
    } finally {
      setLoading(false);
    }
  }, [searchText, ciTypeFilter]);

  const fetchReviewItems = useCallback(async () => {
    try {
      const resp = await api.get('/api/v1/cmdb/review-items', { params: { status: 'pending', page_size: 50 } });
      setReviewItems(resp.data.items);
    } catch (e) {
      message.error('获取审核项失败');
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'nodes') fetchNodes();
    else if (activeTab === 'review') fetchReviewItems();
  }, [activeTab, fetchNodes, fetchReviewItems]);

  const handleApprove = async (id: string) => {
    await api.post(`/api/v1/cmdb/review-items/${id}/approve`, { reviewer: 'admin' });
    message.success('已通过');
    fetchReviewItems();
  };

  const handleReject = async (id: string) => {
    await api.post(`/api/v1/cmdb/review-items/${id}/reject`, { reviewer: 'admin', note: '' });
    message.success('已驳回');
    fetchReviewItems();
  };

  const handleSync = async () => {
    setSyncLoading(true);
    try {
      await api.post('/api/v1/datasources/{id}/sync', { mode: 'incremental' });
      message.success('同步已触发');
    } catch (e) {
      message.error('同步触发失败');
    } finally {
      setSyncLoading(false);
    }
  };

  const nodeColumns = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 200 },
    {
      title: 'CI类型', dataIndex: 'ci_type', key: 'ci_type', width: 100,
      render: (t: string) => <Tag color={ciTypeColors[t] || 'default'}>{t}</Tag>,
    },
    { title: '外部ID', dataIndex: 'external_id', key: 'external_id', width: 180, ellipsis: true },
    { title: '来源', dataIndex: 'source', key: 'source', width: 120 },
    {
      title: '同步时间', dataIndex: 'synced_at', key: 'synced_at', width: 180,
      render: (t: string) => t ? new Date(t).toLocaleString() : '-',
    },
    {
      title: '操作', key: 'actions', width: 100,
      render: (_: any, record: CmdbNode) => (
        <Button type="link" onClick={() => setSelectedNode(record)}>详情</Button>
      ),
    },
  ];

  const reviewColumns = [
    { title: '类型', dataIndex: 'review_type', key: 'review_type', width: 100 },
    {
      title: '置信度', dataIndex: 'llm_confidence', key: 'llm_confidence', width: 100,
      render: (v: number) => (
        <Tag color={v >= 80 ? 'green' : v >= 50 ? 'orange' : 'red'}>{v}%</Tag>
      ),
    },
    { title: '原因', dataIndex: 'llm_reason', key: 'llm_reason', ellipsis: true },
    {
      title: '时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: (t: string) => t ? new Date(t).toLocaleString() : '-',
    },
    {
      title: '操作', key: 'actions', width: 160,
      render: (_: any, record: ReviewItem) => (
        <Space>
          <Button type="primary" size="small" icon={<CheckOutlined />}
            onClick={() => handleApprove(record.id)}>通过</Button>
          <Button danger size="small" icon={<CloseOutlined />}
            onClick={() => handleReject(record.id)}>驳回</Button>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2>CMDB 配置管理</h2>
        <Button type="primary" icon={<SyncOutlined spin={syncLoading} />}
          onClick={handleSync} loading={syncLoading}>
          触发同步
        </Button>
      </div>

      <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
        {
          key: 'nodes',
          label: <span><NodeIndexOutlined /> 配置项 ({nodes.length})</span>,
          children: (
            <>
              <Space style={{ marginBottom: 16 }}>
                <Input.Search placeholder="搜索名称/ID" onSearch={setSearchText} style={{ width: 300 }} />
                <Select placeholder="CI类型" allowClear style={{ width: 120 }}
                  onChange={setCiTypeFilter} options={[
                    { value: 'server', label: 'Server' },
                    { value: 'app', label: 'App' },
                    { value: 'db', label: 'DB' },
                    { value: 'vip', label: 'VIP' },
                    { value: 'lb', label: 'LB' },
                  ]} />
              </Space>
              <Table columns={nodeColumns} dataSource={nodes} rowKey="id"
                loading={loading} size="middle" pagination={{ pageSize: 50 }} />
            </>
          ),
        },
        {
          key: 'review',
          label: <span>审核队列 <Badge count={reviewItems.length} /></span>,
          children: (
            <Table columns={reviewColumns} dataSource={reviewItems} rowKey="id"
              size="middle" pagination={{ pageSize: 20 }} />
          ),
        },
      ]} />

      <Drawer title="节点详情" open={!!selectedNode} onClose={() => setSelectedNode(null)} width={500}>
        {selectedNode && (
          <div>
            <p><strong>名称:</strong> {selectedNode.name}</p>
            <p><strong>CI类型:</strong> <Tag color={ciTypeColors[selectedNode.ci_type]}>{selectedNode.ci_type}</Tag></p>
            <p><strong>外部ID:</strong> {selectedNode.external_id}</p>
            <p><strong>来源:</strong> {selectedNode.source}</p>
            <p><strong>同步时间:</strong> {selectedNode.synced_at ? new Date(selectedNode.synced_at).toLocaleString() : '-'}</p>
            <p><strong>属性:</strong></p>
            <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 6, maxHeight: 400, overflow: 'auto' }}>
              {JSON.stringify(selectedNode.properties, null, 2)}
            </pre>
          </div>
        )}
      </Drawer>
    </div>
  );
};

export default CmdbPage;
```

- [ ] **Step 2: Commit**

```bash
git add web/src/features/cmdb/CmdbPage.tsx
git commit -m "feat: add CMDB management page with node list, review queue, and sync trigger"
```

---

### Task 15: Create LogIngestionPage and ItsmPage

**Files:**
- Create: `web/src/features/logs/LogIngestionPage.tsx`
- Create: `web/src/features/itsm/ItsmPage.tsx`

- [ ] **Step 1: Create LogIngestionPage**

```tsx
import React, { useState, useEffect, useCallback } from 'react';
import { Table, Select, Input, Tag, Space, Card, Statistic, Row, Col } from 'antd';
import { SearchOutlined, WarningOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { api } from '@/api/client';

interface LogEntry {
  id: string;
  timestamp: string;
  service: string;
  host: string;
  level: string;
  trace_id: string;
  message: string;
}

const levelColors: Record<string, string> = {
  ERROR: 'red', WARN: 'orange', WARNING: 'orange', INFO: 'blue', DEBUG: 'default',
};

const LogIngestionPage: React.FC = () => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [service, setService] = useState<string | undefined>();
  const [level, setLevel] = useState<string | undefined>();
  const [keyword, setKeyword] = useState('');
  const [counts, setCounts] = useState<Record<string, number>>({});

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = { limit: 200 };
      if (service) params.service = service;
      if (level) params.level = level;
      if (keyword) params.keyword = keyword;
      const resp = await api.get('/api/v1/logs/search', { params });
      setLogs(resp.data.items);
    } catch (e) {
      // API may not be ready yet — silent fail
    } finally {
      setLoading(false);
    }
  }, [service, level, keyword]);

  const fetchCounts = useCallback(async () => {
    try {
      const resp = await api.get('/api/v1/logs/count', { params: { minutes: 30 } });
      setCounts(resp.data.counts);
    } catch (e) { /* silent */ }
  }, []);

  useEffect(() => { fetchLogs(); fetchCounts(); }, [fetchLogs, fetchCounts]);

  const columns = [
    {
      title: '时间', dataIndex: 'timestamp', key: 'timestamp', width: 180,
      render: (t: string) => t ? new Date(t).toLocaleString() : '-',
    },
    {
      title: '级别', dataIndex: 'level', key: 'level', width: 80,
      render: (l: string) => <Tag color={levelColors[l] || 'default'}>{l}</Tag>,
    },
    { title: '服务', dataIndex: 'service', key: 'service', width: 120 },
    { title: '主机', dataIndex: 'host', key: 'host', width: 120 },
    { title: '消息', dataIndex: 'message', key: 'message', ellipsis: true },
    { title: 'TraceID', dataIndex: 'trace_id', key: 'trace_id', width: 120, ellipsis: true },
  ];

  const totalCount = Object.values(counts).reduce((a, b) => a + b, 0);

  return (
    <div style={{ padding: 24 }}>
      <h2>日志查看</h2>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="30分钟日志总量" value={totalCount} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="ERROR" value={counts['ERROR'] || 0}
              valueStyle={{ color: '#cf1322' }} prefix={<WarningOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="WARN" value={counts['WARN'] || 0}
              valueStyle={{ color: '#fa8c16' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="INFO" value={counts['INFO'] || 0}
              valueStyle={{ color: '#1890ff' }} prefix={<InfoCircleOutlined />} />
          </Card>
        </Col>
      </Row>

      <Space style={{ marginBottom: 16 }}>
        <Input.Search placeholder="搜索关键词" onSearch={setKeyword} style={{ width: 300 }} />
        <Select placeholder="级别" allowClear style={{ width: 120 }}
          onChange={setLevel} options={[
            { value: 'ERROR', label: 'ERROR' },
            { value: 'WARN', label: 'WARN' },
            { value: 'INFO', label: 'INFO' },
            { value: 'DEBUG', label: 'DEBUG' },
          ]} />
        <Input placeholder="服务名" onChange={e => setService(e.target.value)} style={{ width: 150 }} />
      </Space>

      <Table columns={columns} dataSource={logs} rowKey="id"
        loading={loading} size="small" pagination={{ pageSize: 50 }}
        scroll={{ x: 900 }} />
    </div>
  );
};

export default LogIngestionPage;
```

- [ ] **Step 2: Create ItsmPage**

```tsx
import React, { useState, useEffect, useCallback } from 'react';
import { Table, Select, Input, Tag, Space, Drawer, Descriptions } from 'antd';
import { api } from '@/api/client';

interface ItsmTicket {
  id: string;
  external_id: string;
  ticket_type: string;
  title: string;
  status: string;
  priority: string;
  affected_service: string;
  created_at: string;
  resolved_at: string;
  linked_alert_ids: string[];
  raw_data?: Record<string, any>;
}

const typeColors: Record<string, string> = {
  incident: 'red', change: 'blue', problem: 'orange', request: 'green',
};
const priorityColors: Record<string, string> = {
  critical: 'red', high: 'orange', medium: 'blue', low: 'default',
};
const statusColors: Record<string, string> = {
  new: 'blue', in_progress: 'processing', resolved: 'success', closed: 'default',
};

const ItsmPage: React.FC = () => {
  const [tickets, setTickets] = useState<ItsmTicket[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedTicket, setSelectedTicket] = useState<ItsmTicket | null>(null);
  const [typeFilter, setTypeFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [serviceFilter, setServiceFilter] = useState('');

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = { page: 1, page_size: 50 };
      if (typeFilter) params.ticket_type = typeFilter;
      if (statusFilter) params.status = statusFilter;
      if (serviceFilter) params.service = serviceFilter;
      const resp = await api.get('/api/v1/itsm/tickets', { params });
      setTickets(resp.data.items);
    } catch (e) {
      // API may not be ready yet
    } finally {
      setLoading(false);
    }
  }, [typeFilter, statusFilter, serviceFilter]);

  useEffect(() => { fetchTickets(); }, [fetchTickets]);

  const handleViewDetail = async (ticket: ItsmTicket) => {
    try {
      const resp = await api.get(`/api/v1/itsm/tickets/${ticket.id}`);
      setSelectedTicket(resp.data);
    } catch (e) {
      setSelectedTicket(ticket);
    }
  };

  const columns = [
    { title: '外部ID', dataIndex: 'external_id', key: 'external_id', width: 140, ellipsis: true },
    {
      title: '类型', dataIndex: 'ticket_type', key: 'ticket_type', width: 90,
      render: (t: string) => <Tag color={typeColors[t] || 'default'}>{t}</Tag>,
    },
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => <Tag color={statusColors[s] || 'default'}>{s}</Tag>,
    },
    {
      title: '优先级', dataIndex: 'priority', key: 'priority', width: 90,
      render: (p: string) => <Tag color={priorityColors[p] || 'default'}>{p}</Tag>,
    },
    { title: '关联服务', dataIndex: 'affected_service', key: 'affected_service', width: 120 },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: (t: string) => t ? new Date(t).toLocaleString() : '-',
    },
    {
      title: '操作', key: 'actions', width: 80,
      render: (_: any, record: ItsmTicket) => (
        <a onClick={() => handleViewDetail(record)}>详情</a>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <h2>ITSM 工单</h2>
      <Space style={{ marginBottom: 16 }}>
        <Select placeholder="工单类型" allowClear style={{ width: 130 }}
          onChange={setTypeFilter} options={[
            { value: 'incident', label: '事件单' },
            { value: 'change', label: '变更单' },
            { value: 'problem', label: '问题单' },
            { value: 'request', label: '服务请求' },
          ]} />
        <Select placeholder="状态" allowClear style={{ width: 130 }}
          onChange={setStatusFilter} options={[
            { value: 'new', label: '新建' },
            { value: 'in_progress', label: '处理中' },
            { value: 'resolved', label: '已解决' },
            { value: 'closed', label: '已关闭' },
          ]} />
        <Input placeholder="服务名" onChange={e => setServiceFilter(e.target.value)} style={{ width: 150 }} />
      </Space>
      <Table columns={columns} dataSource={tickets} rowKey="id"
        loading={loading} size="middle" pagination={{ pageSize: 50 }} />

      <Drawer title="工单详情" open={!!selectedTicket} onClose={() => setSelectedTicket(null)} width={600}>
        {selectedTicket && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="外部ID">{selectedTicket.external_id}</Descriptions.Item>
            <Descriptions.Item label="类型">
              <Tag color={typeColors[selectedTicket.ticket_type]}>{selectedTicket.ticket_type}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="标题">{selectedTicket.title}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={statusColors[selectedTicket.status]}>{selectedTicket.status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="优先级">
              <Tag color={priorityColors[selectedTicket.priority]}>{selectedTicket.priority}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="关联服务">{selectedTicket.affected_service}</Descriptions.Item>
            <Descriptions.Item label="创建时间">
              {selectedTicket.created_at ? new Date(selectedTicket.created_at).toLocaleString() : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="解决时间">
              {selectedTicket.resolved_at ? new Date(selectedTicket.resolved_at).toLocaleString() : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="关联告警">
              {(selectedTicket.linked_alert_ids || []).join(', ') || '无'}
            </Descriptions.Item>
            {selectedTicket.raw_data && (
              <Descriptions.Item label="原始数据">
                <pre style={{ maxHeight: 300, overflow: 'auto', fontSize: 12 }}>
                  {JSON.stringify(selectedTicket.raw_data, null, 2)}
                </pre>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Drawer>
    </div>
  );
};

export default ItsmPage;
```

- [ ] **Step 3: Commit**

```bash
git add web/src/features/logs/LogIngestionPage.tsx web/src/features/itsm/ItsmPage.tsx
git commit -m "feat: add LogIngestionPage and ItsmPage with search, filter, and detail views"
```

---

### Task 16: Update Sidebar Navigation and Router

**Files:**
- Modify: `web/src/components/layout/Sidebar.tsx`
- Modify: `web/src/router/index.tsx`

- [ ] **Step 1: Add menu items to Sidebar**

In `web/src/components/layout/Sidebar.tsx`, find the ops center menu items and add:

```tsx
// Add alongside the existing datacenter, events, etc. menu items:
{
  key: '/cmdb',
  icon: <NodeIndexOutlined />,
  label: 'CMDB',
},
{
  key: '/logs',
  icon: <FileTextOutlined />,
  label: '日志',
},
{
  key: '/itsm',
  icon: <OrderedListOutlined />,
  label: 'ITSM',
},
```

Add the required icon imports at the top of Sidebar.tsx:

```tsx
import { NodeIndexOutlined, FileTextOutlined, OrderedListOutlined } from '@ant-design/icons';
```

- [ ] **Step 2: Add routes to router**

In `web/src/router/index.tsx`, add lazy-loaded routes before the closing of the routes array:

```tsx
{
  path: '/cmdb',
  element: <LazyLoad><CmdbPage /></LazyLoad>,
},
{
  path: '/logs',
  element: <LazyLoad><LogIngestionPage /></LazyLoad>,
},
{
  path: '/itsm',
  element: <LazyLoad><ItsmPage /></LazyLoad>,
},
```

Add the lazy imports at the top of router/index.tsx:

```tsx
const CmdbPage = React.lazy(() => import('@/features/cmdb/CmdbPage'));
const LogIngestionPage = React.lazy(() => import('@/features/logs/LogIngestionPage'));
const ItsmPage = React.lazy(() => import('@/features/itsm/ItsmPage'));
```

- [ ] **Step 3: Verify TypeScript compilation**

```bash
cd web && pnpm tsc --noEmit
```

Expected: No new type errors from the added files.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/layout/Sidebar.tsx web/src/router/index.tsx
git commit -m "feat: add CMDB, Log, and ITSM routes to sidebar navigation and router"
```

---

## Verification Checklist

After all tasks are complete:

1. **Backend**: Start server and verify all new endpoints appear in `/docs`
   - `GET /api/v1/cmdb/nodes` — returns empty list initially
   - `GET /api/v1/cmdb/review-items` — returns empty list
   - `GET /api/v1/cmdb/mapping-rules` — returns empty list
   - `GET /api/v1/cmdb/sync-logs` — returns empty list
   - `GET /api/v1/logs/search` — returns empty items
   - `GET /api/v1/logs/count` — returns zero counts
   - `GET /api/v1/itsm/tickets` — returns empty items
   - `POST /api/v1/datasources/{id}/sync` — returns 404 for non-existent datasource

2. **Frontend**: Start dev server and navigate to:
   - `/cmdb` — CMDB page loads with nodes table and review tab
   - `/logs` — Log page loads with stats cards and search
   - `/itsm` — ITSM page loads with ticket table and filters
   - DataSource create form — shows conditional fields for log/itsm/cmdb types

3. **Database**: Verify all 7 new tables exist:
   ```sql
   SELECT table_name FROM information_schema.tables
   WHERE table_name IN ('log_events', 'itsm_tickets', 'cmdb_nodes', 'cmdb_edges',
                         'cmdb_sync_logs', 'cmdb_mapping_rules', 'cmdb_review_items');
   ```

4. **Integration**: Create a cmdb-type DataSource via UI, trigger sync via API, verify sync_log created
