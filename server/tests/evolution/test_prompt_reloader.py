"""Unit tests for :class:`PromptReloader`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 20.1 /
R-3.15, R-3.17, R-3.18.

These are pure-python tests with an in-memory fake consumer and a fake
repository; they don't need Kafka or Postgres. They exercise the three
contract points of the reloader:

1. Only ``kind="prompt_patch"`` messages drive ``apply_promotion``;
   ``skill`` / ``tool_config`` are silently skipped.
2. Decode errors and handler exceptions don't kill the loop.
3. The per-instance consumer group id is derived from
   :func:`instance_id`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.core.instance import instance_id, reset_instance_id_for_tests
from src.services.evolution.prompt_registry import (
    PromptVersion,
    SubAgentPromptRegistry,
)
from src.services.evolution.prompt_reloader import (
    PROMOTION_TOPIC,
    PromptReloader,
)
from src.services.prompt_versions.repository import PromptVersionRow


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeMessage:
    """Minimal stand-in for a kafka-python ConsumerRecord."""

    __slots__ = ("value", "offset", "topic")

    def __init__(self, value: bytes | dict | str, *, offset: int = 0) -> None:
        if isinstance(value, dict):
            value = json.dumps(value).encode("utf-8")
        elif isinstance(value, str):
            value = value.encode("utf-8")
        self.value = value
        self.offset = offset
        self.topic = PROMOTION_TOPIC


class _FakeConsumer:
    """Async iterator that yields a fixed list of messages, then sleeps."""

    def __init__(self, messages: list[_FakeMessage]) -> None:
        self._messages = list(messages)
        self._stop = asyncio.Event()
        self.started = False
        self.stopped = False
        self.group_id: str | None = None

    def __aiter__(self) -> "_FakeConsumer":
        return self

    async def __anext__(self) -> _FakeMessage:
        if self._messages:
            return self._messages.pop(0)
        # Block until stopped so the reloader's main loop stays alive.
        await self._stop.wait()
        raise StopAsyncIteration

    async def stop(self) -> None:
        self.stopped = True
        self._stop.set()


class _FakeRepo:
    """Trivial repository that services ``apply_promotion`` re-reads."""

    def __init__(self) -> None:
        self._rows: dict[str, PromptVersionRow] = {}

    def add(self, row: PromptVersionRow) -> None:
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
        matches.sort(key=lambda r: r.activated_at or datetime.min, reverse=True)
        return matches[0] if matches else None

    async def get_by_candidate(self, candidate_id: Any) -> list[PromptVersionRow]:
        return []


def _make_row(
    *,
    sub_agent_name: str = "ops",
    prompt: str = "TEXT",
    status: str = "proposed",
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_instance_id() -> None:
    """Reset the process-wide instance id before each test for isolation."""
    reset_instance_id_for_tests()
    yield
    reset_instance_id_for_tests()


async def _make_registry(repo: _FakeRepo, defaults: dict[str, str] | None = None) -> SubAgentPromptRegistry:
    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults=defaults or {"ops": "FALLBACK"},
    )
    await registry.load()
    return registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_id_includes_instance_id() -> None:
    """R-3.17: every reloader has its own ``prompt-reloader-{uuid}`` group."""
    repo = _FakeRepo()
    registry = await _make_registry(repo)
    reloader = PromptReloader(registry, consumer_factory=_never_called_factory)
    assert reloader.group_id == f"prompt-reloader-{instance_id()}"


@pytest.mark.asyncio
async def test_only_prompt_patch_events_are_applied() -> None:
    """Non-prompt_patch kinds must be ignored without touching the registry."""
    repo = _FakeRepo()
    baseline = _make_row(status="active", activated=datetime.now(UTC))
    repo.add(baseline)
    registry = await _make_registry(repo)

    # A skill-kind event targeting a real row; reloader must skip it.
    skill_evt = {
        "kind": "skill",
        "target_ref": "ops",
        "new_version_id": str(baseline.id),
        "to_status": "active",
        "event_id": "skill-1",
    }
    messages = [_FakeMessage(skill_evt)]
    fake_consumer = _FakeConsumer(messages)

    reloader = PromptReloader(
        registry,
        consumer_factory=_fixed_consumer_factory(fake_consumer),
    )
    await reloader.start()
    await _yield_for_consumer(fake_consumer)
    # No change because no apply_promotion ran — active id unchanged.
    assert registry.get_active("ops").id == str(baseline.id)
    await reloader.stop()


@pytest.mark.asyncio
async def test_prompt_patch_event_promotes_active() -> None:
    """Happy path: a valid prompt_patch flips the active lane."""
    repo = _FakeRepo()
    baseline = _make_row(
        prompt="baseline", status="active", activated=datetime.now(UTC)
    )
    candidate = _make_row(prompt="candidate", status="proposed")
    repo.add(baseline)
    repo.add(candidate)
    registry = await _make_registry(repo)

    # Simulate the DB transition that Promoter.promote_to_active would do
    # BEFORE emitting to Kafka: the candidate row is now ``active``.
    repo._rows[str(candidate.id)] = replace(
        repo._rows[str(candidate.id)],
        status="active",
        activated_at=datetime.now(UTC),
    )
    repo._rows[str(baseline.id)] = replace(
        repo._rows[str(baseline.id)], status="retired"
    )

    event = {
        "kind": "prompt_patch",
        "target_ref": "ops",
        "new_version_id": str(candidate.id),
        "to_status": "active",
        "event_id": "promote-1",
    }
    fake_consumer = _FakeConsumer([_FakeMessage(event)])

    reloader = PromptReloader(
        registry,
        consumer_factory=_fixed_consumer_factory(fake_consumer),
    )
    await reloader.start()
    await _yield_for_consumer(fake_consumer)

    active = registry.get_active("ops")
    assert active.id == str(candidate.id)
    assert active.system_prompt == "candidate"
    await reloader.stop()


@pytest.mark.asyncio
async def test_replayed_event_is_idempotent() -> None:
    """R-3.18: re-delivering the same event leaves the final state unchanged."""
    repo = _FakeRepo()
    baseline = _make_row(prompt="B", status="active", activated=datetime.now(UTC))
    candidate = _make_row(prompt="C", status="proposed")
    repo.add(baseline)
    repo.add(candidate)
    registry = await _make_registry(repo)

    repo._rows[str(candidate.id)] = replace(
        repo._rows[str(candidate.id)],
        status="active",
        activated_at=datetime.now(UTC),
    )
    repo._rows[str(baseline.id)] = replace(
        repo._rows[str(baseline.id)], status="retired"
    )

    payload = {
        "kind": "prompt_patch",
        "target_ref": "ops",
        "new_version_id": str(candidate.id),
        "to_status": "active",
        "event_id": "dedupe-me",
    }
    # Same event three times in a row.
    fake_consumer = _FakeConsumer(
        [_FakeMessage(payload), _FakeMessage(payload), _FakeMessage(payload)]
    )

    reloader = PromptReloader(
        registry,
        consumer_factory=_fixed_consumer_factory(fake_consumer),
    )
    await reloader.start()
    await _yield_for_consumer(fake_consumer)

    active = registry.get_active("ops")
    assert active.id == str(candidate.id)
    assert active.system_prompt == "C"
    await reloader.stop()


@pytest.mark.asyncio
async def test_malformed_payloads_do_not_kill_loop() -> None:
    """Bad JSON, missing fields, and unknown kinds all survive the next event."""
    repo = _FakeRepo()
    baseline = _make_row(status="active", activated=datetime.now(UTC))
    candidate = _make_row(prompt="GOOD", status="proposed")
    repo.add(baseline)
    repo.add(candidate)
    registry = await _make_registry(repo)

    # Poison the candidate row to active so the real event would work.
    repo._rows[str(candidate.id)] = replace(
        repo._rows[str(candidate.id)],
        status="active",
        activated_at=datetime.now(UTC),
    )
    repo._rows[str(baseline.id)] = replace(
        repo._rows[str(baseline.id)], status="retired"
    )

    messages = [
        _FakeMessage(b"\x00\x01not-json"),  # undecodable
        _FakeMessage({"kind": "prompt_patch"}),  # missing required fields
        _FakeMessage({"kind": "tool_config", "new_version_id": "x", "to_status": "active"}),
        _FakeMessage(
            {
                "kind": "prompt_patch",
                "target_ref": "ops",
                "new_version_id": str(candidate.id),
                "to_status": "active",
                "event_id": "good-after-bad",
            }
        ),
    ]
    fake_consumer = _FakeConsumer(messages)

    reloader = PromptReloader(
        registry,
        consumer_factory=_fixed_consumer_factory(fake_consumer),
    )
    await reloader.start()
    await _yield_for_consumer(fake_consumer)

    active = registry.get_active("ops")
    assert active.id == str(candidate.id)
    assert active.system_prompt == "GOOD"
    await reloader.stop()


@pytest.mark.asyncio
async def test_start_stop_are_idempotent() -> None:
    """Calling start twice is a no-op; stop without start is also fine."""
    repo = _FakeRepo()
    registry = await _make_registry(repo)
    fake_consumer = _FakeConsumer([])

    reloader = PromptReloader(
        registry,
        consumer_factory=_fixed_consumer_factory(fake_consumer),
    )
    await reloader.start()
    await reloader.start()  # second call no-op
    await reloader.stop()
    await reloader.stop()  # second stop no-op
    assert fake_consumer.stopped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _yield_for_consumer(consumer: _FakeConsumer) -> None:
    """Give the reloader's task a few event-loop ticks to drain the queue."""
    for _ in range(10):
        if not consumer._messages:  # noqa: SLF001
            break
        await asyncio.sleep(0.01)


async def _never_called_factory(**_kwargs: Any) -> Any:
    raise AssertionError("consumer factory should not be called in this test")


def _fixed_consumer_factory(consumer: _FakeConsumer):
    async def _factory(*, topic: str, bootstrap_servers: str, group_id: str, auto_offset_reset: str):
        consumer.group_id = group_id
        consumer.started = True
        return consumer

    return _factory
