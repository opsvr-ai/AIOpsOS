"""Webhook handler — validates, normalizes, and ingests webhook payloads."""

import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime

from src.models.datasource import DataSource
from src.models.ingestion_log import IngestionLog
from src.models.notification import Notification
from src.models.base import async_session_factory
from src.consumers.normalizer import normalize
from src.consumers.dedup import find_existing
from src.models.alert import Alert
from src.services.event_mapper import insert_events

logger = logging.getLogger(__name__)

_rate_buckets: dict[str, list[float]] = {}


def _check_rate_limit(endpoint_id: str, limit_per_min: int) -> bool:
    now = time.time()
    bucket = _rate_buckets.setdefault(endpoint_id, [])
    bucket[:] = [t for t in bucket if now - t < 60]
    if len(bucket) >= limit_per_min:
        return False
    bucket.append(now)
    return True


def _verify_signature(secret: str, payload: bytes, signature: str | None) -> bool:
    if not secret or not signature:
        return True
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


async def process_webhook(datasource: DataSource, body: dict, headers: dict) -> dict:
    config = datasource.config
    endpoint_id = config.get("endpoint_id", "")
    secret = config.get("secret", "")
    rate_limit = config.get("rate_limit_per_min", 60)
    sig_header = config.get("signature_header", "X-Hub-Signature-256")

    if not _check_rate_limit(endpoint_id, rate_limit):
        return {"status": "rate_limited", "detail": "Too many requests"}

    raw_bytes = json.dumps(body, separators=(",", ":")).encode()
    signature = headers.get(sig_header.lower(), headers.get(sig_header))
    if not _verify_signature(secret, raw_bytes, signature):
        return {"status": "unauthorized", "detail": "Invalid signature"}

    payloads: list[dict] = []
    if isinstance(body, list):
        payloads = body
    elif isinstance(body, dict):
        data_key = config.get("data_path")
        if data_key:
            items = body
            for key in str(data_key).strip("$.").split("."):
                items = items.get(key, {}) if isinstance(items, dict) else items
            if isinstance(items, list):
                payloads = items
            else:
                payloads = [body]
        else:
            payloads = [body]

    source_name = datasource.name
    events_received = len(payloads)
    alerts_created = 0
    alerts_deduped = 0
    errors_count = 0
    errors_detail: list[str] = []
    start = time.time()

    async with async_session_factory() as db:
        for raw in payloads:
            try:
                norm = normalize(raw, source_hint=source_name)
                nom_rules = datasource.normalization_rules or {}
                if nom_rules.get("title_key"):
                    norm["title"] = str(raw.get(nom_rules["title_key"], norm["title"]))
                if nom_rules.get("severity_key"):
                    norm["severity"] = str(raw.get(nom_rules["severity_key"], norm["severity"]))

                existing = await find_existing(db, norm["title"], norm["source"])
                if existing:
                    alerts_deduped += 1
                    continue

                alert = Alert(
                    title=norm["title"],
                    source=norm["source"],
                    severity=norm["severity"],
                    status="pending",
                    raw_event=raw,
                    event_id=norm.get("event_id"),
                )
                db.add(alert)
                await db.flush()

                notif = Notification(
                    alert_id=alert.id,
                    title=f"[{source_name}] {alert.title}",
                    message=f"Severity: {alert.severity}",
                    severity=alert.severity,
                )
                db.add(notif)
                alerts_created += 1

                # Fire-and-forget channel dispatch
                try:
                    import asyncio
                    from src.services.channel_manager import channel_manager
                    asyncio.ensure_future(
                        channel_manager.notify(
                            alert_title=alert.title,
                            alert_message=f"Source: {source_name}\nSeverity: {alert.severity}",
                            severity=alert.severity,
                            alert_id=str(alert.id),
                        )
                    )
                except Exception:
                    pass
            except Exception:
                errors_count += 1
                errors_detail.append("ingestion failed")

        # Write to dynamic event table if mapping is configured
        events_mapped = 0
        if datasource.table_mapping:
            try:
                events_mapped = await insert_events(
                    str(datasource.id), datasource.table_mapping, payloads
                )
            except Exception:
                logger.exception("Event mapping failed for datasource %s", datasource.id)

        log = IngestionLog(
            datasource_id=datasource.id,
            status="success" if errors_count == 0 else "partial",
            events_received=events_received,
            alerts_created=alerts_created,
            alerts_deduped=alerts_deduped,
            errors_count=errors_count,
            errors_detail=errors_detail,
            duration_ms=int((time.time() - start) * 1000),
        )
        db.add(log)

        datasource_row = await db.get(DataSource, datasource.id)
        if datasource_row:
            datasource_row.last_ingested_at = datetime.now(UTC)
            datasource_row.total_ingested += alerts_created

        await db.commit()

    return {
        "status": "accepted",
        "events_received": events_received,
        "alerts_created": alerts_created,
        "alerts_deduped": alerts_deduped,
        "events_mapped": events_mapped,
    }
