"""LogProcessor — normalizes raw log entries and writes to log_events partition table."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.models.base import async_session_factory
from src.models.log import LogEvent

logger = logging.getLogger(__name__)

FIELD_EXTRACTORS: dict[str, list[str]] = {
    "timestamp": ["@timestamp", "time", "timestamp"],
    "service": ["service", "app", "container_name", "namespace"],
    "level": ["level", "severity", "log_level"],
    "trace_id": ["trace_id", "traceId", "x-trace-id", "traceparent"],
    "message": ["message", "msg", "log", "body"],
}

TIMESTAMP_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
]


def _extract_field(raw: dict[str, Any], target: str) -> Any | None:
    extractors = FIELD_EXTRACTORS.get(target, [target])
    for key in extractors:
        value = raw.get(key)
        if value is not None and value != "":
            return str(value)[:600] if target == "message" else value
    return None


def _try_parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def normalize(raw: dict[str, Any], datasource_id: str) -> dict[str, Any]:
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
    normalized = [normalize(log, datasource_id) for log in raw_logs]
    count = await batch_insert(normalized)
    logger.debug("Log batch: %d raw -> %d inserted", len(raw_logs), count)
    return count
