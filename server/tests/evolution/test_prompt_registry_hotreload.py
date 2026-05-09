"""Property-based tests for :class:`SubAgentPromptRegistry`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` —
tasks 18.3 / 18.4; correctness properties P-HotReload-1 (atomic swap)
and P-HotReload-4 (promotion idempotency).

Both properties are exercised against an in-memory fake repository so
the tests don't need Postgres. The registry code under test is
unchanged — the ``SubAgentPromptVersionRepository`` collaborator is
swapped for :class:`_FakeRepo` which implements the exact methods
``SubAgentPromptRegistry`` calls (``list_live``, ``get_by_id``,
``get_previous_active``).

Property statements:

* **P-HotReload-1** — while the registry is applying a sequence of
  promotions, concurrent readers calling :meth:`get_active` must
  always observe a :class:`PromptVersion` whose ``(id, system_prompt)``
  tuple exists somewhere in the history. They may see ``prev`` or
  ``new`` but never a half-built record.

* **P-HotReload-4** — applying the same
  :class:`PromotionEvent` K times (simulating Kafka redelivery) leaves
  the registry in the same terminal state as applying it once. Both
  lane contents and by-id lookup must match.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Iterable

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from src.services.evolution.prompt_registry import (
    PromotionEvent,
    PromptVersion,
    SubAgentPromptRegistry,
)
from src.services.prompt_versions.repository import PromptVersionRow


# ---------------------------------------------------------------------------
# Fake repository
# ---------------------------------------------------------------------------


class _FakeRepo:
    """In-memory substitute for ``SubAgentPromptVersionRepository``.

    Only implements the surface the registry uses. Rows are stored by
    id; status transitions are done by replacing the row with a new
    dataclass because :class:`PromptVersionRow` is frozen.
    """

    def __init__(self) -> None:
        self._rows: dict[str, PromptVersionRow] = {}

    def add(self, row: PromptVersionRow) -> None:
        self._rows[str(row.id)] = row

    def set_status(
        self,
        row_id: str | uuid.UUID,
        status: str,
        *,
        activated: bool = False,
        retired: bool = False,
    ) -> None:
        key = str(row_id)
        cur = self._rows[key]
        now = datetime.now(UTC)
        updates: dict = {"status": status}
        if activated:
            updates["activated_at"] = now
        if retired:
            updates["retired_at"] = now
        self._rows[key] = replace(cur, **updates)

    async def list_live(self) -> list[PromptVersionRow]:
        return [
            r
            for r in self._rows.values()
            if r.status in ("proposed", "shadow", "ab", "active")
        ]

    async def get_by_id(
        self, version_id: uuid.UUID | str
    ) -> PromptVersionRow | None:
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
        before_id: uuid.UUID | str | None,
    ) -> PromptVersionRow | None:
        bid = str(before_id) if before_id is not None else None
        candidates = [
            r
            for r in self._rows.values()
            if r.sub_agent_name == sub_agent_name
            and r.activated_at is not None
            and (bid is None or str(r.id) != bid)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.activated_at or datetime.min, reverse=True)
        return candidates[0]

    async def get_by_candidate(
        self, candidate_id: uuid.UUID | str
    ) -> list[PromptVersionRow]:
        return [r for r in self._rows.values() if r.candidate_id == candidate_id]


def _make_row(
    *,
    sub_agent_name: str,
    system_prompt: str,
    status: str = "proposed",
    activated_at: datetime | None = None,
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
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# P-HotReload-1 : atomic swap
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    n_versions=st.integers(min_value=2, max_value=8),
    n_readers=st.integers(min_value=10, max_value=100),
    reads_per_reader=st.integers(min_value=20, max_value=80),
)
@pytest.mark.asyncio
async def test_get_active_is_atomic_across_promotions(
    n_versions: int, n_readers: int, reads_per_reader: int
) -> None:
    """Readers must never observe a half-applied swap.

    Setup: build N distinct ``active``-bound versions for ``ops``.
    Every version carries a unique prompt text keyed by its id. We
    start N_readers tasks hammering :meth:`get_active` while a separate
    writer task promotes versions v2, v3, …, vN in sequence via
    :meth:`apply_promotion`. Each read's observed ``system_prompt``
    must match the observed ``id`` in the known ``id -> prompt`` map.
    """
    repo = _FakeRepo()
    name = "ops"

    # Build versions v1..vN. They start as ``proposed`` and get
    # promoted to ``active`` one by one in the writer task. Only v1
    # starts as active so load() gives us a deterministic baseline.
    versions: list[PromptVersionRow] = []
    for idx in range(n_versions):
        row = _make_row(
            sub_agent_name=name,
            system_prompt=f"prompt-v{idx}-" + uuid.uuid4().hex[:8],
            status="active" if idx == 0 else "proposed",
            activated_at=datetime.now(UTC) if idx == 0 else None,
        )
        repo.add(row)
        versions.append(row)

    # id -> expected system_prompt (plus the default-version id).
    expected_prompt_by_id: dict[str, str] = {
        str(v.id): v.system_prompt for v in versions
    }
    default_default = "DEFAULT-ops-prompt"
    expected_prompt_by_id[f"default::{name}"] = default_default

    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults={name: default_default},
    )
    await registry.load()

    # Sanity: starting point is v1 (active).
    start = registry.get_active(name)
    assert start.id == str(versions[0].id)

    stop_event = asyncio.Event()

    async def reader() -> list[PromptVersion]:
        observed: list[PromptVersion] = []
        for _ in range(reads_per_reader):
            pv = registry.get_active(name)
            observed.append(pv)
            # Yield to scheduler so writes interleave meaningfully.
            await asyncio.sleep(0)
        return observed

    async def writer() -> None:
        # Promote v2..vN in order. Each promotion flips the row to
        # ``active`` in the fake repo then fires a PromotionEvent.
        for row in versions[1:]:
            repo.set_status(row.id, "active", activated=True)
            # Previous active row must be demoted so the fake repo stays
            # consistent with the partial-unique constraint of the real DB.
            # We don't need that for apply_promotion to work correctly but
            # leaving multiple ``active`` rows would confuse a later
            # refresh() — keep the repo realistic.
            for other in versions:
                if other.id == row.id:
                    continue
                cur = await repo.get_by_id(other.id)
                if cur and cur.status == "active":
                    repo.set_status(other.id, "retired", retired=True)
            event = PromotionEvent(
                event_id=f"evt-{row.id}",
                new_version_id=str(row.id),
                to_status="active",
                sub_agent_name=name,
            )
            await registry.apply_promotion(event)
            # Let readers make progress between promotions.
            await asyncio.sleep(0)
        stop_event.set()

    reader_tasks = [asyncio.create_task(reader()) for _ in range(n_readers)]
    writer_task = asyncio.create_task(writer())

    observed_per_reader = await asyncio.gather(*reader_tasks)
    await writer_task

    # Invariant: every single observation matches a known (id, prompt).
    for obs_list in observed_per_reader:
        assert obs_list, "reader captured zero samples — scheduler starved"
        for pv in obs_list:
            assert pv.sub_agent_name == name
            expected = expected_prompt_by_id.get(pv.id)
            assert expected is not None, f"unknown pv.id: {pv.id}"
            assert pv.system_prompt == expected, (
                f"prompt/id mismatch: id={pv.id} got={pv.system_prompt!r} "
                f"expected={expected!r}"
            )

    # After all writes land the terminal state must be the last version.
    final = registry.get_active(name)
    assert final.id == str(versions[-1].id)
    assert final.system_prompt == versions[-1].system_prompt


# ---------------------------------------------------------------------------
# P-HotReload-4 : idempotency
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    target_status=st.sampled_from(["active", "shadow", "ab"]),
    n_replays=st.integers(min_value=2, max_value=20),
)
@pytest.mark.asyncio
async def test_apply_promotion_is_idempotent_under_replay(
    target_status: str, n_replays: int
) -> None:
    """Re-delivering the same event must be a no-op after the first apply.

    Strategy:

    1. Seed the fake repo with a baseline active ``ops`` version and a
       candidate ``target`` row in ``proposed``.
    2. Transition ``target`` to ``target_status`` in the repo.
    3. Apply the same :class:`PromotionEvent` N_replays times back-to-back.
    4. Compare the registry state to the state after a single apply.

    The registry must:

    * Return ``True`` exactly once (first apply), ``False`` thereafter.
    * Leave the snapshot / shadow / ab lanes identical to the 1-apply
      branch.
    * Leave ``get_by_id`` returning the same object.
    """
    # Both branches start from the same seed rows so their terminal
    # state can be compared by id, not just by shape.
    baseline = _make_row(
        sub_agent_name="ops",
        system_prompt="baseline-prompt",
        status="active",
        activated_at=datetime.now(UTC),
    )
    target = _make_row(
        sub_agent_name="ops",
        system_prompt=f"target-prompt-{uuid.uuid4().hex[:6]}",
        status="proposed",
    )

    # ── branch A: apply once ──────────────────────────────────────────
    once_repo = _FakeRepo()
    once_repo.add(replace(baseline))
    once_repo.add(replace(target))

    once = SubAgentPromptRegistry(
        repo=once_repo,  # type: ignore[arg-type]
        defaults={"ops": "fallback"},
    )
    await once.load()
    once_repo.set_status(
        target.id, target_status, activated=(target_status == "active")
    )
    if target_status == "active":
        # Keep the repo consistent: demote baseline.
        once_repo.set_status(baseline.id, "retired", retired=True)
    evt_a = PromotionEvent(
        event_id="deadbeef",
        new_version_id=str(target.id),
        to_status=target_status,  # type: ignore[arg-type]
        sub_agent_name="ops",
    )
    applied_once = await once.apply_promotion(evt_a)
    assert applied_once is True

    # ── branch B: apply the same event N times ───────────────────────
    many_repo = _FakeRepo()
    many_repo.add(replace(baseline))
    many_repo.add(replace(target))

    many = SubAgentPromptRegistry(
        repo=many_repo,  # type: ignore[arg-type]
        defaults={"ops": "fallback"},
    )
    await many.load()
    many_repo.set_status(
        target.id, target_status, activated=(target_status == "active")
    )
    if target_status == "active":
        many_repo.set_status(baseline.id, "retired", retired=True)

    evt_b = PromotionEvent(
        event_id="deadbeef",  # same id ⇒ same event
        new_version_id=str(target.id),
        to_status=target_status,  # type: ignore[arg-type]
        sub_agent_name="ops",
    )
    results: list[bool] = []
    for _ in range(n_replays):
        results.append(await many.apply_promotion(evt_b))
    # First delivery mutates, the rest are dedup'd no-ops.
    assert results[0] is True
    assert all(r is False for r in results[1:])

    # ── terminal state equivalence ────────────────────────────────────
    _assert_lanes_equivalent(once, many, sub_agent_name="ops")


# ---------------------------------------------------------------------------
# Helper: compare two registries' observable state for one sub-agent
# ---------------------------------------------------------------------------


def _assert_lanes_equivalent(
    a: SubAgentPromptRegistry,
    b: SubAgentPromptRegistry,
    *,
    sub_agent_name: str,
) -> None:
    """Two registries must expose the same observable state.

    Observable = ``get_active`` id + prompt, ``get_shadow`` id,
    ``get_ab`` id, and same-id round-trip through ``get_by_id``.
    ``version_no`` is explicitly excluded from the comparison because
    it's a per-process counter and can legitimately differ when the
    same event path hit one registry once vs another N times.
    """
    a_active = a.get_active(sub_agent_name)
    b_active = b.get_active(sub_agent_name)
    assert a_active.id == b_active.id
    assert a_active.status == b_active.status
    assert a_active.system_prompt == b_active.system_prompt
    assert a_active.source == b_active.source

    assert _id_or_none(a.get_shadow(sub_agent_name)) == _id_or_none(
        b.get_shadow(sub_agent_name)
    )
    assert _id_or_none(a.get_ab(sub_agent_name)) == _id_or_none(
        b.get_ab(sub_agent_name)
    )

    # get_by_id round-trips
    for pv in (a_active,):
        assert a.get_by_id(pv.id) is not None
        b_pv = b.get_by_id(pv.id)
        assert b_pv is not None
        assert b_pv.system_prompt == pv.system_prompt


def _id_or_none(pv: PromptVersion | None) -> str | None:
    return pv.id if pv is not None else None


# ---------------------------------------------------------------------------
# Sanity assertions — not parameterised but cheap and useful
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_falls_back_to_default_when_db_empty() -> None:
    """R-3.20: registry must never raise / return None for known names."""
    repo = _FakeRepo()
    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults={"ops": "FALLBACK"},
    )
    await registry.load()
    pv = registry.get_active("ops")
    assert pv.system_prompt == "FALLBACK"
    assert pv.source == "default"
    assert pv.id == "default::ops"


@pytest.mark.asyncio
async def test_apply_promotion_rejects_when_db_has_drifted() -> None:
    """If the row moved on in DB, the event is ignored (no lane change)."""
    repo = _FakeRepo()
    baseline = _make_row(
        sub_agent_name="ops",
        system_prompt="baseline",
        status="active",
        activated_at=datetime.now(UTC),
    )
    candidate = _make_row(
        sub_agent_name="ops",
        system_prompt="candidate",
        status="proposed",
    )
    repo.add(baseline)
    repo.add(candidate)

    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults={"ops": "fallback"},
    )
    await registry.load()

    # Row is still 'proposed' but the event claims to_status='active'.
    # Registry must ignore.
    evt = PromotionEvent(
        event_id="drift-1",
        new_version_id=str(candidate.id),
        to_status="active",
        sub_agent_name="ops",
    )
    applied = await registry.apply_promotion(evt)
    assert applied is False
    assert registry.get_active("ops").id == str(baseline.id)


@pytest.mark.asyncio
async def test_apply_promotion_unknown_id_is_noop() -> None:
    """Event pointing at a row that doesn't exist is harmless."""
    repo = _FakeRepo()
    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults={"ops": "fallback"},
    )
    await registry.load()
    evt = PromotionEvent(
        event_id="orphan",
        new_version_id=str(uuid.uuid4()),
        to_status="active",
        sub_agent_name="ops",
    )
    assert await registry.apply_promotion(evt) is False
    # Replaying is still a no-op (and now also deduped).
    assert await registry.apply_promotion(evt) is False


@pytest.mark.asyncio
async def test_retire_active_restores_previous() -> None:
    """Retiring the active row falls back to the previous activated version."""
    repo = _FakeRepo()
    old = _make_row(
        sub_agent_name="ops",
        system_prompt="OLD",
        status="retired",
        activated_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    current = _make_row(
        sub_agent_name="ops",
        system_prompt="CURRENT",
        status="active",
        activated_at=datetime(2026, 5, 4, tzinfo=UTC),
    )
    repo.add(old)
    repo.add(current)

    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults={"ops": "FALLBACK"},
    )
    await registry.load()
    assert registry.get_active("ops").id == str(current.id)

    # Retire current in the repo, then fire the event.
    repo.set_status(current.id, "retired", retired=True)
    evt = PromotionEvent(
        event_id="retire-current",
        new_version_id=str(current.id),
        to_status="retired",
        sub_agent_name="ops",
    )
    changed = await registry.apply_promotion(evt)
    assert changed is True
    new_active = registry.get_active("ops")
    assert new_active.id == str(old.id)
    assert new_active.system_prompt == "OLD"


@pytest.mark.asyncio
async def test_retire_without_history_falls_back_to_default() -> None:
    """If no previous active exists, retirement should expose the default."""
    repo = _FakeRepo()
    only = _make_row(
        sub_agent_name="ops",
        system_prompt="ONLY",
        status="active",
        activated_at=datetime.now(UTC),
    )
    repo.add(only)

    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults={"ops": "FALLBACK"},
    )
    await registry.load()
    assert registry.get_active("ops").id == str(only.id)

    repo.set_status(only.id, "retired", retired=True)
    evt = PromotionEvent(
        event_id="retire-only",
        new_version_id=str(only.id),
        to_status="retired",
        sub_agent_name="ops",
    )
    assert await registry.apply_promotion(evt) is True
    pv = registry.get_active("ops")
    assert pv.source == "default"
    assert pv.system_prompt == "FALLBACK"
