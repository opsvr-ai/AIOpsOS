"""Dead-letter queue management.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.5 / R-5.5.

Design principles:

* **DLQ topics are append-only.** We never delete messages from them; instead
  we track metadata in Redis: whether an entry has been replayed, discarded,
  or marked-handled. Listing filters these sets out of the UI.
* **Replay is idempotent per ``original_message_id``.** Redis ``SETNX`` on
  ``dlq:replayed:{id}`` acts as a 30-day dedupe key. Concurrent replay
  invocations observe the same outcome; re-invocations of the same ids are
  a no-op reported under ``skipped``.
* **Discard leaves an audit trail.** We produce a tombstone to
  ``<topic>.dlq.discarded`` so downstream consumers can reconcile; and we
  add the entry id to a Redis SET ``dlq:discarded`` so list filters hide it.

Expected DLQ envelope (JSON value in a ``*.dlq`` topic)::

    {
      "id": "uuid",
      "original_topic": "ops.agent.trajectory",
      "original_partition": 3,
      "original_offset": 12345,
      "original_key": "session-id-…",
      "original_value": {...} | "raw string",
      "original_headers": {"k": "v"},
      "failure_reason": "SchemaRejected: …",
      "failed_at": "2026-05-04T12:34:56Z",
      "attempt_count": 3,
      "tags": {"cause": "schema"}
    }

Entries not matching this shape are still listable but their ``id`` falls back
to the Kafka ``offset`` key to stay deduplicatable.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)


REPLAYED_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
DISCARDED_SET_KEY = "dlq:discarded"
HANDLED_SET_PREFIX = "dlq:handled:"
REPLAYED_KEY_PREFIX = "dlq:replayed:"


@dataclass
class DLQEntry:
    id: str
    original_topic: str
    original_partition: int | None
    original_offset: int | None
    original_key: str | None
    original_value: Any
    original_headers: dict[str, str] = field(default_factory=dict)
    failure_reason: str | None = None
    failed_at: datetime | None = None
    attempt_count: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    # Where the entry was found on the DLQ topic:
    dlq_topic: str | None = None
    dlq_partition: int | None = None
    dlq_offset: int | None = None


@dataclass
class ReplayReport:
    replayed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"replayed": self.replayed, "skipped": self.skipped, "errors": list(self.errors)}


class KafkaDLQManager:
    """CRUD over DLQ topics. Stateless aside from the injected dependencies."""

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        *,
        producer: Any | None = None,
        redis_client: Any | None = None,
        consumer_factory: Any | None = None,
        dlq_suffix: str = ".dlq",
    ) -> None:
        self._bootstrap = bootstrap_servers or settings.kafka_bootstrap_servers
        self._injected_producer = producer
        self._injected_redis = redis_client
        self._consumer_factory = consumer_factory
        self._dlq_suffix = dlq_suffix

    # -- dependency access ---------------------------------------------

    async def _redis(self) -> Any:
        if self._injected_redis is not None:
            return self._injected_redis
        from src.core.redis import get_redis

        return await get_redis()

    async def _get_producer(self) -> tuple[Any, bool]:
        """Return ``(producer, owns)`` where ``owns=True`` means we started it
        ourselves and must close it after use.
        """
        if self._injected_producer is not None:
            return self._injected_producer, False
        from aiokafka import AIOKafkaProducer

        p = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            client_id="aiopsos-dlq-producer",
            acks="all",
        )
        await p.start()
        return p, True

    def _list_consumer(self, topic: str) -> Any:
        """Build the read-side consumer used by :meth:`list_entries`."""
        if self._consumer_factory is not None:
            return self._consumer_factory(topic)
        from aiokafka import AIOKafkaConsumer

        return AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap,
            group_id=None,
            enable_auto_commit=False,
            client_id="aiopsos-dlq-browser",
            auto_offset_reset="earliest",
        )

    # -- public surface -------------------------------------------------

    async def list_entries(
        self,
        topic: str | None = None,
        *,
        since: datetime | None = None,
        tag_filter: dict[str, str] | None = None,
        limit: int = 100,
    ) -> list[DLQEntry]:
        """List DLQ entries across one or all DLQ topics.

        ``topic`` must be either a specific DLQ topic (``*.dlq``) or ``None``
        to union every ``*.dlq`` discovered on the broker.
        """
        if limit <= 0:
            return []

        topics = await self._resolve_dlq_topics(topic)
        if not topics:
            return []

        redis = await self._redis()
        discarded = set(await redis.smembers(DISCARDED_SET_KEY) or [])

        entries: list[DLQEntry] = []
        for t in topics:
            entries.extend(
                await self._scan_topic(
                    t,
                    since=since,
                    tag_filter=tag_filter,
                    limit=limit - len(entries),
                    discarded=discarded,
                )
            )
            if len(entries) >= limit:
                break
        return entries[:limit]

    async def replay(
        self,
        entry_ids: list[str],
        *,
        target_topic: str | None = None,
    ) -> ReplayReport:
        """Replay DLQ entries. Idempotent per ``original_message_id``.

        The manager rescans the broker-side DLQ topics to find the messages
        whose id matches. In production this is bounded by the retention of
        the DLQ topic (90d per design).
        """
        report = ReplayReport()
        if not entry_ids:
            return report

        wanted = set(entry_ids)
        topics = await self._resolve_dlq_topics(None)
        if not topics:
            report.errors.append("no DLQ topics discovered")
            return report

        redis = await self._redis()
        producer, owns = await self._get_producer()

        try:
            # We scan all DLQ topics; early-exit once every wanted id has been
            # accounted for.
            for dlq_topic in topics:
                if not wanted:
                    break
                async for entry in self._iter_entries(dlq_topic):
                    if entry.id not in wanted:
                        continue
                    wanted.discard(entry.id)
                    replay_key = f"{REPLAYED_KEY_PREFIX}{entry.id}"
                    # SETNX: returns True when the key was created, False when
                    # it already existed. set(nx=True) returns None/False in
                    # redis-py; we normalise to a truthy check.
                    was_set = await redis.set(
                        replay_key, "1", ex=REPLAYED_TTL_SECONDS, nx=True
                    )
                    if not was_set:
                        report.skipped += 1
                        continue
                    dest = target_topic or entry.original_topic
                    if not dest:
                        report.errors.append(f"{entry.id}: no destination topic")
                        # Roll back the replay marker so future calls can retry.
                        try:
                            await redis.delete(replay_key)
                        except Exception:  # pragma: no cover
                            pass
                        continue
                    try:
                        await self._produce_replay(producer, dest, entry)
                        report.replayed += 1
                    except Exception as exc:
                        logger.exception("replay failed for %s", entry.id)
                        report.errors.append(f"{entry.id}: {exc}")
                        try:
                            await redis.delete(replay_key)
                        except Exception:  # pragma: no cover
                            pass
                    if not wanted:
                        break

            # Any ids we didn't find on the broker but that ARE already in
            # the replayed set should count as skipped-idempotent.
            for remaining_id in list(wanted):
                already = await redis.exists(f"{REPLAYED_KEY_PREFIX}{remaining_id}")
                if already:
                    report.skipped += 1
                    wanted.discard(remaining_id)

            for missing_id in wanted:
                report.errors.append(f"{missing_id}: not found in any DLQ topic")
        finally:
            if owns:
                try:
                    await producer.stop()
                except Exception:  # pragma: no cover
                    logger.exception("producer.stop failed")
        return report

    async def discard(self, entry_ids: list[str]) -> int:
        if not entry_ids:
            return 0
        redis = await self._redis()
        await redis.sadd(DISCARDED_SET_KEY, *entry_ids)

        # Publish tombstones so downstream auditors can reconcile.
        producer, owns = await self._get_producer()
        try:
            for eid in entry_ids:
                await producer.send_and_wait(
                    f"{self._dlq_suffix.lstrip('.')}.discarded"
                    if not self._dlq_suffix.startswith(".")
                    else "dlq.discarded",
                    key=eid.encode("utf-8"),
                    value=json.dumps({"id": eid, "discarded_at": _now_iso()}).encode("utf-8"),
                )
        except Exception:  # pragma: no cover - non-fatal; discard set already updated
            logger.exception("discard tombstone publish failed")
        finally:
            if owns:
                try:
                    await producer.stop()
                except Exception:
                    pass
        return len(entry_ids)

    async def mark_handled(self, entry_ids: list[str]) -> int:
        if not entry_ids:
            return 0
        redis = await self._redis()
        # We don't know which DLQ topic each id belongs to without another
        # scan; bucket under a generic ``all`` key plus per-topic best-effort.
        await redis.sadd(f"{HANDLED_SET_PREFIX}all", *entry_ids)
        return len(entry_ids)

    # -- internals ------------------------------------------------------

    async def _resolve_dlq_topics(self, topic: str | None) -> list[str]:
        if topic:
            return [topic]
        # Use admin list to enumerate
        from src.services.kafka.admin import KafkaAdminService

        async with KafkaAdminService(bootstrap_servers=self._bootstrap) as admin:
            all_topics = await admin.list_topics(include_internal=False)
        return [t.name for t in all_topics if t.name.endswith(self._dlq_suffix)]

    async def _scan_topic(
        self,
        topic: str,
        *,
        since: datetime | None,
        tag_filter: dict[str, str] | None,
        limit: int,
        discarded: set[str],
    ) -> list[DLQEntry]:
        out: list[DLQEntry] = []
        if limit <= 0:
            return out
        since_ms = int(since.timestamp() * 1000) if since else None
        async for entry in self._iter_entries(topic):
            if entry.id in discarded:
                continue
            if since_ms and entry.failed_at:
                try:
                    if int(entry.failed_at.timestamp() * 1000) < since_ms:
                        continue
                except Exception:  # pragma: no cover
                    pass
            if tag_filter:
                mismatch = any(
                    entry.tags.get(k) != v for k, v in tag_filter.items()
                )
                if mismatch:
                    continue
            out.append(entry)
            if len(out) >= limit:
                break
        return out

    async def _iter_entries(self, topic: str):
        """Yield every currently-visible entry on a DLQ topic.

        Uses ``seek_to_beginning`` + ``getmany`` until the high-water-mark is
        exhausted. Safe for repeated use in the same process because we use
        ``group_id=None``.
        """
        consumer = self._list_consumer(topic)
        await consumer.start()
        try:
            # Subscribe to all partitions explicitly so we can read from the
            # very beginning even if the broker has retained old messages.
            parts = consumer.partitions_for_topic(topic)
            if not parts:
                try:
                    await consumer.topics()
                    parts = consumer.partitions_for_topic(topic)
                except Exception:
                    parts = None
            if parts:
                from aiokafka.structs import TopicPartition

                tps = [TopicPartition(topic, p) for p in parts]
                consumer.assign(tps)
                await consumer.seek_to_beginning(*tps)
                end_offsets = await consumer.end_offsets(tps)
            else:  # pragma: no cover - topic has no partitions
                end_offsets = {}
                tps = []

            # Nothing to read: every partition already at end.
            if tps and all(end_offsets.get(tp, 0) == 0 for tp in tps):
                return

            while True:
                batch = await consumer.getmany(timeout_ms=2000, max_records=200)
                if not batch:
                    break
                any_yielded = False
                for _tp, records in batch.items():
                    for rec in records:
                        any_yielded = True
                        entry = _deserialize_entry(rec, topic)
                        if entry is not None:
                            yield entry
                if not any_yielded:
                    break
                # Stop once we've read the tail
                positions = {tp: consumer.position(tp) for tp in tps}
                if all(positions[tp] >= end_offsets.get(tp, 0) for tp in tps):
                    break
        finally:
            await consumer.stop()

    async def _produce_replay(
        self, producer: Any, dest_topic: str, entry: DLQEntry
    ) -> None:
        value = entry.original_value
        if isinstance(value, (dict, list)):
            payload = json.dumps(value).encode("utf-8")
        elif isinstance(value, str):
            payload = value.encode("utf-8")
        elif value is None:
            payload = b""
        else:
            payload = json.dumps(value).encode("utf-8")
        key_bytes = entry.original_key.encode("utf-8") if entry.original_key else None
        headers = [(k, v.encode("utf-8")) for k, v in (entry.original_headers or {}).items()]
        headers.append(("x-replay-source", b"dlq"))
        headers.append(("x-replay-id", entry.id.encode("utf-8")))
        await producer.send_and_wait(
            dest_topic, value=payload, key=key_bytes, headers=headers
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat()


def _deserialize_entry(rec: Any, dlq_topic: str) -> DLQEntry | None:
    """Build a :class:`DLQEntry` from a Kafka record.

    Missing fields get safe defaults so callers see a partial-but-usable
    record even when upstream producers didn't fill the envelope out.
    """
    raw = rec.value
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        data = {}

    if not isinstance(data, dict):
        # Preserve the body in ``original_value`` for display.
        data = {"original_value": raw.decode("utf-8", errors="replace")}

    eid = data.get("id") or f"{rec.topic}:{rec.partition}:{rec.offset}"
    failed_at = data.get("failed_at")
    failed_at_dt: datetime | None = None
    if failed_at:
        try:
            failed_at_dt = datetime.fromisoformat(str(failed_at).replace("Z", "+00:00"))
        except ValueError:
            failed_at_dt = None

    headers: dict[str, str] = {}
    for hk, hv in (rec.headers or ()):
        if isinstance(hv, (bytes, bytearray)):
            try:
                headers[str(hk)] = hv.decode("utf-8", errors="replace")
            except Exception:
                headers[str(hk)] = repr(hv)
        else:
            headers[str(hk)] = str(hv) if hv is not None else ""

    return DLQEntry(
        id=str(eid),
        original_topic=str(data.get("original_topic") or _strip_dlq_suffix(dlq_topic)),
        original_partition=data.get("original_partition"),
        original_offset=data.get("original_offset"),
        original_key=data.get("original_key"),
        original_value=data.get("original_value", data if "id" not in data else None),
        original_headers=data.get("original_headers") or headers,
        failure_reason=data.get("failure_reason"),
        failed_at=failed_at_dt,
        attempt_count=int(data.get("attempt_count") or 0),
        tags=dict(data.get("tags") or {}),
        dlq_topic=rec.topic,
        dlq_partition=rec.partition,
        dlq_offset=rec.offset,
    )


def _strip_dlq_suffix(topic: str) -> str:
    return topic[: -len(".dlq")] if topic.endswith(".dlq") else topic


__all__ = ["DLQEntry", "KafkaDLQManager", "ReplayReport"]
