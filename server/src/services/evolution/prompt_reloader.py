"""Kafka consumer that feeds :class:`SubAgentPromptRegistry` hot-reload events.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 20.1 /
R-3.15 (5s convergence), R-3.17 (per-instance consumer group),
R-3.18 (idempotent replay).

Design (from ``design.md § PromptReloader``):

* Subscribes to topic ``ops.agent.promotion``.
* Consumer group is ``prompt-reloader-{instance_id}`` — every FastAPI
  process gets its **own** group so every replica receives every
  promotion event. This is deliberate: we are fan-out, not fan-in.
* ``auto_offset_reset=latest``. Historical events are already
  materialised in the DB; :meth:`SubAgentPromptRegistry.load` reads
  them on startup. Replaying the Kafka log on every boot would be
  redundant and slow.
* Only ``kind=prompt_patch`` events are handled. ``skill`` and
  ``tool_config`` are routed through ``tool_manager.invalidate_cache()``
  (task 23, outside this module's scope).
* Handler errors are **never** fatal for the loop — each event is
  wrapped in try/except so a malformed message doesn't stop consumption.

The reloader is designed to be plugged into FastAPI's lifespan:

    reloader = PromptReloader(registry)
    await reloader.start()
    ...
    await reloader.stop()

It owns no state the registry doesn't; if it dies, the registry
continues to serve whatever it last successfully loaded. The Promoter
can always call ``/api/control/evolution/force-reload`` to trigger
:meth:`SubAgentPromptRegistry.refresh` out-of-band.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from src.config import settings
from src.core.instance import instance_id
from src.services.evolution.prompt_registry import (
    PromotionEvent,
    SubAgentPromptRegistry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


PROMOTION_TOPIC = "ops.agent.promotion"

# Candidate kinds this reloader cares about. Everything else is routed
# through other hot-reload mechanisms (see design.md § Candidate 多类型).
_HANDLED_KINDS: frozenset[str] = frozenset({"prompt_patch"})


# Type alias for an injected consumer factory — tests pass a fake
# async iterator; prod passes :func:`_default_consumer_factory` which
# builds an AIOKafkaConsumer.
ConsumerFactory = Callable[..., Awaitable[Any]]


# ---------------------------------------------------------------------------
# Reloader
# ---------------------------------------------------------------------------


class PromptReloader:
    """Background consumer that drives ``SubAgentPromptRegistry`` from Kafka.

    Construction is cheap; all I/O is deferred to :meth:`start`. Tests
    can inject a fake ``consumer_factory`` that returns an async
    iterator of messages — no real broker required.

    Lifecycle:

    * ``start()`` — build the consumer, spawn the background task.
      Idempotent; a second call is a no-op.
    * ``stop()`` — signal shutdown, wait up to ``stop_timeout_s`` for
      the loop to exit, then cancel. Also best-effort closes the
      consumer.

    Observability: each successfully-handled ``prompt_patch`` event is
    counted in ``evolution_prompt_reload_total``; handler errors bump
    ``evolution_prompt_reload_error_total``. The metrics are created
    here rather than in ``core/metrics.py`` so they stay co-located
    with the code that owns them — adding them later to the central
    module is a trivial move.
    """

    def __init__(
        self,
        registry: SubAgentPromptRegistry,
        *,
        bootstrap_servers: str | None = None,
        topic: str = PROMOTION_TOPIC,
        consumer_factory: ConsumerFactory | None = None,
        group_id: str | None = None,
        stop_timeout_s: float = 5.0,
    ) -> None:
        self._registry = registry
        self._bootstrap = bootstrap_servers or settings.kafka_bootstrap_servers
        self._topic = topic
        self._consumer_factory = consumer_factory or _default_consumer_factory
        # Lazy-resolved: we only need the instance_id when we actually
        # start the consumer, and tests can inject ``group_id`` directly
        # rather than setting a dummy instance id.
        self._group_id_override = group_id
        self._stop_timeout_s = float(stop_timeout_s)

        self._consumer: Any | None = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._started = False

        # Metrics — imported lazily so the module can be loaded in
        # test environments that stripped out prometheus_client.
        self._metric_reload_ok, self._metric_reload_err = _load_metrics()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def group_id(self) -> str:
        """Return the Kafka consumer group id this reloader uses."""
        if self._group_id_override:
            return self._group_id_override
        return f"prompt-reloader-{instance_id()}"

    async def start(self) -> None:
        """Build the consumer and spawn the background task.

        A second call is a no-op. If consumer construction fails we
        log + record the error but do NOT raise — the process should
        still boot and serve traffic, just without hot-reload.
        """
        if self._started:
            return
        self._started = True
        self._stop_event.clear()

        try:
            self._consumer = await self._consumer_factory(
                topic=self._topic,
                bootstrap_servers=self._bootstrap,
                group_id=self.group_id,
                auto_offset_reset="latest",
            )
        except Exception:
            logger.exception(
                "prompt_reloader: consumer construction failed; "
                "hot-reload disabled"
            )
            self._consumer = None
            self._started = False
            return

        self._task = asyncio.create_task(
            self._run(), name="prompt-reloader"
        )
        logger.info(
            "prompt_reloader: started (topic=%s group=%s)",
            self._topic,
            self.group_id,
        )

    async def stop(self) -> None:
        """Stop the background loop and release broker resources."""
        if not self._started:
            return
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None:
            # Cancel first — the loop is likely parked inside the
            # consumer's async iterator waiting on a network poll, so
            # even though we set stop_event the iterator won't yield
            # until the next message arrives. Cancelling wakes it up
            # immediately and is safe because the consumer itself is
            # then closed below.
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=self._stop_timeout_s)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                logger.exception("prompt_reloader: stop wait raised")

        consumer, self._consumer = self._consumer, None
        if consumer is not None:
            try:
                await consumer.stop()
            except Exception:
                logger.exception("prompt_reloader: consumer.stop failed")

        self._started = False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Consume messages forever, dispatching each to :meth:`_handle`."""
        consumer = self._consumer
        if consumer is None:
            return
        try:
            async for msg in consumer:
                if self._stop_event.is_set():
                    break
                try:
                    await self._handle(msg)
                except Exception:
                    # Single-event failures are non-fatal to the loop.
                    # We rely on PromotionEvent idempotency (R-3.18) to
                    # make replay safe; if that guarantee is ever
                    # broken this is the single place you'd add a DLQ.
                    if self._metric_reload_err is not None:
                        try:
                            self._metric_reload_err.inc()
                        except Exception:  # pragma: no cover
                            pass
                    logger.exception(
                        "prompt_reloader: failed on topic=%s offset=%s",
                        getattr(msg, "topic", self._topic),
                        getattr(msg, "offset", "?"),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Broker disconnect, decode failure at iterator level, etc.
            # We don't auto-reconnect here — the registry still works
            # from the initial DB load, and a process restart (or a
            # retry in a supervisor) is the intended recovery.
            logger.exception(
                "prompt_reloader: consumer loop terminated abnormally"
            )

    async def _handle(self, msg: Any) -> None:
        """Decode one Kafka message and apply it to the registry.

        Only ``kind=prompt_patch`` events cause work; all others are
        silently skipped (they flow to other hot-reload mechanisms).
        Silently here means: we still advance past the message — the
        event is not "owed" to this consumer group any more.
        """
        raw = getattr(msg, "value", None)
        if raw is None:
            return

        try:
            if isinstance(raw, (bytes, bytearray)):
                payload = json.loads(raw.decode("utf-8"))
            elif isinstance(raw, str):
                payload = json.loads(raw)
            elif isinstance(raw, dict):
                payload = raw
            else:
                logger.debug(
                    "prompt_reloader: unsupported msg value type %s",
                    type(raw),
                )
                return
        except (ValueError, UnicodeDecodeError):
            logger.exception(
                "prompt_reloader: JSON decode failed, offset=%s",
                getattr(msg, "offset", "?"),
            )
            if self._metric_reload_err is not None:
                try:
                    self._metric_reload_err.inc()
                except Exception:  # pragma: no cover
                    pass
            return

        if not isinstance(payload, dict):
            return

        kind = str(payload.get("kind", "")).strip()
        if kind not in _HANDLED_KINDS:
            # skill / tool_config / anything else — not ours.
            return

        event = self._build_event(payload)
        if event is None:
            return

        applied = await self._registry.apply_promotion(event)
        if applied and self._metric_reload_ok is not None:
            try:
                self._metric_reload_ok.labels(
                    sub_agent=event.sub_agent_name or "<unknown>",
                    to_status=event.to_status,
                ).inc()
            except Exception:  # pragma: no cover
                pass
        logger.info(
            "prompt_reloader: %s event_id=%s target=%s to_status=%s version=%s",
            "applied" if applied else "skipped",
            event.event_id,
            event.sub_agent_name,
            event.to_status,
            event.new_version_id,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_event(payload: dict[str, Any]) -> PromotionEvent | None:
        """Translate a Kafka payload into a :class:`PromotionEvent`.

        The promoter emits events with these fields:

        * ``kind`` — filtered upstream, always ``"prompt_patch"`` here
        * ``target_ref`` — sub-agent name
        * ``new_version_id`` — id of the row to re-fetch
        * ``to_status`` — expected DB status after apply
        * ``event_id`` (optional) — dedupe key; we synthesise from
          the Kafka message id if absent so replays still dedupe
        """
        new_id = payload.get("new_version_id") or payload.get("version_id")
        to_status = payload.get("to_status") or payload.get("status")
        if not new_id or not to_status:
            logger.debug(
                "prompt_reloader: ignoring malformed event: %s", payload
            )
            return None

        event_id = str(
            payload.get("event_id")
            or payload.get("id")
            or f"{payload.get('target_ref', '')}:{new_id}:{to_status}"
        )
        return PromotionEvent(
            event_id=event_id,
            new_version_id=str(new_id),
            to_status=str(to_status),  # type: ignore[arg-type]
            sub_agent_name=(
                str(payload["target_ref"])
                if payload.get("target_ref")
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Default consumer factory
# ---------------------------------------------------------------------------


async def _default_consumer_factory(
    *,
    topic: str,
    bootstrap_servers: str,
    group_id: str,
    auto_offset_reset: str = "latest",
) -> Any:
    """Build and start a real ``AIOKafkaConsumer`` for ``topic``.

    Isolated as a module-level function so tests can bypass it with a
    fake that yields synthetic messages. Production callers get a
    configured, started consumer they can iterate directly.
    """
    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        auto_offset_reset=auto_offset_reset,
        enable_auto_commit=True,
        client_id="aiopsos-prompt-reloader",
    )
    await consumer.start()
    return consumer


# ---------------------------------------------------------------------------
# Metric wiring
# ---------------------------------------------------------------------------


def _load_metrics() -> tuple[Any | None, Any | None]:
    """Return ``(reload_ok_counter, reload_err_counter)`` or ``(None, None)``.

    Uses the global prometheus registry so duplicate imports don't
    double-register. If prometheus_client is unavailable or registration
    fails, we fall back to ``None`` and the reloader silently skips
    metrics — the event path keeps working.
    """
    try:
        from prometheus_client import REGISTRY, Counter

        # Re-use existing metric if already registered (helpful for
        # uvicorn-reload test scenarios).
        existing_ok = _find_existing(REGISTRY, "evolution_prompt_reload_total")
        existing_err = _find_existing(REGISTRY, "evolution_prompt_reload_error_total")
        ok = existing_ok or Counter(
            "evolution_prompt_reload_total",
            "PromptReloader successful apply_promotion count.",
            labelnames=("sub_agent", "to_status"),
        )
        err = existing_err or Counter(
            "evolution_prompt_reload_error_total",
            "PromptReloader failures (decode / apply).",
        )
        return ok, err
    except Exception:  # pragma: no cover
        logger.debug("prompt_reloader: prometheus metrics unavailable")
        return None, None


def _find_existing(registry: Any, name: str) -> Any | None:
    """Return the Counter currently registered for ``name`` if any."""
    try:
        # ``_names_to_collectors`` is stable across prometheus_client versions.
        collectors = getattr(registry, "_names_to_collectors", None)
        if isinstance(collectors, dict):
            return collectors.get(name)
    except Exception:  # pragma: no cover
        pass
    return None


__all__ = [
    "PROMOTION_TOPIC",
    "PromptReloader",
]
