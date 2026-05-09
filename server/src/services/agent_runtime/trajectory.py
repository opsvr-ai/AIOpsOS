"""TrajectorySink — non-blocking emitter for ``agent_trajectories``.

Spec: .kiro/specs/agent-runtime-optimization-evolution, tasks 6.2 + 6.4 /
R-2.1 / R-5.10 / R-6.3 / R-8.2.

Design goals (from design.md § TrajectorySink):

* Fire-and-forget ``emit()`` from any async context — never blocks the
  request path.
* Backpressure via a bounded ``asyncio.Queue`` (default 10 000 slots);
  on overflow we drop + bump ``trajectory_emit_dropped``.
* Flusher loop batches events (size or interval triggered), validates
  against :class:`KafkaSchemaRegistry`, persists to Postgres in one
  bulk insert, and publishes to Kafka keyed by ``session_id`` for
  per-session ordering.
* Schema-validation failures route to ``ops.agent.trajectory.dlq`` + bump
  ``kafka_schema_reject_total`` so bad events never silently vanish.
* Every flush emits a ``trajectory.flush`` OTel span with
  ``batch_size`` / ``success`` / ``duration_ms`` attributes.
* Lazy singleton — ``get_trajectory_sink()`` spins up the first caller
  and wires ``start/stop`` into the FastAPI lifespan.

The implementation is defensive: any single Kafka / DB failure is
counted and logged; it never raises into the caller. That contract is
what makes ``emit()`` safe from request handlers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert as sa_insert

from src.config import settings
from src.core.metrics import (
    kafka_schema_reject_total,
    trajectory_emit_dropped,
)
from src.core.tracing import tracer
from src.models.base import async_session_factory
from src.models.trajectory import AgentTrajectory
from src.schemas.trajectory import TrajectoryEvent
from src.services.kafka.schema import KafkaSchemaRegistry
from src.services.pii import sanitize_pii

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_TOPIC = "ops.agent.trajectory"
DLQ_TOPIC = "ops.agent.trajectory.dlq"


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


class TrajectorySink:
    """Async batch emitter for ``TrajectoryEvent``.

    Construction is cheap and does NOT touch the network; all I/O is
    deferred to :meth:`start`. Tests may inject an ``AsyncMock`` producer
    and a custom ``db_factory`` to exercise the flusher without a real
    Kafka / Postgres.
    """

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        *,
        queue_maxsize: int = 10_000,
        batch_size: int = 50,
        flush_interval_s: float = 1.0,
        kafka_topic: str = DEFAULT_TOPIC,
        dlq_topic: str = DLQ_TOPIC,
        producer: Any | None = None,
        schema_registry: KafkaSchemaRegistry | None = None,
        db_factory: Any | None = None,
        schema_version: int = 1,
    ) -> None:
        self._bootstrap = bootstrap_servers or settings.kafka_bootstrap_servers
        self._kafka_topic = kafka_topic
        self._dlq_topic = dlq_topic
        self._batch_size = int(batch_size)
        self._flush_interval_s = float(flush_interval_s)
        self._schema_version = int(schema_version)

        self._queue: asyncio.Queue[TrajectoryEvent] = asyncio.Queue(
            maxsize=int(queue_maxsize)
        )

        # Injected deps (tests) vs lazily-constructed defaults (prod)
        self._producer = producer
        self._producer_owned = producer is None
        self._registry = schema_registry or KafkaSchemaRegistry()
        self._db_factory = db_factory or async_session_factory

        # Cached schema row — fetched lazily; refreshed once on validation failure.
        self._cached_schema: dict[str, Any] | None = None

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._started: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        if self._producer is None:
            # Lazy import so the module is safe to load without aiokafka.
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap,
                client_id="aiopsos-trajectory-sink",
                enable_idempotence=True,
                request_timeout_ms=5000,
            )
            try:
                await self._producer.start()
            except Exception:
                logger.exception(
                    "trajectory: AIOKafkaProducer.start failed; sink continues "
                    "with kafka disabled (events still persist to PG)"
                )
                self._producer = None

        # Pre-warm schema cache but don't fail start if the DB blips.
        try:
            await self._refresh_schema()
        except Exception:
            logger.exception("trajectory: initial schema fetch failed")

        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._flush_loop(), name="trajectory-sink-flush"
        )
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
            except Exception:
                logger.exception("trajectory: flusher terminated abnormally")

        # Drain any tail events once more, best-effort.
        tail: list[TrajectoryEvent] = []
        while not self._queue.empty():
            try:
                tail.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if tail:
            try:
                await self._flush_batch(tail)
            except Exception:
                logger.exception("trajectory: final drain failed")

        if self._producer_owned and self._producer is not None:
            try:
                await self._producer.stop()
            except Exception:
                logger.exception("trajectory: producer.stop failed")
        self._producer = None
        self._started = False

    # ------------------------------------------------------------------
    # emit (caller API)
    # ------------------------------------------------------------------

    def emit(self, event: TrajectoryEvent) -> None:
        """Fire-and-forget enqueue. Drops + counts on overflow.

        Must be called from an async context (the ``asyncio.Queue`` uses
        the running loop under the hood). Callers in sync paths should
        use a ``loop.call_soon_threadsafe`` bridge instead.
        """
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                trajectory_emit_dropped.inc()
            except Exception:
                logger.debug("metric inc failed", exc_info=True)

    # ------------------------------------------------------------------
    # Flusher
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        while not self._stop_event.is_set():
            batch = await self._collect_batch()
            if not batch:
                continue
            try:
                await self._flush_batch(batch)
            except Exception:
                # Individual failures already counted inside the helpers;
                # this catch just prevents a rogue exception from killing
                # the flusher task.
                logger.exception("trajectory: flush iteration failed")

    async def _collect_batch(self) -> list[TrajectoryEvent]:
        """Drain up to ``batch_size`` events or wait up to ``flush_interval_s``.

        Returns an empty list only when ``stop()`` fires while we're
        waiting on the first item.
        """
        batch: list[TrajectoryEvent] = []
        first_wait_task = asyncio.create_task(self._queue.get())
        stop_wait_task = asyncio.create_task(self._stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {first_wait_task, stop_wait_task},
                timeout=self._flush_interval_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if first_wait_task in done:
                batch.append(first_wait_task.result())
            else:
                first_wait_task.cancel()
                try:
                    await first_wait_task
                except (asyncio.CancelledError, Exception):
                    pass
                if stop_wait_task in done:
                    return []
                # Timeout with empty queue → opportunistically return empty
                # so the loop can re-check stop_event.
                return []
        finally:
            if not stop_wait_task.done():
                stop_wait_task.cancel()
                try:
                    await stop_wait_task
                except (asyncio.CancelledError, Exception):
                    pass

        # Greedy drain up to batch_size.
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _flush_batch(self, batch: list[TrajectoryEvent]) -> None:
        if not batch:
            return

        t0 = time.perf_counter()
        success = False
        with tracer.start_as_current_span("trajectory.flush") as span:
            span.set_attribute("batch_size", len(batch))
            try:
                # 1) Sanitise PII in data / metadata *before* schema validation
                #    so scrubbed payloads are what we persist AND validate.
                sanitised = [self._sanitise_event(e) for e in batch]

                # 2) Validate every event; bad ones route to DLQ.
                valid, invalid = await self._split_valid(sanitised)

                # 3) Persist the valid ones to Postgres.
                if valid:
                    await self._persist_rows(valid)

                # 4) Fan-out to Kafka (with single retry).
                if valid:
                    await self._produce_batch(valid)

                # 5) Route invalid events to the DLQ.
                if invalid:
                    await self._produce_dlq(invalid)

                success = True
            except Exception:
                logger.exception("trajectory: flush pipeline failed")
            finally:
                dt = (time.perf_counter() - t0) * 1000.0
                span.set_attribute("success", success)
                span.set_attribute("duration_ms", dt)

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitise_event(event: TrajectoryEvent) -> TrajectoryEvent:
        """Return a copy with ``data`` / ``metadata`` scrubbed of known secrets."""
        new_data = sanitize_pii(event.data) if event.data else event.data
        new_meta = sanitize_pii(event.metadata) if event.metadata else event.metadata
        if new_data is event.data and new_meta is event.metadata:
            return event
        return event.model_copy(update={"data": new_data, "metadata": new_meta})

    async def _split_valid(
        self, events: list[TrajectoryEvent]
    ) -> tuple[list[TrajectoryEvent], list[tuple[TrajectoryEvent, list[str]]]]:
        """Validate each event against the cached TrajectoryEvent.v1 schema.

        Cache miss → refresh once. If validation consistently reports
        "no schema registered", we bail out after a second attempt and
        treat the whole batch as invalid so it lands in the DLQ.
        """
        if self._cached_schema is None:
            try:
                await self._refresh_schema()
            except Exception:
                pass

        valid: list[TrajectoryEvent] = []
        invalid: list[tuple[TrajectoryEvent, list[str]]] = []
        for ev in events:
            payload = _event_to_payload(ev)
            errors = _validate_against(self._cached_schema, payload)
            if errors is None:
                # "None" signals "no cached schema" — attempt one refresh
                try:
                    await self._refresh_schema()
                except Exception:
                    logger.debug(
                        "trajectory: schema refresh failed during validate",
                        exc_info=True,
                    )
                errors = _validate_against(self._cached_schema, payload)

            if errors:
                invalid.append((ev, errors))
                try:
                    kafka_schema_reject_total.labels(topic=self._kafka_topic).inc()
                except Exception:
                    logger.debug("metric inc failed", exc_info=True)
            elif errors == []:
                valid.append(ev)
            else:
                # None → still no schema; treat as invalid so nothing is
                # silently skipped.
                invalid.append((ev, ["schema unavailable"]))
        return valid, invalid

    async def _refresh_schema(self) -> None:
        row = await self._registry.get(self._kafka_topic, version=self._schema_version)
        if row is None:
            self._cached_schema = None
            return
        self._cached_schema = dict(row.schema)

    async def _persist_rows(self, events: list[TrajectoryEvent]) -> None:
        """Bulk-insert the batch into ``agent_trajectories``.

        Failures are counted via ``trajectory_emit_dropped`` because the
        caller treats Postgres as the system-of-record — if a row never
        lands there, the promise of R-6.3 needs the dropped counter to
        account for it.
        """
        try:
            async with self._db_factory() as session:
                rows = [_event_to_row(e) for e in events]
                await session.execute(sa_insert(AgentTrajectory), rows)
                await session.commit()
        except Exception:
            logger.exception("trajectory: DB insert failed for batch of %d", len(events))
            try:
                trajectory_emit_dropped.inc(len(events))
            except Exception:
                logger.debug("metric inc failed", exc_info=True)

    async def _produce_batch(self, events: list[TrajectoryEvent]) -> None:
        if self._producer is None:
            return
        for ev in events:
            ok = await self._produce_one(self._kafka_topic, ev)
            if not ok:
                try:
                    trajectory_emit_dropped.inc()
                except Exception:
                    logger.debug("metric inc failed", exc_info=True)

    async def _produce_dlq(
        self, invalid: list[tuple[TrajectoryEvent, list[str]]]
    ) -> None:
        if self._producer is None:
            return
        for ev, errors in invalid:
            envelope = {
                "id": str(ev.id),
                "original_topic": self._kafka_topic,
                "failure_reason": "schema_validation_failed",
                "failed_at": datetime.now(tz=timezone.utc).isoformat(),
                "attempt_count": 1,
                "original_value": _event_to_payload(ev),
                "errors": errors,
                "tags": {"cause": "schema"},
            }
            try:
                await self._producer.send_and_wait(
                    self._dlq_topic,
                    key=str(ev.session_id).encode("utf-8"),
                    value=json.dumps(envelope).encode("utf-8"),
                )
            except Exception:
                logger.exception("trajectory: DLQ produce failed for %s", ev.id)

    async def _produce_one(self, topic: str, event: TrajectoryEvent) -> bool:
        """Send one event with a single 200ms-delay retry before giving up."""
        payload = json.dumps(_event_to_payload(event)).encode("utf-8")
        key = str(event.session_id).encode("utf-8")
        for attempt in (1, 2):
            try:
                await self._producer.send_and_wait(topic, key=key, value=payload)
                return True
            except Exception:
                logger.warning(
                    "trajectory: kafka produce attempt %d failed for %s",
                    attempt,
                    event.id,
                    exc_info=True,
                )
                if attempt == 1:
                    await asyncio.sleep(0.2)
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_to_payload(event: TrajectoryEvent) -> dict[str, Any]:
    """JSON-safe dict. UUIDs stringified, datetime ISO-8601."""
    data = event.model_dump(mode="json")
    # model_dump(mode="json") already coerces UUID → str and datetime → ISO.
    return data


def _event_to_row(event: TrajectoryEvent) -> dict[str, Any]:
    """Column dict suitable for SQLAlchemy core ``insert(AgentTrajectory)``."""
    return {
        "id": event.id,
        "session_id": event.session_id,
        "user_id": event.user_id,
        "space_id": event.space_id,
        "parent_id": event.parent_id,
        "kind": event.kind,
        "outcome": event.outcome,
        "model": event.model,
        "latency_ms": event.latency_ms,
        "tokens_in": event.tokens_in,
        "tokens_out": event.tokens_out,
        # ``data`` on the DB side is a single JSONB blob; we merge metadata
        # into it under a reserved key so reflection tooling can still get
        # at ``prompt_version_id`` / ``sub_agent_name`` etc.
        "data": {**(event.data or {}), "metadata": event.metadata or {}},
        "tags": list(event.tags or []),
        "created_at": event.ts,
    }


def _validate_against(
    schema: dict[str, Any] | None, payload: dict[str, Any]
) -> list[str] | None:
    """Run a Draft 2020-12 validator; ``None`` means "no cached schema"."""
    if schema is None:
        return None
    try:
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        return [
            f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in errors
        ]
    except Exception:
        logger.exception("trajectory: validator crashed")
        return ["validator_error"]


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


_INIT_LOCK = asyncio.Lock()
_SINK: TrajectorySink | None = None


async def get_trajectory_sink() -> TrajectorySink:
    """Lazily create + start a single ``TrajectorySink`` per process."""
    global _SINK
    if _SINK is not None:
        return _SINK
    async with _INIT_LOCK:
        if _SINK is None:
            sink = TrajectorySink()
            try:
                await sink.start()
            except Exception:
                logger.exception(
                    "trajectory: sink start failed; returning unstarted sink"
                )
            _SINK = sink
    return _SINK


async def shutdown_trajectory_sink() -> None:
    global _SINK
    sink, _SINK = _SINK, None
    if sink is not None:
        try:
            await sink.stop()
        except Exception:
            logger.exception("trajectory: shutdown failed")


def _reset_singleton_for_tests() -> None:
    """Drop the cached sink without awaiting (tests only)."""
    global _SINK
    _SINK = None


__all__ = [
    "DEFAULT_TOPIC",
    "DLQ_TOPIC",
    "TrajectorySink",
    "get_trajectory_sink",
    "shutdown_trajectory_sink",
    # helpers kept for unit tests
    "_event_to_payload",
    "_event_to_row",
]


def make_turn_event(
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    space_id: uuid.UUID | None,
    started_at: float,
    message_preview: str,
    platform: str | None,
    outcome: str = "ok",
    model: str | None = None,
) -> TrajectoryEvent:
    """Convenience builder for ``/chat``-style handlers."""
    latency_ms = max(0, int((time.time() - started_at) * 1000))
    tags: list[str] = []
    if platform:
        tags.append(f"platform:{platform}")
    return TrajectoryEvent(
        id=uuid.uuid4(),
        session_id=session_id,
        user_id=user_id,
        space_id=space_id,
        kind="turn",
        ts=datetime.now(tz=timezone.utc),
        latency_ms=latency_ms,
        tokens_in=None,
        tokens_out=None,
        model=model,
        outcome=outcome,  # type: ignore[arg-type]
        data={"message_preview": (message_preview or "")[:200]},
        tags=tags,
        metadata={},
    )
