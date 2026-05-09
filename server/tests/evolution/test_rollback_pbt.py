"""Property-based tests for ``Promoter.rollback_prompt`` (task 23.6).

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 23.6
(Phase L — Promoter + Rollback). Correctness property **P-Evolve-3**:
for any history of prompt-version activations, a single rollback of
the currently-active version restores the immediately-previous active
version (ordered by ``activated_at DESC``), marks the current version
``retired`` with ``retired_at`` set, and emits exactly one Kafka
event on ``ops.agent.promotion`` with the rollback payload shape.

**Validates: Requirements 3.9**

R-3.9 in concrete terms:

* ``rollback(target)`` flips the currently ``active`` row to
  ``retired`` and sets its ``retired_at`` timestamp.
* The **most recently activated** prior row is restored to ``active``
  (``retired_at = NULL``, ``activated_at`` bumped).
* A single Kafka event is published with
  ``kind='prompt_patch'``, ``event_kind='rollback'``,
  ``target_ref=<sub_agent_name>``, ``new_version_id=<restored_id>``,
  ``active_version_id=<restored_id>``,
  ``retired_version_id=<retired_id>``.

Test surface
------------

The unit tests in :mod:`tests.evolution.test_rollback` exercise
hand-crafted fixtures (two rows, three rows). This PBT raises the
sample count: hypothesis generates N ∈ [2, 10] versions with
randomized ``activated_at`` timestamps, picks exactly one of them to
be the current ``active`` row, and asserts the rollback post-condition
holds across every such history.

The fake DB + fake Kafka producer are reused verbatim from
``test_rollback.py`` (imported directly) so no implementation detail
is duplicated and the fake stays the single source of truth for the
SQL surface the Promoter actually issues.

Hypothesis profile: ``max_examples=100``, ``deadline=None`` — the
rollback involves an ``asyncio.run`` round-trip through the fake DB
per example and the wall-clock of that is irrelevant to the
property. ``HealthCheck.function_scoped_fixture`` is suppressed
because the body re-uses the incoming ``tmp_path_factory`` across
examples, which hypothesis would otherwise flag.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from src.services.evolution.promoter import RollbackResult

# Re-use the exact in-memory fakes / helpers the unit tests already
# verify against the real Promoter — keeps the PBT honest and avoids
# drift between the two test surfaces.
from tests.evolution.test_rollback import (  # noqa: E402
    _FakeDB,
    _FakeProducer,
    _make_promoter,
    _mk_prompt_row,
)


pytestmark = [pytest.mark.property]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


# Sub-agent names: a small pool so hypothesis doesn't waste examples
# on string exploration — the property is independent of the name,
# we just need a stable identifier.
_SUB_AGENT_NAMES = st.sampled_from(
    ["ops", "triage", "monitor", "knowledge", "incident"]
)


@st.composite
def _activation_histories(draw: Any) -> tuple[str, list[float], int]:
    """Generate one prompt-version history.

    Returns a tuple ``(sub_agent_name, activation_offsets, active_idx)``:

    * ``sub_agent_name`` — the target the rollback will operate on.
    * ``activation_offsets`` — N ∈ [2, 10] distinct floats in hours
      representing ``activated_at`` offsets backward from "now". All
      values are distinct so the ``ORDER BY activated_at DESC`` tie-
      break in :meth:`Promoter._select_previous_prompt` is
      unambiguous — the property statement ("restores the
      immediately-previous active version") is only well-defined
      when no two activations collide on the same timestamp.
    * ``active_idx`` — which entry in ``activation_offsets`` is the
      current ``active`` row. Every other entry is a retired row
      (status="retired", ``retired_at`` set to a time *after* its
      own ``activated_at``). The active row's ``activated_at``
      need not be the most recent — the promoter resolves "previous"
      via ``activated_at DESC`` regardless of which row currently
      holds the ``active`` flag.
    """
    name = draw(_SUB_AGENT_NAMES)
    n = draw(st.integers(min_value=2, max_value=10))
    # Distinct integer-hour offsets so no two activations tie.
    # Range is wide enough (1..240 hours) that hypothesis can always
    # find ``n`` distinct values even at n=10.
    offsets_int = draw(
        st.lists(
            st.integers(min_value=1, max_value=240),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    # Convert to float hours so the ``_mk_prompt_row`` factory can
    # feed them into ``timedelta(hours=...)`` without re-casting.
    offsets = [float(v) for v in offsets_int]
    active_idx = draw(st.integers(min_value=0, max_value=n - 1))
    return name, offsets, active_idx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_history(
    db: _FakeDB,
    *,
    sub_agent_name: str,
    offsets: list[float],
    active_idx: int,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed ``db.prompt_versions`` from a generated history.

    Returns ``(expected_active_id, expected_previous_id)`` — the
    properties are asserted against these two ids after rollback.

    ``expected_previous_id`` is resolved by the same rule the
    promoter uses: among all rows *other than* the current active
    whose ``activated_at`` is non-null, pick the one with the
    greatest ``activated_at``. Tying offsets are excluded by
    strategy (see :func:`_activation_histories`) so this is a
    deterministic choice per example.
    """
    now = datetime.now(UTC)
    rows = []
    for i, off_hours in enumerate(offsets):
        activated_at = now - timedelta(hours=off_hours)
        if i == active_idx:
            row = _mk_prompt_row(
                sub_agent_name=sub_agent_name,
                prompt=f"v{i}",
                status="active",
                activated_at=activated_at,
            )
        else:
            # Retired rows have ``retired_at`` strictly after their
            # own ``activated_at`` — mirrors production shape and
            # keeps the fake's state model realistic.
            retired_at = activated_at + timedelta(minutes=15)
            row = _mk_prompt_row(
                sub_agent_name=sub_agent_name,
                prompt=f"v{i}",
                status="retired",
                activated_at=activated_at,
                retired_at=retired_at,
            )
        rows.append(row)
        db.prompt_versions[row.id] = row

    active_row = rows[active_idx]
    # Expected "previous" = most-recently-activated non-current row.
    non_current = [r for r in rows if r.id != active_row.id]
    assert non_current, "strategy guarantees n>=2 so at least one exists"
    previous_row = max(
        non_current,
        key=lambda r: r.activated_at or datetime.min.replace(tzinfo=UTC),
    )
    return active_row.id, previous_row.id


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Property — P-Evolve-3
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
)
@given(history=_activation_histories())
def test_rollback_restores_previous_active_version(
    history: tuple[str, list[float], int],
) -> None:
    """R-3.9 / P-Evolve-3: rollback swaps current ↔ prior-active row.

    Given N ∈ [2, 10] prompt versions with randomized ``activated_at``
    offsets and one row marked ``active``, ``rollback_prompt``:

    1. Returns ``ok=True`` with the correct retired / restored ids.
    2. Flips the current active row to ``status='retired'`` and sets
       ``retired_at``.
    3. Flips the immediately-previous (highest-``activated_at``) row
       back to ``status='active'`` with ``retired_at=None`` and a
       fresh ``activated_at``.
    4. Commits exactly once (single-transaction guarantee — R-3.19).
    5. Publishes exactly one Kafka event on ``ops.agent.promotion``
       with ``kind='prompt_patch'``, ``event_kind='rollback'``,
       ``target_ref=<sub_agent_name>``,
       ``new_version_id=<restored_id>``,
       ``active_version_id=<restored_id>``,
       ``retired_version_id=<retired_id>``.

    Every row untouched by the rollback remains exactly as seeded —
    no side effects on unrelated history rows.
    """
    sub_agent_name, offsets, active_idx = history
    db = _FakeDB()
    expected_retired_id, expected_restored_id = _build_history(
        db,
        sub_agent_name=sub_agent_name,
        offsets=offsets,
        active_idx=active_idx,
    )

    # Capture untouched rows' pre-state so we can assert the rollback
    # leaves them alone. Cloning the dataclass fields is sufficient —
    # the fake stores plain dataclass instances.
    untouched_ids = [
        rid
        for rid in db.prompt_versions
        if rid not in {expected_retired_id, expected_restored_id}
    ]
    pre_untouched = {
        rid: (
            db.prompt_versions[rid].status,
            db.prompt_versions[rid].activated_at,
            db.prompt_versions[rid].retired_at,
        )
        for rid in untouched_ids
    }

    promoter, producer, _invalidate = _make_promoter(db)
    result = _run(promoter.rollback_prompt(sub_agent_name))

    # -- 1. Result shape ----------------------------------------------------
    assert isinstance(result, RollbackResult)
    assert result.ok is True
    assert result.kind == "prompt_patch"
    assert result.name == sub_agent_name
    assert result.retired_version_id == expected_retired_id
    assert result.restored_version_id == expected_restored_id

    # -- 2. Retired row state -----------------------------------------------
    retired_row = db.prompt_versions[expected_retired_id]
    assert retired_row.status == "retired", (
        "post-rollback: current active must be flipped to retired"
    )
    assert retired_row.retired_at is not None, (
        "post-rollback: retired_at must be set on the old active row"
    )

    # -- 3. Restored row state ----------------------------------------------
    restored_row = db.prompt_versions[expected_restored_id]
    assert restored_row.status == "active", (
        "post-rollback: previous active must be restored"
    )
    assert restored_row.retired_at is None, (
        "post-rollback: restored row's retired_at must be cleared"
    )
    assert restored_row.activated_at is not None, (
        "post-rollback: restored row's activated_at must be bumped"
    )

    # -- 4. Single transaction ---------------------------------------------
    assert db.commit_count == 1, (
        f"expected exactly one commit (R-3.19), got {db.commit_count}"
    )

    # -- 5. Kafka event emitted with correct payload -----------------------
    assert result.event_published is True
    assert len(producer.sent) == 1, (
        f"expected exactly one Kafka event, got {len(producer.sent)}"
    )
    topic, payload_bytes = producer.sent[0]
    assert topic == "ops.agent.promotion"

    payload = json.loads(payload_bytes.decode("utf-8"))
    assert payload["kind"] == "prompt_patch"
    assert payload["event_kind"] == "rollback"
    assert payload["target_ref"] == sub_agent_name
    assert payload["sub_agent_name"] == sub_agent_name
    assert payload["to_status"] == "active"
    assert payload["new_version_id"] == str(expected_restored_id)
    assert payload["active_version_id"] == str(expected_restored_id)
    assert payload["retired_version_id"] == str(expected_retired_id)
    # Rollback events carry a deterministic event_id prefix so the
    # PromptReloader can deduplicate retries (R-3.18).
    assert payload["event_id"].startswith("rollback-prompt-")

    # -- 6. Unrelated rows left untouched ----------------------------------
    for rid, pre_state in pre_untouched.items():
        current = db.prompt_versions[rid]
        post_state = (current.status, current.activated_at, current.retired_at)
        assert post_state == pre_state, (
            f"row {rid} was modified but should have been untouched; "
            f"pre={pre_state!r}, post={post_state!r}"
        )
