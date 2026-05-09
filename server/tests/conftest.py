"""Root test fixtures.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 20.4.

Houses the ``multi_instance_cluster`` fixture used by
:mod:`tests.evolution.test_multi_instance_convergence`. The fixture
gives an integration test a small "cluster" of in-process
PromptReloader instances, each with its own ``instance_id`` and its
own per-instance Kafka consumer group, all subscribed to the same
real ``ops.agent.promotion`` topic.

Why in-process rather than subprocessed uvicorn workers
-------------------------------------------------------

The spec allows a lighter-weight convergence test when subprocess
orchestration is infeasible (see task 20.4 note). In this repository:

* Starting three full uvicorn processes from WSL with a live Postgres +
  Kafka + Redis docker-compose stack adds considerable flakiness to the
  test (port contention, DB schema bootstrap ordering, ``asyncpg``
  pool warm-up time) that isn't actually testing the property of
  interest.
* The property under test, P-HotReload-3, is specifically "every
  FastAPI instance subscribed to ``ops.agent.promotion`` converges on
  the new active prompt version within 5s". The observable that
  matters is ``registry.get_active(...).id``. Running each registry in
  its own Python task with a distinct ``group_id`` exercises the same
  Kafka fan-out semantics as a multi-process deployment.
* Subprocess uvicorn tests remain a valid add-on for teams that
  have the environment for them; this fixture can be swapped for a
  subprocess-based factory without changing the test body.

This fixture is marked ``asyncio`` and requires a reachable Kafka
broker at ``localhost:9094``; callers use ``pytest.mark.skipif`` to
keep CI without a broker green.
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest_asyncio

from src.services.evolution.prompt_registry import (
    PromotionEvent,
    SubAgentPromptRegistry,
)
from src.services.evolution.prompt_reloader import (
    PROMOTION_TOPIC,
    PromptReloader,
)
from src.services.prompt_versions.repository import PromptVersionRow


# ---------------------------------------------------------------------------
# Connectivity helper
# ---------------------------------------------------------------------------


def kafka_available(host: str = "localhost", port: int = 9094) -> bool:
    """Return True iff a TCP connection to ``host:port`` is accepted.

    Integration tests that depend on a real broker guard themselves
    with ``pytest.mark.skipif(not kafka_available(), ...)``.
    """
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# In-memory repository (no Postgres required)
# ---------------------------------------------------------------------------


class _InMemoryPromptRepo:
    """Minimal substitute for ``SubAgentPromptVersionRepository``.

    Shared across the whole cluster so a single "DB" state is visible
    from every PromptReloader — mirrors the real system where all
    FastAPI replicas read the same Postgres. All registry methods used
    by :class:`SubAgentPromptRegistry` are implemented; everything
    else would raise ``AttributeError`` in the registry code we don't
    exercise here.
    """

    def __init__(self) -> None:
        self._rows: dict[str, PromptVersionRow] = {}

    def add(self, row: PromptVersionRow) -> None:
        self._rows[str(row.id)] = row

    def replace(self, row: PromptVersionRow) -> None:
        """Overwrite the row with matching id (used by promotion helper)."""
        self._rows[str(row.id)] = row

    async def list_live(self) -> list[PromptVersionRow]:
        return [
            r
            for r in self._rows.values()
            if r.status in ("proposed", "shadow", "ab", "active")
        ]

    async def get_by_id(self, version_id: Any) -> PromptVersionRow | None:
        return self._rows.get(str(version_id))

    async def get_active(self, sub_agent_name: str) -> PromptVersionRow | None:
        for r in self._rows.values():
            if r.sub_agent_name == sub_agent_name and r.status == "active":
                return r
        return None

    async def get_previous_active(
        self,
        sub_agent_name: str,
        *,
        before_id: Any,
    ) -> PromptVersionRow | None:
        bid = str(before_id) if before_id is not None else None
        matches = [
            r
            for r in self._rows.values()
            if r.sub_agent_name == sub_agent_name
            and r.activated_at is not None
            and (bid is None or str(r.id) != bid)
        ]
        matches.sort(
            key=lambda r: r.activated_at or datetime.min, reverse=True
        )
        return matches[0] if matches else None

    async def get_by_candidate(self, candidate_id: Any) -> list[PromptVersionRow]:
        return []


def _make_row(
    *,
    sub_agent_name: str,
    prompt: str,
    status: str,
    activated: datetime | None = None,
) -> PromptVersionRow:
    return PromptVersionRow(
        id=uuid.uuid4(),
        sub_agent_name=sub_agent_name,
        candidate_id=None,
        system_prompt=prompt,
        rationale=None,
        status=status,
        parent_version_id=None,
        manifest_sha256=None,
        activated_at=activated,
        retired_at=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Cluster description
# ---------------------------------------------------------------------------


@dataclass
class _Instance:
    """One simulated FastAPI replica: unique id + registry + reloader."""

    instance_id: str
    registry: SubAgentPromptRegistry
    reloader: PromptReloader


@dataclass
class MultiInstanceCluster:
    """Handle returned by the ``multi_instance_cluster`` fixture.

    Tests interact via three surfaces:

    * :attr:`instances` — list of :class:`_Instance` objects. Each one
      exposes a ``registry`` the test can query with ``get_active``.
    * :meth:`promote_to_active` — atomically flip a row to ``active``
      in the shared repo and publish a Kafka promotion event. Mirrors
      what :class:`Promoter.promote_to_active` does in production.
    * :meth:`wait_for_convergence` — poll all instance registries until
      they all return the same ``active`` version id, or timeout.

    The fixture itself builds the cluster and tears it down; the test
    body only calls the helpers above.
    """

    repo: _InMemoryPromptRepo
    instances: list[_Instance]
    topic: str
    bootstrap_servers: str
    producer: Any | None = None
    _rows_by_name: dict[str, list[PromptVersionRow]] = field(default_factory=dict)

    # -- registration ---------------------------------------------------

    def seed_initial_active(
        self, sub_agent_name: str, prompt_text: str
    ) -> PromptVersionRow:
        """Create and persist a baseline active row; return it.

        This is done BEFORE the reloaders are started so every
        registry's initial ``load()`` sees it.
        """
        row = _make_row(
            sub_agent_name=sub_agent_name,
            prompt=prompt_text,
            status="active",
            activated=datetime.now(UTC),
        )
        self.repo.add(row)
        self._rows_by_name.setdefault(sub_agent_name, []).append(row)
        return row

    def stage_candidate(
        self, sub_agent_name: str, prompt_text: str
    ) -> PromptVersionRow:
        """Add a candidate (``proposed``) row. Not yet active."""
        row = _make_row(
            sub_agent_name=sub_agent_name,
            prompt=prompt_text,
            status="proposed",
        )
        self.repo.add(row)
        self._rows_by_name.setdefault(sub_agent_name, []).append(row)
        return row

    # -- promotion ------------------------------------------------------

    async def promote_to_active(
        self, candidate: PromptVersionRow
    ) -> None:
        """Flip ``candidate`` to active in the repo and emit the Kafka event.

        Mimics :class:`Promoter.promote_to_active`: DB state is updated
        first, then the event is published. Per-instance reloaders pick
        up the event from Kafka and re-read the DB via
        ``repo.get_by_id``.
        """
        # Retire any existing active row for this sub-agent so the repo
        # stays consistent with the partial-unique index in prod.
        from dataclasses import replace as _replace

        for row in list(self.repo._rows.values()):  # noqa: SLF001
            if (
                row.sub_agent_name == candidate.sub_agent_name
                and row.status == "active"
                and row.id != candidate.id
            ):
                self.repo.replace(_replace(row, status="retired"))

        promoted = _replace(
            candidate, status="active", activated_at=datetime.now(UTC)
        )
        self.repo.replace(promoted)

        # Publish the promotion event over the shared Kafka topic.
        assert self.producer is not None, "producer not started"
        import json as _json

        payload = {
            "kind": "prompt_patch",
            "target_ref": candidate.sub_agent_name,
            "new_version_id": str(candidate.id),
            "to_status": "active",
            "event_id": f"promote-{candidate.id}",
        }
        await self.producer.send_and_wait(
            self.topic, _json.dumps(payload).encode("utf-8")
        )

    # -- verification ---------------------------------------------------

    async def wait_for_convergence(
        self,
        sub_agent_name: str,
        expected_version_id: str,
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
    ) -> bool:
        """Return True iff every instance reports ``expected_version_id``.

        Polls up to ``timeout_s``. ``False`` means at least one
        instance never caught up — test assertions should then surface
        which instances diverged for the operator.
        """
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if all(
                inst.registry.get_active(sub_agent_name).id
                == expected_version_id
                for inst in self.instances
            ):
                return True
            await asyncio.sleep(poll_interval_s)
        return False

    def divergence_snapshot(self, sub_agent_name: str) -> dict[str, str]:
        """Return {instance_id -> active version id} for debugging."""
        return {
            inst.instance_id: inst.registry.get_active(sub_agent_name).id
            for inst in self.instances
        }


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def multi_instance_cluster():
    """Spin up an in-process 3-instance cluster + a Kafka producer.

    Caller must pre-check broker availability with ``kafka_available()``
    (the test decorates itself with ``skipif``). The fixture creates
    three :class:`SubAgentPromptRegistry` + :class:`PromptReloader`
    pairs, each with a distinct ``group_id`` derived from a synthetic
    ``instance_id``. They all share a single in-memory repo so a DB
    lookup from any reloader sees the same state.

    A unique topic name is used per test run (``ops.agent.promotion.<uuid>``)
    so concurrent test runs and previous-run residue can't cross-talk.
    """
    bootstrap = "localhost:9094"
    # Use a fresh topic per fixture invocation — each test promotion is
    # seen only by this cluster's consumers.
    topic = f"ops.agent.promotion.test-{uuid.uuid4().hex[:8]}"

    # Create the topic explicitly so AUTO_CREATE being off on the
    # broker doesn't surprise us. Best-effort cleanup in teardown.
    from src.services.kafka.admin import KafkaAdminService

    admin = KafkaAdminService(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        await admin.create_topic(
            topic, partitions=3, replication_factor=1
        )
    finally:
        await admin.close()

    # Seed the cluster with a shared repo + a pre-existing active row
    # for the sub-agent under test. The test itself stages a candidate
    # and promotes it; the convergence assertion watches the reloaders
    # swap over.
    repo = _InMemoryPromptRepo()
    cluster = MultiInstanceCluster(
        repo=repo,
        instances=[],
        topic=topic,
        bootstrap_servers=bootstrap,
    )

    # Build 3 instance objects BEFORE seeding so tests can pre-seed via
    # ``cluster.seed_initial_active`` and then start the reloaders.
    instance_ids = [f"test-inst-{i}-{uuid.uuid4().hex[:6]}" for i in range(3)]
    instances: list[_Instance] = []
    for iid in instance_ids:
        registry = SubAgentPromptRegistry(
            repo=repo,  # type: ignore[arg-type]
            defaults={"ops": "FALLBACK-ops", "monitor": "FALLBACK-monitor"},
        )
        reloader = PromptReloader(
            registry,
            bootstrap_servers=bootstrap,
            topic=topic,
            group_id=f"prompt-reloader-{iid}",
        )
        instances.append(
            _Instance(instance_id=iid, registry=registry, reloader=reloader)
        )
    cluster.instances = instances

    # Shared producer that the test uses to publish promotion events.
    from aiokafka import AIOKafkaProducer

    producer = AIOKafkaProducer(
        bootstrap_servers=bootstrap, client_id="multi-cluster-test-producer"
    )
    await producer.start()
    cluster.producer = producer

    try:
        yield cluster
    finally:
        # Stop reloaders BEFORE closing the producer; loop tasks may
        # still log on shutdown but won't see connection errors.
        for inst in cluster.instances:
            try:
                await inst.reloader.stop()
            except Exception:
                pass
        try:
            await producer.stop()
        except Exception:
            pass

        # Best-effort topic cleanup so repeat local runs stay clean.
        admin2 = KafkaAdminService(bootstrap_servers=bootstrap)
        try:
            await admin2.start()
            try:
                await admin2.delete_topic(topic, confirm=True)
            except Exception:
                pass
        finally:
            try:
                await admin2.close()
            except Exception:
                pass


__all__ = [
    "MultiInstanceCluster",
    "kafka_available",
    "multi_instance_cluster",
]
