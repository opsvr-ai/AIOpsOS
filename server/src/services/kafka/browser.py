"""Transient Kafka consumer used by the admin UI to browse messages.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.4 / R-5.4.

The browser is deliberately not a singleton: each call spins up its own
consumer with ``group_id=None`` so nothing is committed or rebalanced in
production consumer groups. Invocations return at most ``limit`` messages,
with a server-side timeout (default 3s) to keep the UI responsive when the
tail of a partition is empty.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class BrowserMessage:
    topic: str
    partition: int
    offset: int
    timestamp: int | None
    key: str | None
    value: str | None
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - trivial
        return {
            "topic": self.topic,
            "partition": self.partition,
            "offset": self.offset,
            "timestamp": self.timestamp,
            "key": self.key,
            "value": self.value,
            "headers": self.headers,
        }


def _decode(value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        return value.decode("utf-8", errors="replace")
    except Exception:
        return repr(value)


class KafkaBrowser:
    def __init__(
        self,
        bootstrap_servers: str | None = None,
        *,
        client_id: str = "aiopsos-browser",
        default_timeout_s: float = 3.0,
    ) -> None:
        self._bootstrap = bootstrap_servers or settings.kafka_bootstrap_servers
        self._client_id = client_id
        self._default_timeout_s = default_timeout_s

    async def fetch(
        self,
        topic: str,
        *,
        partition: int | None = None,
        start_offset: int | str = "latest",
        limit: int = 50,
        key_regex: str | None = None,
        value_regex: str | None = None,
        header_regex: str | None = None,
        timeout_s: float | None = None,
    ) -> list[BrowserMessage]:
        """Read at most ``limit`` messages from a topic.

        ``start_offset`` semantics:
          * ``"earliest"`` — beginning of the log
          * ``"latest"``   — read only messages produced after we subscribe
          * positive int   — absolute offset
          * negative int   — tail: ``end_offset + start_offset`` (so ``-100``
            reads the last 100 messages)
        """
        from aiokafka import AIOKafkaConsumer
        from aiokafka.structs import TopicPartition

        if limit <= 0:
            return []
        effective_timeout = timeout_s or self._default_timeout_s

        key_pat = re.compile(key_regex) if key_regex else None
        val_pat = re.compile(value_regex) if value_regex else None
        hdr_pat = re.compile(header_regex) if header_regex else None

        consumer = AIOKafkaConsumer(
            bootstrap_servers=self._bootstrap,
            group_id=None,
            enable_auto_commit=False,
            client_id=self._client_id,
            request_timeout_ms=int(effective_timeout * 1000) + 5000,
            # Starting position; will be overridden per-partition via seek.
            auto_offset_reset="latest",
        )
        await consumer.start()
        try:
            # Resolve partitions
            available = consumer.partitions_for_topic(topic)
            if not available:
                # Refresh metadata once
                try:
                    await consumer.topics()
                    available = consumer.partitions_for_topic(topic)
                except Exception:  # pragma: no cover
                    pass
            if not available:
                return []
            if partition is not None:
                if partition not in available:
                    raise LookupError(f"topic {topic} has no partition {partition}")
                tps = [TopicPartition(topic, partition)]
            else:
                tps = [TopicPartition(topic, p) for p in sorted(available)]

            consumer.assign(tps)

            # Determine starting offset per partition
            await self._seek(consumer, tps, start_offset)

            out: list[BrowserMessage] = []
            deadline = asyncio.get_running_loop().time() + effective_timeout
            while len(out) < limit:
                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                if remaining <= 0:
                    break
                try:
                    batch = await consumer.getmany(timeout_ms=int(remaining * 1000), max_records=limit - len(out))
                except Exception:
                    logger.exception("getmany failed")
                    break
                if not batch:
                    break
                got_any = False
                for _tp, records in batch.items():
                    for rec in records:
                        got_any = True
                        headers = {}
                        for hk, hv in rec.headers or ():
                            headers[str(hk)] = _decode(hv) or ""
                        msg = BrowserMessage(
                            topic=rec.topic,
                            partition=rec.partition,
                            offset=rec.offset,
                            timestamp=int(rec.timestamp) if rec.timestamp else None,
                            key=_decode(rec.key),
                            value=_decode(rec.value),
                            headers=headers,
                        )
                        if not self._matches(msg, key_pat, val_pat, hdr_pat):
                            continue
                        out.append(msg)
                        if len(out) >= limit:
                            break
                    if len(out) >= limit:
                        break
                if not got_any:
                    break
            return out
        finally:
            await consumer.stop()

    @staticmethod
    async def _seek(consumer: Any, tps: list[Any], start_offset: int | str) -> None:
        """Seek each partition to the requested start position."""
        if start_offset == "earliest":
            await consumer.seek_to_beginning(*tps)
            return
        if start_offset == "latest":
            await consumer.seek_to_end(*tps)
            return
        try:
            target = int(start_offset)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid start_offset {start_offset!r}; expected 'earliest' | 'latest' | int"
            ) from exc

        if target >= 0:
            for tp in tps:
                consumer.seek(tp, target)
            return

        # Negative: tail = end + target
        end = await consumer.end_offsets(tps)
        for tp in tps:
            new_off = max(0, int(end.get(tp, 0)) + target)
            consumer.seek(tp, new_off)

    @staticmethod
    def _matches(
        msg: BrowserMessage,
        key_pat: re.Pattern[str] | None,
        val_pat: re.Pattern[str] | None,
        hdr_pat: re.Pattern[str] | None,
    ) -> bool:
        if key_pat is not None and not (msg.key and key_pat.search(msg.key)):
            return False
        if val_pat is not None and not (msg.value and val_pat.search(msg.value)):
            return False
        if hdr_pat is not None:
            blob = "\n".join(f"{k}={v}" for k, v in (msg.headers or {}).items())
            if not hdr_pat.search(blob):
                return False
        return True


__all__ = ["BrowserMessage", "KafkaBrowser"]
