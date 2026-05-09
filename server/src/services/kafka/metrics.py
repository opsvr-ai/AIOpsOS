"""Kafka lag + DLQ growth metrics collector.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.3 / R-5.7 / R-6.2.

Runs a single long-lived asyncio task that, every ``poll_interval_s`` seconds:

1. Lists all consumer groups + topics.
2. For each ``(group, topic, partition)`` triple: computes lag as
   ``end_offset - committed_offset`` and writes it to the
   ``kafka_lag{group,topic,partition}`` Prometheus gauge.
3. For each ``*.dlq`` topic: tracks log-end-offset deltas over a 60s rolling
   window and publishes the growth rate (events/min) to ``kafka_dlq_rate``.

The collector never raises into the caller — all exceptions are logged and
the loop backs off for ``error_backoff_s`` seconds before retrying. Tests can
drive individual ticks via :meth:`_tick` to avoid sleeping.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from src.config import settings
from src.core.metrics import kafka_dlq_rate, kafka_lag
from src.services.kafka.admin import KafkaAdminService

logger = logging.getLogger(__name__)


class KafkaMetricsCollector:
    def __init__(
        self,
        admin: KafkaAdminService | None = None,
        *,
        bootstrap_servers: str | None = None,
        poll_interval_s: float = 5.0,
        dlq_window_s: float = 60.0,
        error_backoff_s: float = 30.0,
        dlq_suffix: str = ".dlq",
    ) -> None:
        self._owns_admin = admin is None
        self._admin = admin or KafkaAdminService(bootstrap_servers=bootstrap_servers)
        self._bootstrap = bootstrap_servers or settings.kafka_bootstrap_servers
        self._poll_interval_s = poll_interval_s
        self._dlq_window_s = dlq_window_s
        self._error_backoff_s = error_backoff_s
        self._dlq_suffix = dlq_suffix

        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

        # DLQ rolling window: topic → deque[(ts_seconds, end_offset_sum)]
        self._dlq_history: dict[str, deque[tuple[float, int]]] = {}

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        if self._owns_admin:
            await self._admin.start()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="kafka-metrics-collector")
        logger.info("KafkaMetricsCollector started (interval=%ss)", self._poll_interval_s)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._poll_interval_s + 2)
        except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover
            self._task.cancel()
        finally:
            self._task = None
            if self._owns_admin:
                try:
                    await self._admin.close()
                except Exception:  # pragma: no cover
                    logger.exception("Error closing admin client")
        logger.info("KafkaMetricsCollector stopped")

    # -- loop -----------------------------------------------------------

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._tick()
                sleep_for = self._poll_interval_s
            except Exception:
                logger.exception("KafkaMetricsCollector tick failed; backing off")
                sleep_for = self._error_backoff_s
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        """One poll cycle. Public-ish so tests can drive it manually."""
        groups = await self._admin.list_consumer_groups()
        topics = await self._admin.list_topics(include_internal=False)

        # 1) lag for every group
        for g in groups:
            try:
                detail = await self._admin.describe_group(g.group_id)
            except Exception:
                logger.warning("describe_group failed: %s", g.group_id, exc_info=True)
                continue
            for lag in detail.lags:
                kafka_lag.labels(
                    group=g.group_id, topic=lag.topic, partition=str(lag.partition)
                ).set(float(lag.lag))

        # 2) DLQ growth rate
        dlq_topics = [t.name for t in topics if t.name.endswith(self._dlq_suffix)]
        if dlq_topics:
            await self._publish_dlq_rates(dlq_topics)

    async def _publish_dlq_rates(self, dlq_topics: list[str]) -> None:
        """Compute end-offset sums for DLQ topics and emit per-minute rates."""
        end_offsets = await self._fetch_end_offsets_by_topic(dlq_topics)
        now = time.monotonic()
        for topic, total_offsets in end_offsets.items():
            history = self._dlq_history.setdefault(topic, deque())
            history.append((now, total_offsets))
            # Evict anything older than the window
            cutoff = now - self._dlq_window_s
            while history and history[0][0] < cutoff:
                history.popleft()
            if len(history) < 2:
                rate_per_minute = 0.0
            else:
                first_ts, first_count = history[0]
                last_ts, last_count = history[-1]
                elapsed = max(1e-6, last_ts - first_ts)
                delta = max(0, last_count - first_count)
                rate_per_minute = (delta / elapsed) * 60.0
            kafka_dlq_rate.labels(topic=topic).set(rate_per_minute)

    async def _fetch_end_offsets_by_topic(
        self, topics: list[str]
    ) -> dict[str, int]:
        """Return {topic: sum(end_offset for every partition)}.

        We use a transient consumer with no group so polling does not affect
        production consumer offsets.
        """
        from aiokafka import AIOKafkaConsumer
        from aiokafka.structs import TopicPartition

        consumer = AIOKafkaConsumer(
            bootstrap_servers=self._bootstrap,
            group_id=None,
            enable_auto_commit=False,
            client_id="aiopsos-dlq-probe",
            request_timeout_ms=20_000,
        )
        await consumer.start()
        try:
            all_tps: list[Any] = []
            for t in topics:
                parts = consumer.partitions_for_topic(t) or set()
                if not parts:
                    # Force metadata refresh on first sighting
                    try:
                        await consumer.topics()  # refreshes metadata
                        parts = consumer.partitions_for_topic(t) or set()
                    except Exception:
                        parts = set()
                for p in parts:
                    all_tps.append(TopicPartition(t, int(p)))
            if not all_tps:
                return {t: 0 for t in topics}
            end = await consumer.end_offsets(all_tps)
            out: dict[str, int] = {t: 0 for t in topics}
            for tp, off in end.items():
                out[tp.topic] = out.get(tp.topic, 0) + int(off)
            return out
        finally:
            await consumer.stop()


__all__ = ["KafkaMetricsCollector"]
