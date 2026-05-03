"""API Poller — polls API-type DataSources on configured intervals with request chaining."""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime

import aiohttp
from sqlalchemy import select

from src.consumers.dedup import find_existing
from src.consumers.normalizer import normalize
from src.models.alert import Alert
from src.models.base import async_session_factory
from src.models.datasource import DataSource
from src.models.ingestion_log import IngestionLog
from src.models.notification import Notification

logger = logging.getLogger(__name__)

POLL_RECONCILE_INTERVAL = 30
TEMPLATE_RE = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")


def _resolve_template(template: str, context: dict) -> str:
    def replacer(m):
        keys = m.group(1).split(".")
        val = context
        for k in keys:
            val = val.get(k) if isinstance(val, dict) else getattr(val, k, None)
            if val is None:
                return m.group(0)
        return str(val)
    return TEMPLATE_RE.sub(replacer, template)


def _resolve_value(value, context: dict):
    if isinstance(value, str):
        return _resolve_template(value, context)
    if isinstance(value, dict):
        return {k: _resolve_value(v, context) for k, v in value.items()}
    return value


def _extract_jsonpath(data: dict, path: str | None):
    if not path:
        return data if isinstance(data, list) else [data]
    keys = path.strip("$.").split(".")
    result = data
    for k in keys:
        if isinstance(result, dict):
            result = result.get(k, {})
        elif isinstance(result, list) and k == "*":
            return result
    return result if isinstance(result, list) else [result]


async def _execute_request_chain(datasource: DataSource) -> list[dict]:
    config = datasource.config
    request_chain = config.get("request_chain", [])
    if not request_chain:
        # single request to base_url
        request_chain = [{
            "step": 1, "name": "fetch", "method": "GET",
            "url": config.get("data_path", "/"), "data_path": config.get("data_path"),
        }]

    session: dict = {}
    timeout = aiohttp.ClientTimeout(total=config.get("timeout_seconds", 30))
    base_url = config.get("base_url", "")

    async with aiohttp.ClientSession(timeout=timeout) as http:
        for step in sorted(request_chain, key=lambda s: s.get("step", 0)):
            ctx = {**session, "last_run": datasource.last_ingested_at.isoformat() if datasource.last_ingested_at else ""}
            url = _resolve_template(step.get("url", ""), ctx)
            full_url = url if url.startswith("http") else f"{base_url.rstrip('/')}/{url.lstrip('/')}"

            method = step.get("method", "GET").upper()
            headers = {str(k): str(v) for k, v in (step.get("headers") or {}).items()}
            headers = {k: _resolve_template(v, ctx) for k, v in headers.items()}

            query_params = _resolve_value(step.get("query_params") or {}, ctx)
            body_raw = _resolve_value(step.get("body"), ctx)

            logger.debug("API poll %s step %s: %s %s", datasource.name, step["step"], method, full_url)
            resp = await http.request(method, full_url, headers=headers, params=query_params,
                                       json=body_raw if body_raw else None)
            resp_data = await resp.json() if resp.content_type and "json" in resp.content_type else {}

            extract_rules = step.get("extract") or {}
            for key, jsonpath_expr in extract_rules.items():
                val = _extract_jsonpath({"response": resp_data}, f"$.response.{jsonpath_expr.strip('$.')}")
                session[key] = val[0] if isinstance(val, list) and len(val) == 1 else val

            store_as = step.get("store_as")
            if store_as:
                if step.get("data_path"):
                    session[store_as] = _extract_jsonpath(resp_data, step["data_path"])
                else:
                    session[store_as] = resp_data

    # After all steps, extract result from last step's data_path
    last_step = sorted(request_chain, key=lambda s: s.get("step", 0))[-1]
    data_path = last_step.get("data_path")
    if data_path:
        store_key = last_step.get("store_as", "result")
        if store_key in session:
            return session[store_key] if isinstance(session[store_key], list) else [session[store_key]]
    return []


async def _poll_datasource(datasource_id: str) -> None:
    """Poll a single API datasource and ingest events."""
    async with async_session_factory() as db:
        ds = await db.get(DataSource, datasource_id)
        if not ds or not ds.is_enabled or ds.source_type != "api":
            return
        ds_name = ds.name

    start = time.time()
    events_received = 0
    alerts_created = 0
    alerts_deduped = 0
    errors_count = 0
    errors_detail: list[str] = []
    request_url = ""

    try:
        events = await _execute_request_chain(ds)
        events_received = len(events)

        async with async_session_factory() as db:
            for raw in events:
                try:
                    norm = normalize(raw, source_hint=ds_name)
                    nom_rules = ds.normalization_rules or {}
                    if nom_rules.get("title_key"):
                        norm["title"] = str(raw.get(nom_rules["title_key"], norm["title"]))

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
                    )
                    db.add(alert)
                    await db.flush()

                    notif = Notification(
                        alert_id=alert.id,
                        title=f"[{ds_name}] {alert.title}",
                        message=f"Severity: {alert.severity}",
                        severity=alert.severity,
                    )
                    db.add(notif)
                    alerts_created += 1
                except Exception:
                    errors_count += 1

            log = IngestionLog(
                datasource_id=ds.id,
                status="success" if errors_count == 0 else "partial",
                events_received=events_received,
                alerts_created=alerts_created,
                alerts_deduped=alerts_deduped,
                errors_count=errors_count,
                errors_detail=errors_detail,
                duration_ms=int((time.time() - start) * 1000),
                request_url=request_url,
            )
            db.add(log)

            ds_row = await db.get(DataSource, ds.id)
            if ds_row:
                ds_row.last_ingested_at = datetime.now(UTC)
                ds_row.total_ingested += alerts_created

            await db.commit()

    except Exception as e:
        logger.exception("API poll failed for %s", ds_name)
        async with async_session_factory() as db:
            ds_row = await db.get(DataSource, ds.id)
            if ds_row:
                ds_row.status = "error"
                ds_row.error_message = str(e)[:1024]
            await db.commit()


class ApiPoller:
    """Manages per-source polling tasks for API-type DataSources."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._poller_tasks: dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._reconcile_loop())
        logger.info("API poller started")

    async def stop(self) -> None:
        self._running = False
        for ds_id, t in list(self._poller_tasks.items()):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._poller_tasks.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("API poller stopped")

    async def _reconcile_loop(self) -> None:
        while self._running:
            try:
                await self._reconcile()
            except Exception:
                logger.exception("API poller reconcile error")
            await asyncio.sleep(POLL_RECONCILE_INTERVAL)

    async def _reconcile(self) -> None:
        async with async_session_factory() as db:
            result = await db.execute(
                select(DataSource).where(
                    DataSource.source_type == "api",
                    DataSource.is_enabled,
                )
            )
            sources = result.scalars().all()

        active_ids = {str(s.id) for s in sources}
        current_ids = set(self._poller_tasks.keys())

        for ds_id in current_ids - active_ids:
            self._poller_tasks[ds_id].cancel()
            try:
                await self._poller_tasks[ds_id]
            except (asyncio.CancelledError, Exception):
                pass
            del self._poller_tasks[ds_id]

        for ds in sources:
            ds_id = str(ds.id)
            if ds_id not in self._poller_tasks:
                interval = (ds.config or {}).get("poll_interval_seconds", 60)
                self._poller_tasks[ds_id] = asyncio.create_task(self._poll_loop(ds, interval))
                logger.info("Started poller for %s (every %ds)", ds.name, interval)

    async def _poll_loop(self, ds: DataSource, interval: int) -> None:
        while self._running:
            try:
                await _poll_datasource(str(ds.id))
            except Exception:
                logger.exception("Poll loop error for %s", ds.name)
            await asyncio.sleep(interval)


api_poller = ApiPoller()
