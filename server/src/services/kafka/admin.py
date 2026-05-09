"""Async wrapper around :class:`aiokafka.admin.AIOKafkaAdminClient`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.2.

The goal is to present a strongly-typed, broker-agnostic facade to the rest of
the platform. Callers must never see aiokafka dataclasses — everything is
marshalled into plain pydantic BaseModels defined in this module.

All methods are idempotent-friendly: ``create_topic`` treats
``TopicAlreadyExistsError`` as a no-op, ``alter_topic`` is a partial update,
and ``delete_topic`` requires ``confirm=True`` to guard against mass deletion
from an errant admin API call.

The service itself is an async context manager::

    async with KafkaAdminService() as admin:
        topics = await admin.list_topics()

but may also be used as a long-lived singleton by calling :meth:`start` /
:meth:`close` manually.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from src.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public DTOs (never leak aiokafka types)
# ---------------------------------------------------------------------------


class PartitionLag(BaseModel):
    """Per-partition lag breakdown for a single consumer group + topic."""

    topic: str
    partition: int
    current_offset: int  # last committed offset by the group; -1 if unknown
    end_offset: int  # log-end-offset of the partition
    lag: int  # end_offset - current_offset, clamped at 0

    @property
    def is_healthy(self) -> bool:  # pragma: no cover - trivial
        return self.lag >= 0


class MemberInfo(BaseModel):
    """A single member of a consumer group."""

    member_id: str
    client_id: str
    client_host: str | None = None
    assignments: list[str] = Field(default_factory=list)  # "topic:partition"


class ConsumerGroupInfo(BaseModel):
    """Lightweight listing entry for a consumer group."""

    group_id: str
    state: str | None = None  # "Stable", "Empty", etc. May be None on older brokers.
    protocol_type: str | None = None


class ConsumerGroupDetail(BaseModel):
    """Full describe result for a consumer group."""

    group_id: str
    state: str | None = None
    protocol: str | None = None
    protocol_type: str | None = None
    members: list[MemberInfo] = Field(default_factory=list)
    lags: list[PartitionLag] = Field(default_factory=list)

    @property
    def total_lag(self) -> int:  # pragma: no cover - trivial
        return sum(max(0, p.lag) for p in self.lags)


class TopicInfo(BaseModel):
    """Describe result for a single topic."""

    name: str
    partitions: int
    replication_factor: int
    configs: dict[str, str] = Field(default_factory=dict)
    internal: bool = False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class KafkaAdminService:
    """Async wrapper around :class:`aiokafka.admin.AIOKafkaAdminClient`.

    The wrapper lazily connects on ``start()`` / async-context-enter so tests
    can construct the service without hitting a broker.
    """

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        *,
        client_id: str = "aiopsos-admin",
        request_timeout_ms: int = 30_000,
    ) -> None:
        self._bootstrap = bootstrap_servers or settings.kafka_bootstrap_servers
        self._client_id = client_id
        self._request_timeout_ms = request_timeout_ms
        self._client: Any = None  # AIOKafkaAdminClient at runtime

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self._client is not None:
            return
        # Lazy import so ``import src.services.kafka.admin`` does not fail
        # when aiokafka is missing from a minimal install.
        from aiokafka.admin import AIOKafkaAdminClient

        self._client = AIOKafkaAdminClient(
            bootstrap_servers=self._bootstrap,
            client_id=self._client_id,
            request_timeout_ms=self._request_timeout_ms,
        )
        await self._client.start()

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.close()
        finally:
            self._client = None

    async def __aenter__(self) -> "KafkaAdminService":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def _ensure(self) -> Any:
        if self._client is None:
            raise RuntimeError(
                "KafkaAdminService not started; call `await svc.start()` or use "
                "`async with KafkaAdminService() as svc:`"
            )
        return self._client

    # -- topics ---------------------------------------------------------

    async def list_topics(self, include_internal: bool = False) -> list[TopicInfo]:
        """List all topics known to the cluster.

        Per-topic configs are fetched in a second admin call so the return
        value is self-contained. For very large clusters this is O(N) in the
        number of topics; that's acceptable for an admin UI surface.
        """
        client = self._ensure()
        names = await client.list_topics()
        if not include_internal:
            names = [n for n in names if not n.startswith("_")]
        if not names:
            return []
        return await self._describe_many(names)

    async def describe_topic(self, name: str) -> TopicInfo:
        infos = await self._describe_many([name])
        if not infos:
            raise LookupError(f"topic not found: {name}")
        return infos[0]

    async def _describe_many(self, names: list[str]) -> list[TopicInfo]:
        client = self._ensure()
        described = await client.describe_topics(names)
        # describe_topics → list[dict]
        # {'error_code', 'topic', 'is_internal', 'partitions': [ {partition, leader, replicas, isr, ...} ]}

        # Fetch configs in one batch
        from aiokafka.admin.config_resource import ConfigResource, ConfigResourceType

        resources = [ConfigResource(ConfigResourceType.TOPIC, n) for n in names]
        cfg_responses: list[Any] = []
        try:
            cfg_responses = await client.describe_configs(resources)
        except Exception:  # pragma: no cover - network flakiness path
            logger.warning("describe_configs failed for %s; returning empty configs", names)

        # Build name → configs dict
        configs_by_topic: dict[str, dict[str, str]] = {}
        for resp in cfg_responses or []:
            for res in getattr(resp, "resources", []):
                # res = (error_code, error_message, resource_type, resource_name, config_entries)
                resource_name = res[3] if len(res) > 3 else None
                entries = res[4] if len(res) > 4 else []
                if not resource_name:
                    continue
                cfgs: dict[str, str] = {}
                for entry in entries or []:
                    # entry = (config_names, config_value, read_only, config_source, is_sensitive, synonyms)
                    try:
                        k, v = entry[0], entry[1]
                    except (IndexError, TypeError):
                        continue
                    if v is None:
                        continue
                    cfgs[str(k)] = str(v)
                configs_by_topic[resource_name] = cfgs

        out: list[TopicInfo] = []
        for d in described:
            if not isinstance(d, dict):
                continue
            if d.get("error_code"):
                raise LookupError(f"topic not found: {d.get('topic')}")
            tname = d.get("topic") or ""
            parts = d.get("partitions") or []
            # replication factor = length of replicas on partition 0 (assume uniform)
            repl = 0
            if parts:
                first = parts[0]
                repl = len(first.get("replicas") or []) if isinstance(first, dict) else 0
            out.append(
                TopicInfo(
                    name=tname,
                    partitions=len(parts),
                    replication_factor=repl,
                    configs=configs_by_topic.get(tname, {}),
                    internal=bool(d.get("is_internal", False)),
                )
            )
        return out

    async def create_topic(
        self,
        name: str,
        *,
        partitions: int = 3,
        replication_factor: int = 1,
        configs: dict[str, str] | None = None,
    ) -> None:
        """Create a topic. Already-existing topics become a no-op + warn."""
        client = self._ensure()
        from aiokafka.admin import NewTopic
        from aiokafka.errors import TopicAlreadyExistsError

        topic = NewTopic(
            name=name,
            num_partitions=partitions,
            replication_factor=replication_factor,
            topic_configs=dict(configs or {}),
        )
        try:
            await client.create_topics([topic])
        except TopicAlreadyExistsError:
            logger.warning("create_topic: topic %s already exists; no-op", name)

    async def alter_topic(
        self,
        name: str,
        *,
        partitions: int | None = None,
        configs: dict[str, str] | None = None,
    ) -> None:
        """Update partition count and/or configs.

        Aiokafka only supports **growing** the partition count; shrinking is
        rejected by the broker. We surface that error as-is.
        """
        client = self._ensure()
        if partitions is not None:
            from aiokafka.admin import NewPartitions

            await client.create_partitions({name: NewPartitions(total_count=partitions)})
        if configs:
            from aiokafka.admin.config_resource import ConfigResource, ConfigResourceType

            res = ConfigResource(
                ConfigResourceType.TOPIC,
                name,
                configs={str(k): str(v) for k, v in configs.items()},
            )
            await client.alter_configs([res])

    async def delete_topic(self, name: str, *, confirm: bool = False) -> None:
        if not confirm:
            raise ValueError("confirm=True required")
        client = self._ensure()
        await client.delete_topics([name])

    # -- consumer groups ------------------------------------------------

    async def list_consumer_groups(self) -> list[ConsumerGroupInfo]:
        client = self._ensure()
        rows = await client.list_consumer_groups()
        out: list[ConsumerGroupInfo] = []
        for row in rows or []:
            # Row shape differs between broker versions. Support tuple/list and dict.
            if isinstance(row, dict):
                gid = row.get("group_id") or row.get("group") or ""
                proto = row.get("protocol_type")
                state = row.get("state")
            else:
                # tuple: (group_id, protocol_type) or (group_id, protocol_type, state)
                try:
                    gid = row[0]
                except (IndexError, TypeError):
                    continue
                proto = row[1] if len(row) > 1 else None
                state = row[2] if len(row) > 2 else None
            if not gid:
                continue
            out.append(
                ConsumerGroupInfo(
                    group_id=str(gid),
                    protocol_type=str(proto) if proto else None,
                    state=str(state) if state else None,
                )
            )
        return out

    async def describe_group(self, group_id: str) -> ConsumerGroupDetail:
        """Describe a consumer group with members + per-partition lag.

        Lag is computed by fetching committed offsets via
        ``list_consumer_group_offsets`` and comparing against the log-end
        offsets via a transient ``AIOKafkaConsumer`` (since the admin client
        does not expose end offsets directly).
        """
        client = self._ensure()
        descriptions = await client.describe_consumer_groups([group_id])
        if not descriptions:
            raise LookupError(f"consumer group not found: {group_id}")

        state: str | None = None
        protocol: str | None = None
        protocol_type: str | None = None
        members_out: list[MemberInfo] = []
        assigned_tps: set[tuple[str, int]] = set()

        for resp in descriptions:
            # DescribeGroupsResponse.groups = [(error_code, group_id, state, proto_type, proto, members, ...)]
            groups = getattr(resp, "groups", None) or []
            for g in groups:
                try:
                    # aiokafka uses namedtuple-style; index access is stable.
                    _err = g[0]
                    gid = g[1]
                    state = g[2] if len(g) > 2 else state
                    protocol_type = g[3] if len(g) > 3 else protocol_type
                    protocol = g[4] if len(g) > 4 else protocol
                    members = g[5] if len(g) > 5 else []
                except (IndexError, TypeError):
                    continue
                if gid != group_id:
                    continue
                for m in members or []:
                    try:
                        mid = m[0]
                        cid = m[1]
                        host = m[2] if len(m) > 2 else None
                    except (IndexError, TypeError):
                        continue
                    # Member assignment is an opaque bytes blob; try to decode
                    assignment_strings: list[str] = []
                    member_assignment = m[4] if len(m) > 4 else None
                    if isinstance(member_assignment, (bytes, bytearray)):
                        try:
                            from kafka.coordinator.assignors.roundrobin import (
                                ConsumerProtocolMemberAssignment,
                            )

                            parsed = ConsumerProtocolMemberAssignment.decode(
                                member_assignment
                            )
                            for topic, parts in getattr(parsed, "assignment", []) or []:
                                for p in parts or []:
                                    assignment_strings.append(f"{topic}:{p}")
                                    assigned_tps.add((topic, int(p)))
                        except Exception:  # pragma: no cover - decoder path varies
                            pass
                    members_out.append(
                        MemberInfo(
                            member_id=str(mid),
                            client_id=str(cid),
                            client_host=str(host) if host is not None else None,
                            assignments=assignment_strings,
                        )
                    )

        # Committed offsets
        committed_by_tp: dict[tuple[str, int], int] = {}
        try:
            offsets = await client.list_consumer_group_offsets(group_id)
            for tp, meta in (offsets or {}).items():
                try:
                    committed_by_tp[(tp.topic, int(tp.partition))] = int(meta.offset)
                except Exception:
                    continue
        except Exception:  # pragma: no cover
            logger.warning("list_consumer_group_offsets failed for %s", group_id)

        # Collect all topic-partitions we need end offsets for
        all_tps = assigned_tps | set(committed_by_tp.keys())
        lags = await self._compute_lags(all_tps, committed_by_tp)

        return ConsumerGroupDetail(
            group_id=group_id,
            state=str(state) if state else None,
            protocol=str(protocol) if protocol else None,
            protocol_type=str(protocol_type) if protocol_type else None,
            members=members_out,
            lags=lags,
        )

    async def _compute_lags(
        self,
        tps: set[tuple[str, int]],
        committed: dict[tuple[str, int], int],
    ) -> list[PartitionLag]:
        if not tps:
            return []
        from aiokafka import AIOKafkaConsumer
        from aiokafka.structs import TopicPartition

        consumer = AIOKafkaConsumer(
            bootstrap_servers=self._bootstrap,
            client_id=f"{self._client_id}-lag",
            enable_auto_commit=False,
            group_id=None,
            request_timeout_ms=self._request_timeout_ms,
        )
        await consumer.start()
        try:
            tp_objs = [TopicPartition(topic, partition) for (topic, partition) in tps]
            try:
                end = await consumer.end_offsets(tp_objs)
            except Exception:  # pragma: no cover
                end = {}
            out: list[PartitionLag] = []
            for (topic, partition) in sorted(tps):
                tp = TopicPartition(topic, partition)
                end_off = int(end.get(tp, 0))
                cur = int(committed.get((topic, partition), -1))
                lag = max(0, end_off - cur) if cur >= 0 else 0
                out.append(
                    PartitionLag(
                        topic=topic,
                        partition=partition,
                        current_offset=cur,
                        end_offset=end_off,
                        lag=lag,
                    )
                )
            return out
        finally:
            await consumer.stop()

    # -- offset management ---------------------------------------------

    async def reset_offset(
        self,
        group_id: str,
        topic: str,
        partition: int,
        target: str,
    ) -> int:
        """Reset a consumer group's committed offset for one partition.

        ``target`` ∈ ``{"earliest", "latest", "<int>", "<iso8601>"}``.

        Returns the new committed offset.

        Implementation: create a transient consumer with the target
        ``group_id``, seek to the requested position, commit, and return.
        The group MUST be idle (``Empty`` state) for this to succeed; otherwise
        the broker rejects the commit. Callers should stop all consumers in
        the group before invoking this.
        """
        from aiokafka import AIOKafkaConsumer
        from aiokafka.structs import TopicPartition

        tp = TopicPartition(topic, partition)
        consumer = AIOKafkaConsumer(
            bootstrap_servers=self._bootstrap,
            group_id=group_id,
            enable_auto_commit=False,
            client_id=f"{self._client_id}-reset",
            request_timeout_ms=self._request_timeout_ms,
        )
        await consumer.start()
        try:
            consumer.assign([tp])
            new_offset = await self._resolve_offset(consumer, tp, target)
            consumer.seek(tp, new_offset)
            await consumer.commit({tp: new_offset})
            return int(new_offset)
        finally:
            await consumer.stop()

    @staticmethod
    async def _resolve_offset(consumer: Any, tp: Any, target: str) -> int:
        """Map a human target to an absolute offset."""
        if target == "earliest":
            offsets = await consumer.beginning_offsets([tp])
            return int(offsets[tp])
        if target == "latest":
            offsets = await consumer.end_offsets([tp])
            return int(offsets[tp])
        # integer offset
        try:
            return int(target)
        except ValueError:
            pass
        # iso8601 timestamp
        try:
            dt = datetime.fromisoformat(target.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts_ms = int(dt.timestamp() * 1000)
        except ValueError as exc:  # pragma: no cover
            raise ValueError(
                "target must be 'earliest' | 'latest' | integer offset | ISO-8601 timestamp"
            ) from exc
        offsets = await consumer.offsets_for_times({tp: ts_ms})
        meta = offsets.get(tp)
        if meta is None:
            # No message at or after this timestamp — snap to end.
            end = await consumer.end_offsets([tp])
            return int(end[tp])
        return int(meta.offset)


__all__ = [
    "ConsumerGroupDetail",
    "ConsumerGroupInfo",
    "KafkaAdminService",
    "MemberInfo",
    "PartitionLag",
    "TopicInfo",
]
