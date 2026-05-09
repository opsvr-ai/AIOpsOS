"""Property-based tests for the candidate state-machine (task 22.4).

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 22.4
(Phase K — Evaluator). Correctness property **P-Evolve-1**: the
state-machine on :class:`~src.services.evolution.candidate_store.SkillCandidateStore`
is monotone — every edge listed in :data:`STATE_TRANSITIONS` succeeds,
every other edge is rejected with :class:`InvalidStateTransition`,
terminal states stay terminal, and any sequence of random moves
preserves row integrity.

**Validates: Requirements 3.4**

R-3.4 in concrete terms::

    proposed → shadow | rejected
    shadow   → ab | rejected | retired
    ab       → active | rejected | retired
    active   → retired
    retired  → <terminal>
    rejected → <terminal>

Anything else (reverse moves, cross-branch moves, out-of-band leaps
like ``proposed → active``) must raise
:class:`InvalidStateTransition` *without* mutating the row. Writing
the same status back is the one exception — that's an explicit
idempotent no-op so replayed promotion events don't bounce through
the machine twice.

Test surface
------------

We exercise :meth:`SkillCandidateStore.update_status` against an
in-memory fake DB (same pattern as
:mod:`tests.evolution.test_candidate_store` — duplicated here to
keep the sibling test's fake private; the surface we need is
narrower than what ``test_candidate_store`` models).

Four hypothesis properties:

* ``test_all_allowed_edges_succeed`` — enumerates every ``(src, dst)``
  in :data:`STATE_TRANSITIONS`; each move leaves the row at ``dst``.
* ``test_all_disallowed_edges_raise`` — enumerates every pair that
  is neither allowed nor same-status; each move raises
  :class:`InvalidStateTransition` and leaves the row at ``src``.
* ``test_terminal_states_stay_terminal`` — from ``retired`` or
  ``rejected``, every non-idempotent move raises; same-status is a
  no-op.
* ``test_random_walks_preserve_validity`` — walks up to 20 moves
  sampled from :data:`ALL_STATUSES`; at every step the observed row
  status equals the expected transition-model status.

Hypothesis profile: ``max_examples=100``, ``deadline=None`` so the
asyncio round-trips through the fake DB aren't time-policed.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from src.services.evolution.candidate_store import (
    ALL_STATUSES,
    InvalidStateTransition,
    SkillCandidateStore,
    STATE_TRANSITIONS,
)


pytestmark = [pytest.mark.property]


# ---------------------------------------------------------------------------
# Minimal fake DB — models only the SQL ``update_status`` emits
# ---------------------------------------------------------------------------
#
# Kept deliberately narrower than the fake in
# ``test_candidate_store.py``: we don't need INSERT routing (tests
# seed rows directly), tool_config snapshots, or prompt-version
# tables. The state-machine lives entirely on ``skill_candidates``
# UPDATE + SELECT-by-id, so that's all the fake speaks.


@dataclass
class _CandidateRow:
    id: uuid.UUID
    name: str
    status: str
    kind: str = "skill"
    target_ref: str | None = None
    tags: list[Any] = field(default_factory=list)


class _Row:
    """Attribute-style row shim used by ``SELECT`` results."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Result:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows
        self.rowcount = len(rows)

    def first(self) -> _Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[_Row]:
        return list(self._rows)


@dataclass
class _FakeDB:
    """In-memory store dispatching the narrow SQL surface under test."""

    skill_candidates: dict[uuid.UUID, _CandidateRow] = field(default_factory=dict)

    def factory(self):
        db = self

        @asynccontextmanager
        async def _factory():
            yield _FakeSession(db)

        return _factory


class _FakeSession:
    """Dispatches raw SQL by leading-keyword match.

    The store only issues four kinds of statements here:

    * ``SELECT ... FROM skill_candidates WHERE id = :id`` — ``get``
    * ``SELECT ... FROM sub_agent_prompt_versions WHERE id = :id`` —
      ``get`` fallback; always empty in these tests because every
      seeded candidate is a skill row.
    * ``UPDATE skill_candidates SET status ...`` — ``update_status``
    * ``commit()`` / ``rollback()`` — no-ops on the fake.
    """

    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    async def execute(
        self, stmt: Any, params: dict[str, Any] | None = None
    ) -> _Result:
        sql = " ".join(str(stmt).split()).lower()
        params = params or {}

        if sql.startswith(
            "select id, kind, name, status, target_ref, tags from skill_candidates"
        ):
            rid = _to_uuid(params["id"])
            row = self._db.skill_candidates.get(rid)
            if row is None:
                return _Result([])
            return _Result(
                [
                    _Row(
                        id=row.id,
                        kind=row.kind,
                        name=row.name,
                        status=row.status,
                        target_ref=row.target_ref,
                        tags=row.tags,
                    )
                ]
            )

        if sql.startswith(
            "select id, sub_agent_name, status, system_prompt from sub_agent_prompt_versions"
        ):
            # No prompt rows in this fake — the store falls through to
            # "not found" and update_status raises LookupError, which
            # these tests never trigger (we always seed a skill row).
            return _Result([])

        if sql.startswith("update skill_candidates"):
            rid = _to_uuid(params["id"])
            row = self._db.skill_candidates.get(rid)
            if row is None:
                return _Result([])
            # Mirror the real SQL guard: only flip if the current
            # status matches what update_status claims it was.
            if row.status != params.get("current_status"):
                return _Result([])
            row.status = str(params["new_status"])
            return _Result([])

        return _Result([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:  # pragma: no cover - unused
        return None


def _to_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> tuple[SkillCandidateStore, _FakeDB]:
    """Return a freshly wired store + the backing fake DB."""
    db = _FakeDB()
    store = SkillCandidateStore(
        db_factory=db.factory(),
        skills_root_dir=tmp_path,
    )
    return store, db


def _seed_candidate(db: _FakeDB, *, status: str) -> uuid.UUID:
    """Plant one skill candidate at an arbitrary starting status.

    Direct-inserts into the fake DB so we can exercise every
    (src, dst) edge without having to drive there via
    ``update_status`` first. Using the status as-is lets us seed
    terminal states (``retired`` / ``rejected``) which the legal-walk
    helper in ``test_candidate_store.py`` could also reach, but
    inserting skips the redundant bookkeeping.
    """
    rid = uuid.uuid4()
    db.skill_candidates[rid] = _CandidateRow(
        id=rid, name=f"seed-{rid.hex[:6]}", status=status
    )
    return rid


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Edge enumerations
# ---------------------------------------------------------------------------


def _allowed_edges() -> list[tuple[str, str]]:
    """Every ``(src, dst)`` edge listed in :data:`STATE_TRANSITIONS`."""
    return sorted(
        (src, dst)
        for src, dsts in STATE_TRANSITIONS.items()
        for dst in dsts
    )


def _disallowed_edges() -> list[tuple[str, str]]:
    """Every ``(src, dst)`` where both are known statuses, ``src != dst``
    and the edge is **not** in :data:`STATE_TRANSITIONS`.

    Same-status pairs are explicitly excluded because
    ``update_status`` treats them as idempotent no-ops (per the
    "replayed promotion event" rationale in the store docstring) —
    they're neither allowed edges nor errors.
    """
    all_statuses = sorted(ALL_STATUSES)
    allowed = set(_allowed_edges())
    return sorted(
        (src, dst)
        for src in all_statuses
        for dst in all_statuses
        if src != dst and (src, dst) not in allowed
    )


ALLOWED_EDGES = _allowed_edges()
DISALLOWED_EDGES = _disallowed_edges()


# Sanity: the two lists together cover every cross pair — nothing
# falls through the cracks of Properties 1 + 2. Guard the registry
# here too so drift in ``STATE_TRANSITIONS`` is caught at collection
# time, not silently as a missing test case.
assert len(ALLOWED_EDGES) == 9, (
    f"spec expects 9 allowed edges (R-3.4); got {len(ALLOWED_EDGES)}: "
    f"{ALLOWED_EDGES!r}"
)
assert set(ALLOWED_EDGES).isdisjoint(DISALLOWED_EDGES)


# ---------------------------------------------------------------------------
# Property 1 — every allowed edge flips the row
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
)
@given(edge=st.sampled_from(ALLOWED_EDGES))
def test_all_allowed_edges_succeed(tmp_path_factory, edge: tuple[str, str]) -> None:
    """Every edge in :data:`STATE_TRANSITIONS` must succeed and update the row.

    Seeds a fresh candidate at ``src`` and issues
    ``update_status(cid, dst)``. Post-condition: no exception raised,
    and the row's status is ``dst``.
    """
    src, dst = edge
    tmp_path = tmp_path_factory.mktemp("sm-allowed")
    store, db = _make_store(tmp_path)
    cid = _seed_candidate(db, status=src)

    # Must not raise.
    _run(store.update_status(cid, dst))

    assert db.skill_candidates[cid].status == dst


# ---------------------------------------------------------------------------
# Property 2 — every disallowed edge raises and leaves the row untouched
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
)
@given(edge=st.sampled_from(DISALLOWED_EDGES))
def test_all_disallowed_edges_raise(
    tmp_path_factory, edge: tuple[str, str]
) -> None:
    """Every non-allowed, non-self edge must raise ``InvalidStateTransition``.

    The row must also stay at ``src`` — a failed transition is a
    no-op, never a partial write.
    """
    src, dst = edge
    tmp_path = tmp_path_factory.mktemp("sm-disallowed")
    store, db = _make_store(tmp_path)
    cid = _seed_candidate(db, status=src)

    with pytest.raises(InvalidStateTransition) as exc_info:
        _run(store.update_status(cid, dst))

    assert exc_info.value.current == src
    assert exc_info.value.new == dst
    # Row unchanged.
    assert db.skill_candidates[cid].status == src


# ---------------------------------------------------------------------------
# Property 3 — terminal states stay terminal
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
)
@given(
    src=st.sampled_from(["retired", "rejected"]),
    new_status=st.sampled_from(sorted(ALL_STATUSES)),
)
def test_terminal_states_stay_terminal(
    tmp_path_factory, src: str, new_status: str
) -> None:
    """From a terminal state, only a same-status write is allowed.

    Any other transition — regardless of destination, including moves
    to the *other* terminal status — must raise
    :class:`InvalidStateTransition` and leave the row unchanged.

    The same-status case is explicitly asserted to be a silent no-op
    so promoter-side event replay (R-3.18 / P-HotReload-4) works
    even after a candidate has already been retired.
    """
    tmp_path = tmp_path_factory.mktemp("sm-terminal")
    store, db = _make_store(tmp_path)
    cid = _seed_candidate(db, status=src)

    if new_status == src:
        # Idempotent: no exception, no mutation.
        _run(store.update_status(cid, new_status))
        assert db.skill_candidates[cid].status == src
        return

    with pytest.raises(InvalidStateTransition):
        _run(store.update_status(cid, new_status))

    assert db.skill_candidates[cid].status == src


# ---------------------------------------------------------------------------
# Property 4 — random walks preserve validity
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
)
@given(
    start=st.sampled_from(sorted(ALL_STATUSES)),
    walk=st.lists(
        st.sampled_from(sorted(ALL_STATUSES)),
        min_size=0,
        max_size=20,
    ),
)
def test_random_walks_preserve_validity(
    tmp_path_factory, start: str, walk: list[str]
) -> None:
    """Drive a candidate through a random sequence of status requests.

    For each step, compute the expected outcome from
    :data:`STATE_TRANSITIONS` and verify:

    * Allowed edges (including same-status idempotent writes) complete
      without exception and the row lands at the expected status.
    * Disallowed edges raise :class:`InvalidStateTransition` and do
      not mutate the row.

    The walk is seeded at a random starting status — including
    terminal statuses — so this property covers the "trapped in
    terminal" scenario end-to-end.
    """
    tmp_path = tmp_path_factory.mktemp("sm-walk")
    store, db = _make_store(tmp_path)
    cid = _seed_candidate(db, status=start)

    expected = start
    for move in walk:
        if move == expected:
            # Idempotent no-op.
            _run(store.update_status(cid, move))
            assert db.skill_candidates[cid].status == expected
            continue

        if move in STATE_TRANSITIONS[expected]:
            _run(store.update_status(cid, move))
            expected = move
            assert db.skill_candidates[cid].status == expected
        else:
            with pytest.raises(InvalidStateTransition) as exc_info:
                _run(store.update_status(cid, move))
            assert exc_info.value.current == expected
            assert exc_info.value.new == move
            # Row must not have moved.
            assert db.skill_candidates[cid].status == expected

    # Final state must still match the model.
    assert db.skill_candidates[cid].status == expected


# ---------------------------------------------------------------------------
# Spec lock — the state machine itself matches R-3.4
# ---------------------------------------------------------------------------


def test_state_transitions_match_r_3_4() -> None:
    """Guard against silent drift in the transition table.

    R-3.4 is the source of truth; if the graph in
    :data:`STATE_TRANSITIONS` changes, this test must fail so the
    accompanying edge enumerations above get updated in the same
    commit.
    """
    assert STATE_TRANSITIONS == {
        "proposed": frozenset({"shadow", "rejected"}),
        "shadow": frozenset({"ab", "rejected", "retired"}),
        "ab": frozenset({"active", "rejected", "retired"}),
        "active": frozenset({"retired"}),
        "retired": frozenset(),
        "rejected": frozenset(),
    }
    assert ALL_STATUSES == frozenset(
        {"proposed", "shadow", "ab", "active", "retired", "rejected"}
    )
