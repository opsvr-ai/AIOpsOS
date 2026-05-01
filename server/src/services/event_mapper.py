"""Dynamic table mapping for datasource events.

When a DataSource has a table_mapping config, incoming raw events are written
to a dynamically-created table (events_{datasource_id}) instead of being
normalized into alerts. Tables are auto-created on first use.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, String, Table, Text, inspect, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import Base, async_session_factory

logger = logging.getLogger(__name__)

COLUMN_TABLE_PREFIX = "events_"

TYPE_MAP = {
    "string": String(1024),
    "text": Text,
    "integer": Float,
    "float": Float,
    "datetime": DateTime(timezone=True),
    "json": JSONB,
}


def _resolve_path(data: dict, path: str) -> object:
    """Resolve a dotted path like 'event.service' against a dict."""
    clean = path.lstrip("$").lstrip(".")
    if not clean:
        return None
    current: object = data
    for key in clean.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def _table_name(datasource_id: str) -> str:
    return f"{COLUMN_TABLE_PREFIX}{datasource_id.replace('-', '_')}"


def _build_table(datasource_id: str, mapping: dict) -> Table:
    """Build a SQLAlchemy Table from a table_mapping definition."""
    columns = mapping.get("columns", [])
    cols = [
        Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        Column("ingested_at", DateTime(timezone=True), default=lambda: datetime.now(UTC)),
        Column("raw_event", JSONB, nullable=True),
    ]
    for col_def in columns:
        col_name = col_def.get("name", "")
        col_type_key = col_def.get("type", "string")
        sa_type = TYPE_MAP.get(col_type_key, String(1024))
        cols.append(Column(col_name, sa_type, nullable=True))

    return Table(
        _table_name(datasource_id),
        Base.metadata,
        *cols,
        extend_existing=True,
        keep_existing=True,
    )


async def ensure_event_table(datasource_id: str, mapping: dict) -> str:
    """Create the dynamic event table if it doesn't exist. Returns the table name."""
    tbl_name = _table_name(datasource_id)
    async with async_session_factory() as db:
        conn = await db.connection()
        inspector = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).has_table(tbl_name)
        )
        if inspector:
            return tbl_name

        table = _build_table(datasource_id, mapping)
        await conn.run_sync(table.create)
        await db.commit()
        logger.info("Created dynamic event table '%s'", tbl_name)
    return tbl_name


async def insert_events(datasource_id: str, mapping: dict, events: list[dict]) -> int:
    """Insert raw events into the dynamic event table. Returns count inserted."""
    if not events:
        return 0

    tbl_name = await ensure_event_table(datasource_id, mapping)
    columns = mapping.get("columns", [])
    inserted = 0

    async with async_session_factory() as db:
        for raw in events:
            row: dict[str, object] = {
                "id": uuid.uuid4(),
                "ingested_at": datetime.now(UTC),
                "raw_event": raw,
            }
            for col_def in columns:
                col_name = col_def.get("name", "")
                source_path = col_def.get("source_path", "")
                if col_name and source_path:
                    row[col_name] = _resolve_path(raw, source_path)

            try:
                await db.execute(
                    text(
                        f"INSERT INTO {tbl_name} ({', '.join(row.keys())}) "
                        f"VALUES ({', '.join(f':{k}' for k in row)})"
                    ),
                    row,
                )
                inserted += 1
            except Exception:
                logger.exception("Failed to insert event into %s", tbl_name)

        await db.commit()

    return inserted
