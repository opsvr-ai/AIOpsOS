"""Shared fixtures for the Property-Based Test (PBT) registry.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 28.1
(Phase N cross-cutting / Correctness Property registry).

**Validates: design.md § Correctness Properties** (all 19 properties
that the requirements document cross-references in
``requirements.md § "Correctness Properties 与 Requirements 映射"``).

Purpose
-------

The PBT suites under this package each pin a single named property
(``P-Router-1``, ``P-HotReload-6``, …). Rather than each test growing
its own in-module copies of the same fakes, this conftest centralises
the building blocks every PBT needs:

* A **scripted fake LLM** (``fake_llm``) that plays a canned list of
  bodies back in order — mirrors the private ``_ScriptedLLM`` helper
  in :mod:`tests.workers.test_reflection_candidate_generation` and
  :mod:`tests.workers.test_evaluator` so the shared style stays
  consistent.
* A **fake Kafka producer** (``fake_kafka_producer``) that records
  every ``send_and_wait`` call. Matches the ``_FakeProducer`` shape
  used in :mod:`tests.evolution.test_rollback` /
  :mod:`tests.evolution.test_rollback_duality` and the ``AsyncMock``
  pattern used in :mod:`tests.agent_runtime.test_trajectory_zero_loss`
  so that existing PBT suites can be refactored onto this fixture
  without behavioural drift.
* An **in-memory prompt registry** (``in_memory_prompt_registry``)
  pre-wired with a ``_SharedRepo`` fake that implements the registry's
  repository protocol entirely in Python dicts — the same pattern
  :mod:`tests.evolution.test_rollback_duality` uses.
* A **shared Hypothesis settings profile** (``hypothesis_default_settings``
  and the module-level ``DEFAULT_PBT_SETTINGS``) so every suite picks
  up ``max_examples=100`` / ``deadline=None`` by default. Individual
  tests that need more or fewer examples (see ``test_hotreload_6`` at
  200, ``test_trajectory_zero_loss`` at 10) remain free to override.

The fixtures intentionally touch **no external services** — no DB, no
Redis, no Kafka broker. PBT suites that need a live broker continue
to use the ``multi_instance_cluster`` fixture from the root
``tests/conftest.py`` with the ``@pytest.mark.kafka`` skip guard.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from hypothesis import HealthCheck
from hypothesis import settings as hsettings

from src.services.evolution.prompt_registry import SubAgentPromptRegistry
from src.services.prompt_versions.repository import PromptVersionRow


# ---------------------------------------------------------------------------
# Hypothesis defaults
# ---------------------------------------------------------------------------


#: Shared Hypothesis configuration used by PBT suites under this
#: package. Individual tests may compose additional settings on top —
#: e.g. bumping ``max_examples`` for slow but important properties or
#: dropping it for burst / overflow exercises.
DEFAULT_PBT_SETTINGS = hsettings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


@pytest.fixture(scope="session")
def hypothesis_default_settings() -> hsettings:
    """Session-scoped Hypothesis profile.

    Use::

        @hypothesis_default_settings
        @given(...)
        def test_property(...):
            ...

    or read ``settings`` directly off the fixture and compose with
    ``settings(parent=settings, max_examples=...)`` for a per-test
    override.
    """

    return DEFAULT_PBT_SETTINGS


# ---------------------------------------------------------------------------
# Scripted LLM double — deterministic, no network
# ---------------------------------------------------------------------------


@dataclass
class _LLMResponse:
    """Shape-compatible with ``langchain_core.messages.BaseMessage``.

    The only field PBT suites inspect is ``content``. Keeping the
    surface tiny means the fake doesn't drift when LangChain changes
    non-content attributes.
    """

    content: str


class _ScriptedLLM:
    """Returns a queue of canned responses in order.

    Mirrors the private ``_ScriptedLLM`` helpers used by the worker /
    evaluator unit-test modules. Exposes ``calls`` so tests can assert
    both *what* the LLM was asked and *how many times* it was asked.
    """

    def __init__(self, bodies: list[str] | None = None) -> None:
        self._bodies: list[str] = list(bodies or [])
        self.calls: list[list[Any]] = []
        self.ainvoke_calls: int = 0

    def push(self, body: str) -> None:
        """Append another canned body — handy for bodies generated
        inside a Hypothesis strategy example.
        """

        self._bodies.append(body)

    def extend(self, bodies: list[str]) -> None:
        """Bulk version of :meth:`push`."""

        self._bodies.extend(bodies)

    async def ainvoke(self, messages: Any) -> _LLMResponse:
        self.calls.append(messages)
        self.ainvoke_calls += 1
        if not self._bodies:
            raise AssertionError(
                "_ScriptedLLM exhausted — push more bodies or expected fewer calls"
            )
        return _LLMResponse(content=self._bodies.pop(0))

    # Synchronous variant for the rare call-site that bypasses
    # ``ainvoke`` — kept minimal to avoid masking async misuse.
    def invoke(self, messages: Any) -> _LLMResponse:  # pragma: no cover - parity only
        return asyncio.get_event_loop().run_until_complete(self.ainvoke(messages))


@pytest.fixture
def fake_llm() -> _ScriptedLLM:
    """Brand-new ``_ScriptedLLM`` for each test.

    Seed it with canned bodies via ``fake_llm.push("...")`` or construct
    a specialised one with ``_ScriptedLLM(bodies=[...])`` directly.
    """

    return _ScriptedLLM()


# Expose the class for tests that need to build their own pre-seeded
# instance without going through the fixture.
ScriptedLLM = _ScriptedLLM


# ---------------------------------------------------------------------------
# Fake Kafka producer — records every send_and_wait
# ---------------------------------------------------------------------------


class _FakeKafkaProducer:
    """Records every ``send_and_wait`` invocation.

    Aligned with the ``_FakeProducer`` classes in
    :mod:`tests.evolution.test_rollback` and
    :mod:`tests.evolution.test_rollback_duality`. The ``sent`` list
    preserves insertion order so tests can assert both topic and body
    without worrying about async scheduling jitter.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, Any, dict[str, Any]]] = []
        self.started: bool = False
        self.stopped: bool = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_and_wait(
        self, topic: str, value: Any, **kwargs: Any
    ) -> None:
        # ``kwargs`` absorbs ``key=`` / ``headers=`` variants so we stay
        # compatible with any future send-site tweaks.
        self.sent.append((topic, value, dict(kwargs)))

    # Convenience accessors ------------------------------------------------

    @property
    def topics(self) -> list[str]:
        return [t for t, _, _ in self.sent]

    @property
    def bodies(self) -> list[Any]:
        return [b for _, b, _ in self.sent]

    def reset(self) -> None:
        self.sent.clear()


@pytest.fixture
def fake_kafka_producer() -> _FakeKafkaProducer:
    """Recording producer fixture — one fresh instance per test."""

    return _FakeKafkaProducer()


@pytest.fixture
def asyncmock_kafka_producer() -> AsyncMock:
    """``AsyncMock`` variant mirroring the pattern used by
    :mod:`tests.agent_runtime.test_trajectory_zero_loss` and
    :mod:`tests.kafka.test_dlq_idempotency`.

    Prefer :func:`fake_kafka_producer` when the test inspects payloads
    by equality; use this one when the test wants
    ``producer.send_and_wait.assert_called_with(...)``.
    """

    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()
    producer.start = AsyncMock()
    producer.stop = AsyncMock()
    return producer


# Class is exported for direct use without the fixture.
FakeKafkaProducer = _FakeKafkaProducer


# ---------------------------------------------------------------------------
# Fake Kafka consumer — iterable queue of payloads
# ---------------------------------------------------------------------------


@dataclass
class _FakeRecord:
    """Minimal stand-in for ``aiokafka.ConsumerRecord``."""

    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes
    timestamp: int = 0
    headers: tuple = ()


class _FakeKafkaConsumer:
    """In-memory queue-backed consumer.

    PBT suites that need to drive :class:`PromptReloader`-shaped code
    through a sequence of events push :class:`_FakeRecord` entries
    onto the consumer and then drain it by awaiting ``__anext__``.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[_FakeRecord] = asyncio.Queue()
        self.group_id: str | None = None
        self.topic: str | None = None
        self.started: bool = False
        self.stopped: bool = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def push(self, record: _FakeRecord) -> None:
        await self._queue.put(record)

    def __aiter__(self) -> "_FakeKafkaConsumer":
        return self

    async def __anext__(self) -> _FakeRecord:
        return await self._queue.get()


@pytest.fixture
def fake_kafka_consumer() -> _FakeKafkaConsumer:
    return _FakeKafkaConsumer()


FakeKafkaConsumer = _FakeKafkaConsumer
FakeRecord = _FakeRecord


# ---------------------------------------------------------------------------
# In-memory prompt registry (no DB, no Kafka)
# ---------------------------------------------------------------------------


@dataclass
class _Store:
    """Mutable store of :class:`PromptVersionRow` keyed by UUID.

    Shared between the registry's fake repo and any helper that wants
    to flip statuses — lets a PBT suite assert that ``get_active``
    post-promotion reflects a transactional DB write the test itself
    staged.
    """

    rows: dict[uuid.UUID, PromptVersionRow] = field(default_factory=dict)

    def put(self, row: PromptVersionRow) -> None:
        self.rows[row.id] = row

    def update(self, row_id: uuid.UUID, **kwargs: Any) -> None:
        self.rows[row_id] = replace(self.rows[row_id], **kwargs)


class _SharedRepo:
    """Registry-protocol compatible fake backed by :class:`_Store`.

    Only implements the methods
    :class:`~src.services.evolution.prompt_registry.SubAgentPromptRegistry`
    actually calls (``list_live``, ``get_by_id``, ``get_previous_active``,
    ``get_by_candidate``). Anything else raises ``AttributeError``
    loudly — that way an accidental dependency on an unimplemented
    method surfaces in the test run rather than silently returning
    ``None``.
    """

    def __init__(self, store: _Store) -> None:
        self._store = store

    async def list_live(self) -> list[PromptVersionRow]:
        return [
            r
            for r in self._store.rows.values()
            if r.status in ("proposed", "shadow", "ab", "active")
        ]

    async def get_by_id(self, version_id: Any) -> PromptVersionRow | None:
        key = _as_uuid(version_id)
        if key is None:
            return None
        return self._store.rows.get(key)

    async def get_active(self, sub_agent_name: str) -> PromptVersionRow | None:
        for r in self._store.rows.values():
            if r.sub_agent_name == sub_agent_name and r.status == "active":
                return r
        return None

    async def get_previous_active(
        self,
        sub_agent_name: str,
        *,
        before_id: Any,
    ) -> PromptVersionRow | None:
        bid = _as_uuid(before_id) if before_id is not None else None
        matches = [
            r
            for r in self._store.rows.values()
            if r.sub_agent_name == sub_agent_name
            and r.activated_at is not None
            and (bid is None or r.id != bid)
        ]
        matches.sort(
            key=lambda r: r.activated_at or datetime.min, reverse=True
        )
        return matches[0] if matches else None

    async def get_by_candidate(self, candidate_id: Any) -> list[PromptVersionRow]:
        cid = _as_uuid(candidate_id)
        if cid is None:
            return []
        return [r for r in self._store.rows.values() if r.candidate_id == cid]


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def make_prompt_version_row(
    *,
    sub_agent_name: str,
    system_prompt: str,
    status: str = "active",
    activated: datetime | None = None,
    candidate_id: uuid.UUID | None = None,
    parent_version_id: uuid.UUID | None = None,
) -> PromptVersionRow:
    """Factory for a fully-populated :class:`PromptVersionRow`.

    Defaults to ``status='active'`` with ``activated_at`` set to now —
    handy for staging a baseline row before a PBT starts driving
    promotions.
    """

    now = datetime.now(UTC)
    return PromptVersionRow(
        id=uuid.uuid4(),
        sub_agent_name=sub_agent_name,
        candidate_id=candidate_id,
        system_prompt=system_prompt,
        rationale=None,
        status=status,
        parent_version_id=parent_version_id,
        manifest_sha256=None,
        activated_at=(
            activated if activated is not None else (now if status == "active" else None)
        ),
        retired_at=None,
        created_at=now,
    )


@dataclass
class InMemoryPromptRegistryHarness:
    """Bundle the three objects a PBT typically juggles together."""

    store: _Store
    repo: _SharedRepo
    registry: SubAgentPromptRegistry


@pytest_asyncio.fixture
async def in_memory_prompt_registry() -> InMemoryPromptRegistryHarness:
    """A loaded :class:`SubAgentPromptRegistry` on a shared in-memory repo.

    Seeds two common sub-agents (``ops`` / ``monitor``) with a baseline
    ``active`` row so tests can immediately call
    ``registry.get_active("ops")`` without bootstrapping. Tests needing
    a different topology should construct their own harness via the
    exported :class:`_Store` / :class:`_SharedRepo` / factory helpers.
    """

    store = _Store()
    # Baseline rows so the registry has something to return pre-load.
    store.put(
        make_prompt_version_row(
            sub_agent_name="ops",
            system_prompt="baseline-ops",
        )
    )
    store.put(
        make_prompt_version_row(
            sub_agent_name="monitor",
            system_prompt="baseline-monitor",
        )
    )

    repo = _SharedRepo(store)
    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults={"ops": "FALLBACK-ops", "monitor": "FALLBACK-monitor"},
    )
    await registry.load()
    return InMemoryPromptRegistryHarness(store=store, repo=repo, registry=registry)


# Exports so PBT modules can build customised harnesses directly.
SharedRepo = _SharedRepo
Store = _Store


__all__ = [
    "DEFAULT_PBT_SETTINGS",
    "FakeKafkaConsumer",
    "FakeKafkaProducer",
    "FakeRecord",
    "InMemoryPromptRegistryHarness",
    "ScriptedLLM",
    "SharedRepo",
    "Store",
    "asyncmock_kafka_producer",
    "fake_kafka_consumer",
    "fake_kafka_producer",
    "fake_llm",
    "hypothesis_default_settings",
    "in_memory_prompt_registry",
    "make_prompt_version_row",
]
