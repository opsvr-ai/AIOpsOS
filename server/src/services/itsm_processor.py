"""ItsmProcessor — normalizes ITSM tickets, enriches with service context, links alerts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.models.base import async_session_factory
from src.models.itsm import ItsmTicket

logger = logging.getLogger(__name__)

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


def _get_nested(obj: dict[str, Any], path: str) -> Any | None:
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
    field_map = custom_mapping or SYSTEM_FIELD_MAPS.get(itsm_system, {})

    def extract(field: str, default: Any = None) -> Any:
        mapped = field_map.get(field, field)
        return _get_nested(raw, mapped) or raw.get(mapped, default)

    external_id = str(extract("external_id") or raw.get("id", ""))
    created = extract("created_at")
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            created = datetime.now(UTC)
    if created is None:
        created = datetime.now(UTC)

    return {
        "external_id": external_id,
        "ticket_type": str(extract("ticket_type", "incident")).lower(),
        "title": str(extract("title", "") or "")[:512],
        "status": str(extract("status", "new")).lower(),
        "priority": str(extract("priority", "medium")).lower(),
        "affected_service": str(extract("affected_service", "") or ""),
        "created_at": created,
        "resolved_at": extract("resolved_at"),
        "raw_data": raw,
        "datasource_id": datasource_id,
    }


async def upsert_ticket(
    ticket_data: dict[str, Any],
    alert_ids: list[str] | None = None,
) -> None:
    data = dict(ticket_data)
    if alert_ids:
        data["linked_alert_ids"] = alert_ids
    async with async_session_factory() as db:
        stmt = (
            pg_insert(ItsmTicket)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["external_id"],
                set_={
                    "status": data.get("status"),
                    "priority": data.get("priority"),
                    "raw_data": data.get("raw_data"),
                    "resolved_at": data.get("resolved_at"),
                    "linked_alert_ids": data.get("linked_alert_ids"),
                },
            )
        )
        await db.execute(stmt)
        await db.commit()


async def link_alerts(
    affected_service: str,
    ticket_time: datetime,
    window_minutes: int = 30,
) -> list[str]:
    from src.models.alert import Alert

    window = timedelta(minutes=window_minutes)
    start = ticket_time - window
    end = ticket_time + window

    async with async_session_factory() as db:
        result = await db.execute(
            select(Alert.id).where(Alert.created_at.between(start, end))
        )
        return [str(row[0]) for row in result.all()]


async def process_tickets(
    raw_tickets: list[dict[str, Any]],
    datasource_id: str,
    itsm_system: str = "custom",
    custom_mapping: dict[str, str] | None = None,
) -> int:
    count = 0
    for raw in raw_tickets:
        normalized = normalize_ticket(raw, datasource_id, itsm_system, custom_mapping)
        if not normalized["external_id"]:
            continue
        await upsert_ticket(normalized)
        count += 1
    logger.info("ITSM batch: %d tickets upserted", count)
    return count
