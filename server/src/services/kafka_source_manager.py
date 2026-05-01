"""Kafka Source Manager — manages per-datasource Kafka consumer tasks."""

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from src.models.base import async_session_factory
from src.models.datasource import DataSource
from src.models.ingestion_log import IngestionLog
from src.models.notification import Notification
from src.models.alert import Alert
from src.consumers.normalizer import normalize
from src.consumers.dedup import find_existing

logger = logging.getLogger(__name__)


class KafkaSourceManager:
    """Manages per-source Kafka consumer tasks."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._consumer_tasks: dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._reconcile_loop())
        logger.info("Kafka source manager started")

    async def stop(self) -> None:
        self._running = False
        for ds_id, t in list(self._consumer_tasks.items()):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._consumer_tasks.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Kafka source manager stopped")

    async def _reconcile_loop(self) -> None:
        while self._running:
            try:
                await self._reconcile()
            except Exception:
                logger.exception("Kafka manager reconcile error")
            await asyncio.sleep(30)

    async def _reconcile(self) -> None:
        async with async_session_factory() as db:
            result = await db.execute(
                select(DataSource).where(
                    DataSource.source_type == "kafka",
                    DataSource.is_enabled == True,
                )
            )
            sources = result.scalars().all()

        active_ids = {str(s.id) for s in sources}
        current_ids = set(self._consumer_tasks.keys())

        for ds_id in current_ids - active_ids:
            self._consumer_tasks[ds_id].cancel()
            try:
                await self._consumer_tasks[ds_id]
            except (asyncio.CancelledError, Exception):
                pass
            del self._consumer_tasks[ds_id]

        for ds in sources:
            ds_id = str(ds.id)
            if ds_id not in self._consumer_tasks:
                self._consumer_tasks[ds_id] = asyncio.create_task(self._kafka_loop(ds))
                logger.info("Started Kafka consumer for %s", ds.name)

    async def _kafka_loop(self, ds: DataSource) -> None:
        """Poll Kafka for this datasource, with DB-polling fallback."""
        ds_id = str(ds.id)
        ds_name = ds.name
        config = ds.config or {}

        try:
            from kafka import KafkaConsumer
            consumer = KafkaConsumer(
                config.get("topic", "ops-events"),
                bootstrap_servers=config.get("bootstrap_servers", "localhost:9092"),
                group_id=config.get("consumer_group", f"aiopsos-{ds_id[:8]}"),
                auto_offset_reset="latest",
                enable_auto_commit=True,
                value_deserializer=lambda v: json.loads(v.decode()),
            )
            logger.info("Kafka connected for %s", ds_name)
            while self._running:
                for msg in consumer:
                    await self._handle_message(ds, msg.value)
                await asyncio.sleep(0.1)
        except ImportError:
            logger.warning("kafka-python not installed for %s, using DB poll fallback", ds_name)
            while self._running:
                await self._mock_loop(ds)
                await asyncio.sleep(10)
        except Exception:
            logger.exception("Kafka consumer error for %s", ds_name)
            while self._running:
                await asyncio.sleep(30)

    async def _mock_loop(self, ds: DataSource) -> None:
        """DB poll fallback when Kafka is unavailable."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(Alert).where(Alert.status == "pending_event").limit(50)
            )
            pending = result.scalars().all()
            for alert in pending:
                if alert.raw_event and isinstance(alert.raw_event, dict):
                    await self._handle_message(ds, alert.raw_event)
                await db.delete(alert)
            await db.commit()

    async def _handle_message(self, ds: DataSource, raw: dict) -> None:
        norm = normalize(raw, source_hint=ds.name)
        async with async_session_factory() as db:
            existing = await find_existing(db, norm["title"], norm["source"])
            if existing:
                return

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
                title=f"[{ds.name}] {alert.title}",
                message=f"Severity: {alert.severity}",
                severity=alert.severity,
            )
            db.add(notif)

            log = IngestionLog(
                datasource_id=ds.id,
                status="success",
                events_received=1,
                alerts_created=1,
            )
            db.add(log)

            ds_row = await db.get(DataSource, ds.id)
            if ds_row:
                ds_row.last_ingested_at = datetime.now(UTC)
                ds_row.total_ingested += 1

            await db.commit()


kafka_source_manager = KafkaSourceManager()
