"""Alert consumer — Kafka consumer (or DB-polling mock) that ingests alerts."""

import asyncio
import json
import logging

from sqlalchemy import select

from src.config import settings
from src.consumers.dedup import find_existing
from src.consumers.normalizer import normalize
from src.models.alert import Alert
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)


class AlertConsumer:
    """Consumes alert events from Kafka (or a DB mock table) and writes to the alerts table."""

    def __init__(self, mock: bool = False, topic: str = "ops-events"):
        self.mock = mock
        self.topic = topic
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        if self._running:
            return
        self._running = True
        if self.mock:
            self._task = asyncio.create_task(self._mock_loop())
            logger.info("AlertConsumer started in mock mode")
        else:
            self._task = asyncio.create_task(self._kafka_loop())
            logger.info("AlertConsumer started, topic=%s", self.topic)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("AlertConsumer stopped")

    async def _kafka_loop(self):
        """Real Kafka consumer loop."""
        try:
            from kafka import KafkaConsumer
        except ImportError:
            logger.error("kafka-python not installed; use --mock mode")
            return

        consumer = KafkaConsumer(
            self.topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            auto_offset_reset="latest",
            enable_auto_commit=True,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        try:
            for msg in consumer:
                if not self._running:
                    break
                await self._handle_message(msg.value)
        finally:
            consumer.close()

    async def _mock_loop(self):
        """DB-polling mock consumer. Polls a pending_events table every 10s."""
        while self._running:
            try:
                await self._process_mock_events()
            except Exception:
                logger.exception("Mock consumer tick failed")
            await asyncio.sleep(10)

    async def _process_mock_events(self):
        """Read pending_events from DB and process them."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(Alert).where(Alert.status == "pending_event").limit(50)
            )
            events = result.scalars().all()
            for alert in events:
                try:
                    await self._handle_message(dict(alert.raw_event or {}))
                    await db.delete(alert)
                except Exception:
                    logger.exception("Mock event processing failed for %s", alert.id)
            if events:
                await db.commit()

    async def _handle_message(self, raw: dict):
        """Normalize, deduplicate, and insert a single alert event."""
        try:
            fields = normalize(raw)
        except Exception:
            logger.warning("Failed to normalize event: %s", raw.get("title", raw))
            return

        async with async_session_factory() as db:
            existing = await find_existing(
                db, fields["title"], fields["source"]
            )
            if existing:
                logger.debug("Duplicate alert skipped: %s", fields["title"])
                return

            alert = Alert(
                event_id=fields["event_id"],
                title=fields["title"],
                source=fields["source"],
                severity=fields["severity"],
                raw_event=fields["raw_event"],
                status="pending",
            )
            db.add(alert)
            await db.commit()
            await db.refresh(alert)
            logger.info("Alert created: %s [%s]", alert.id, alert.title)

            # Fire-and-forget auto-analysis
            try:
                asyncio.create_task(_auto_analyze(str(alert.id)))
            except Exception:
                logger.exception("Failed to spawn analysis for %s", alert.id)


async def _auto_analyze(alert_id: str):
    """Trigger-based auto-analysis for a newly ingested alert."""
    from src.models.base import async_session_factory
    from src.services.alert_analyzer import analyze
    from src.services.trigger_engine import match_triggers

    async with async_session_factory() as db:
        result = await db.execute(select(Alert).where(Alert.id == alert_id))
        alert = result.scalar_one_or_none()
        if alert is None:
            return

        try:
            triggers = await match_triggers(db, alert)
            if triggers:
                await analyze(alert, triggers)
                await db.commit()
        except Exception:
            logger.exception("Auto-analysis failed for alert %s", alert_id)
