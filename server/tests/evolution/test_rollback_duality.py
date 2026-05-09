"""Property test for P-HotReload-2 — rollback duality.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23.8
(Phase L — Promoter + Rollback).

**Validates: Requirements 3.19**

Property under test
-------------------

For any sequence of ``(promote X, rollback)`` cycles starting from a
baseline active prompt version, after each rollback the registry
returns the version id that was active **right before** the promotion:

    registry.get_active(s).id == previously_active.id

The R-3.19 clock budget (5 seconds) is the :class:`PromptReloader`'s
cross-process convergence SLA in a multi-replica deployment. For
unit-test purposes we drive :meth:`SubAgentPromptRegistry.apply_promotion`
synchronously with the exact Kafka payload the Promoter emits, so
convergence is immediate and no wall-clock timing is required — the
duality either holds on the very next read or it never will.

Implementation choices
----------------------

* **One shared store backs both surfaces.** A tiny mutable dict of
  :class:`PromptVersionRow` objects is read by
  :class:`SubAgentPromptRegistry` (via a protocol-shaped fake repo)
  AND written by :class:`Promoter` (via a narrow SQL fake session).
  That keeps DB state and registry state coherent the same way
  Postgres + live replicas would in production.
* **Kafka is simulated in-process.** The Promoter publishes via a
  recording :class:`_FakeProducer`; the test harvests the last event
  and feeds it back into :meth:`SubAgentPromptRegistry.apply_promotion`.
  This mirrors what :class:`PromptReloader` does after the real
  broker round-trip.
* **Hypothesis ``max_examples=50``**: each example drives up to six
  promote/rollback cycles through the registry machinery, so 50
  examples is plenty to shake out ordering bugs without bloating the
  CI budget.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from src.services.evolution.promoter import Promoter
from src.services.evolution.prompt_registry import (
    PromotionEvent,
    SubAgentPromptRegistry,
)
from src.services.prompt_versions.repository import PromptVersionRow


# ---------------------------------------------------------------------------
# Shared store — single source of truth for both Promoter SQL and repo reads
# ---------------------------------------------------------------------------


@dataclass
class _Store:
    """Mutable store of :class:`PromptVersionRow` keyed by UUID."""

    rows: dict[uuid.UUID, PromptVersionRow] = field(default_factory=dict)

    def put(self, row: PromptVersionRow) -> None:
        self.rows[row.id] = row

    def update(self, row_id: uuid.UUID, **kwargs: Any) -> None:
        current = self.rows[row_id]
        self.rows[row_id] = replace(current, **kwargs)


class _SharedRepo:
    """Implements the registry's repository protocol against a :class:`_Store`.

    Only the methods the registry actually calls are implemented:
    ``list_live`` (during load), ``get_by_id`` (during apply_promotion),
    ``get_previous_active`` (only used on retired/rejected events — the
    rollback payload we test emits an ``active`` event so this is
    exercised purely for correctness of the fake, not the property).
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
        matches.sort(key=lambda r: r.activated_at or datetime.min, reverse=True)
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


# ---------------------------------------------------------------------------
# SQL fake — handles only the statements Promoter.rollback_prompt issues
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.rowcount = len(rows)

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _Row:
    """Attribute-scrape target for :func:`promoter._row_to_dict`."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def _wrap(rows: list[PromptVersionRow]) -> list[_Row]:
    return [
        _Row(
            id=r.id,
            sub_agent_name=r.sub_agent_name,
            system_prompt=r.system_prompt,
            status=r.status,
            activated_at=r.activated_at,
            retired_at=r.retired_at,
        )
        for r in rows
    ]


class _FakeSession:
    """Narrow dispatcher for the SQL Promoter.rollback_prompt issues.

    Three shapes matter:

    * ``SELECT ... FROM sub_agent_prompt_versions WHERE ... status = 'active'``
      → the current-active read.
    * ``SELECT ... WHERE id <> :exclude_id AND activated_at IS NOT NULL``
      → the previous-active read ordered by activated_at DESC.
    * Two UPDATEs guarded by ``status='active'`` (retire) / no guard
      (activate).

    Every other statement returns an empty result so accidental SQL
    additions in Promoter would surface as failing assertions rather
    than silent no-ops.
    """

    def __init__(self, store: _Store) -> None:
        self._store = store

    async def execute(
        self, stmt: Any, params: dict[str, Any] | None = None
    ) -> _Result:
        sql = " ".join(str(stmt).split()).lower()
        params = params or {}

        # -- SELECT reads on sub_agent_prompt_versions --------------------
        if sql.startswith(
            "select id, sub_agent_name, system_prompt, status, activated_at, retired_at "
            "from sub_agent_prompt_versions"
        ):
            name = str(params.get("name", ""))
            if "status = 'active'" in sql and "limit 1" in sql:
                rows = [
                    r
                    for r in self._store.rows.values()
                    if r.sub_agent_name == name and r.status == "active"
                ]
                return _Result(_wrap(rows[:1]))
            if "id <> :exclude_id" in sql and "activated_at is not null" in sql:
                eid = _as_uuid(params.get("exclude_id"))
                matches = [
                    r
                    for r in self._store.rows.values()
                    if r.sub_agent_name == name
                    and r.activated_at is not None
                    and (eid is None or r.id != eid)
                ]
                matches.sort(
                    key=lambda r: r.activated_at or datetime.min, reverse=True
                )
                return _Result(_wrap(matches[:1]))

        # -- UPDATE sub_agent_prompt_versions -----------------------------
        if "update sub_agent_prompt_versions" in sql:
            rid = _as_uuid(params.get("id"))
            if rid is None or rid not in self._store.rows:
                return _Result([])
            row = self._store.rows[rid]
            if "status = 'retired'" in sql and "status = 'active'" in sql:
                # Retire current active — guarded.
                if row.status == "active":
                    self._store.update(
                        rid,
                        status="retired",
                        retired_at=datetime.now(UTC),
                    )
            elif "status = 'active'" in sql:
                # Restore previous active.
                self._store.update(
                    rid,
                    status="active",
                    activated_at=datetime.now(UTC),
                    retired_at=None,
                )
            return _Result([])

        return _Result([])

    async def commit(self) -> None:
        # Single-transaction semantics are asserted separately in
        # tests/evolution/test_rollback.py; here we just no-op so the
        # Promoter's commit call lands without raising.
        pass


def _make_db_factory(store: _Store):
    @asynccontextmanager
    async def _factory():
        yield _FakeSession(store)

    return _factory


# ---------------------------------------------------------------------------
# Kafka producer fake — records every publish
# ---------------------------------------------------------------------------


class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes]] = []

    async def send_and_wait(
        self, topic: str, value: Any, **kwargs: Any
    ) -> None:  # noqa: ANN401
        self.sent.append((topic, value))


def _event_from_payload(value_bytes: bytes) -> PromotionEvent:
    """Translate Promoter's Kafka payload into a :class:`PromotionEvent`.

    Matches the shape :class:`PromptReloader` expects when it consumes
    the promotion topic: ``event_id``, ``new_version_id``,
    ``to_status``, plus the ``sub_agent_name`` / ``target_ref`` hint.
    """
    payload = json.loads(value_bytes.decode("utf-8"))
    assert payload.get("kind") == "prompt_patch", (
        f"non-prompt_patch event slipped through: {payload!r}"
    )
    return PromotionEvent(
        event_id=str(payload["event_id"]),
        new_version_id=str(payload["new_version_id"]),
        to_status=str(payload["to_status"]),  # type: ignore[arg-type]
        sub_agent_name=(
            str(payload.get("sub_agent_name") or payload.get("target_ref") or "")
            or None
        ),
    )


# ---------------------------------------------------------------------------
# Row / cycle helpers
# ---------------------------------------------------------------------------


def _make_row(
    *,
    sub_agent_name: str,
    system_prompt: str,
    status: str,
    activated_at: datetime | None = None,
    created_at: datetime | None = None,
) -> PromptVersionRow:
    return PromptVersionRow(
        id=uuid.uuid4(),
        sub_agent_name=sub_agent_name,
        candidate_id=None,
        system_prompt=system_prompt,
        rationale=None,
        status=status,
        parent_version_id=None,
        manifest_sha256=None,
        activated_at=activated_at,
        retired_at=None,
        created_at=created_at or datetime.now(UTC),
    )


async def _promote(
    store: _Store,
    registry: SubAgentPromptRegistry,
    *,
    sub_agent_name: str,
    candidate: PromptVersionRow,
) -> None:
    """Flip ``candidate`` to active in the store + notify the registry.

    Mirrors what :meth:`Promoter.activate_prompt_patch` does in
    production (retire current active, set candidate active, emit
    event). Here we do it inline so the property test body stays
    readable and the Promoter only drives the rollback half.
    """
    for r in list(store.rows.values()):
        if (
            r.sub_agent_name == sub_agent_name
            and r.status == "active"
            and r.id != candidate.id
        ):
            store.update(r.id, status="retired", retired_at=datetime.now(UTC))

    store.update(
        candidate.id,
        status="active",
        activated_at=datetime.now(UTC),
        retired_at=None,
    )

    await registry.apply_promotion(
        PromotionEvent(
            event_id=f"promote-{candidate.id}",
            new_version_id=str(candidate.id),
            to_status="active",
            sub_agent_name=sub_agent_name,
        )
    )


# ---------------------------------------------------------------------------
# Property test — P-HotReload-2
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    n_cycles=st.integers(min_value=1, max_value=6),
    prompt_seed=st.integers(min_value=0, max_value=10_000),
)
@pytest.mark.property
@pytest.mark.asyncio
async def test_rollback_restores_previously_active(
    n_cycles: int, prompt_seed: int
) -> None:
    """Each ``(promote, rollback)`` cycle restores the pre-promote active id.

    **Validates: Requirements 3.19**

    Hypothesis drives ``n_cycles`` distinct candidate promotions;
    ``prompt_seed`` seeds the prompt-text strings so failing
    counter-examples are self-identifying in the shrinker's output.
    """
    sub_agent_name = "ops"
    store = _Store()

    # Baseline active row — the version ``get_active`` returns before
    # cycle 1 starts.
    baseline = _make_row(
        sub_agent_name=sub_agent_name,
        system_prompt=f"baseline-{prompt_seed}",
        status="active",
        activated_at=datetime.now(UTC) - timedelta(days=1),
        created_at=datetime.now(UTC) - timedelta(days=1),
    )
    store.put(baseline)

    # Pre-stage all candidates as ``proposed`` so the registry's
    # ``list_live`` on load() pulls them into its by_id map. This is
    # what lets the promote-event's ``get_by_id`` lookup succeed
    # later — the registry only ever holds rows it has seen.
    candidates: list[PromptVersionRow] = []
    now_floor = datetime.now(UTC)
    for i in range(n_cycles):
        cand = _make_row(
            sub_agent_name=sub_agent_name,
            system_prompt=f"candidate-{prompt_seed}-{i}",
            status="proposed",
            created_at=now_floor + timedelta(seconds=i + 1),
        )
        store.put(cand)
        candidates.append(cand)

    # Build the registry and the Promoter against the SAME store.
    repo = _SharedRepo(store)
    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults={sub_agent_name: "DEFAULT"},
    )
    await registry.load()
    assert registry.get_active(sub_agent_name).id == str(baseline.id), (
        "baseline should be active after initial load"
    )

    producer = _FakeProducer()
    promoter = Promoter(
        db_factory=_make_db_factory(store),
        producer=producer,
    )

    # --- Drive cycles -----------------------------------------------------
    for idx, cand in enumerate(candidates):
        prev_active_id = registry.get_active(sub_agent_name).id

        # (1) Promote the candidate.
        await _promote(
            store, registry, sub_agent_name=sub_agent_name, candidate=cand
        )
        assert registry.get_active(sub_agent_name).id == str(cand.id), (
            f"cycle {idx}: promote did not land on candidate {cand.id}"
        )

        # (2) Rollback via the production Promoter surface.
        sent_before = len(producer.sent)
        result = await promoter.rollback_prompt(sub_agent_name)
        assert result.ok, (
            f"cycle {idx}: rollback_prompt returned ok=False "
            f"({result.reason!r})"
        )
        assert str(result.retired_version_id) == str(cand.id), (
            f"cycle {idx}: rollback retired the wrong row"
        )
        assert str(result.restored_version_id) == prev_active_id, (
            f"cycle {idx}: rollback restored {result.restored_version_id!r}, "
            f"expected {prev_active_id!r}"
        )

        # (3) Relay the exact payload Promoter published into the
        #     registry, simulating the in-process PromptReloader.
        assert len(producer.sent) == sent_before + 1, (
            f"cycle {idx}: expected one event, got "
            f"{len(producer.sent) - sent_before}"
        )
        topic, payload_bytes = producer.sent[-1]
        assert topic == promoter.topic, (
            f"cycle {idx}: producer wrote to wrong topic {topic!r}"
        )
        rollback_event = _event_from_payload(payload_bytes)
        applied = await registry.apply_promotion(rollback_event)
        assert applied is True, (
            f"cycle {idx}: registry dropped the rollback event — "
            "duality broken"
        )

        # (4) Terminal assertion — rollback duality holds synchronously.
        observed = registry.get_active(sub_agent_name).id
        assert observed == prev_active_id, (
            f"cycle {idx}: rollback duality violated: "
            f"expected active id={prev_active_id!r}, got {observed!r}"
        )
