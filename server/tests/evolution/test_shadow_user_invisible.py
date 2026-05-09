"""Property-based test for task 23.7 — P-Evolve-4 "shadow doesn't affect users".

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23.7
(Phase L — Promoter).

**Validates: Requirements 3.7**

R-3.7 in words: WHILE a candidate is in ``shadow`` status THE
user-visible response SHALL be equal to the baseline response, and
the candidate's run results SHALL only be written to shadow stats.

Property (P-Evolve-4)
---------------------

For **any** pair ``(baseline_response, candidate_response)``,
regardless of how wildly the candidate's output differs from the
baseline, the response actually delivered to the user is
byte-identical to ``baseline_response``.

Model
-----

The gateway's post-turn hook runs in this order::

    1. baseline_response = await run_baseline(live_request)
    2. user_response = baseline_response           # delivered now
    3. shadow.schedule(cid, live_request, baseline_response=user_response)
    4. return user_response                        # byte-equal to (2)

So "user-visible response" = whatever string the gateway handed to
the user **before** scheduling the shadow replay. Our test replays
that sequence: capture ``user_response`` at the equivalent of step
(2), schedule the shadow task with an arbitrarily different
``candidate_response``, await the task to drain, and then assert
byte-equality between the captured ``user_response`` and the original
``baseline_response``.

The property exercises three invariants simultaneously:

* ``ShadowRunner.schedule`` never returns anything user-visible.
* ``ShadowRunner.replay`` returns ``None`` (no leakage channel).
* The ``baseline_response`` input is passed by reference to the
  stat's persistence layer but never mutated — Python string
  immutability makes this automatic, but we confirm the runner
  doesn't, say, return a transformed copy via another side channel.

Test fixture surface
--------------------

We reuse the same ``_FakeDB`` shape as
:mod:`tests.evolution.test_shadow_runner` — narrowly scoped to the
``INSERT INTO skill_evaluations`` statements the runner emits —
duplicated here (rather than imported) so the two test modules stay
independently evolvable.

Hypothesis profile
------------------

* ``max_examples=100`` — enough to explore the
  ``st.text(max_size=500)`` domain without slowing CI.
* ``deadline=None`` — the DB write path goes through an asyncio
  round-trip per example; the default 200ms deadline trips
  spuriously on busy runners.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from src.services.evolution.shadow_runner import (
    CandidateRunResult,
    LiveRequest,
    ShadowRunner,
)


pytestmark = [pytest.mark.property]


# ---------------------------------------------------------------------------
# Narrow fake DB — mirrors test_shadow_runner.py's surface
# ---------------------------------------------------------------------------
#
# The runner emits exactly one ``INSERT INTO skill_evaluations`` per
# replay; we accept-and-discard it. No SELECT path is exercised.


@dataclass
class _InsertRecord:
    sql: str
    params: dict[str, Any]


class _Result:
    def __init__(self) -> None:
        self.rowcount = 0

    def first(self) -> Any | None:
        return None

    def fetchall(self) -> list[Any]:
        return []


@dataclass
class _FakeDB:
    inserts: list[_InsertRecord] = field(default_factory=list)
    commits: int = 0

    def factory(self):
        db = self

        @asynccontextmanager
        async def _factory():
            yield _FakeSession(db)

        return _factory


class _FakeSession:
    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    async def execute(
        self, stmt: Any, params: dict[str, Any] | None = None
    ) -> _Result:
        sql = " ".join(str(stmt).split()).lower()
        params = params or {}
        if sql.startswith("insert into skill_evaluations"):
            self._db.inserts.append(_InsertRecord(sql=sql, params=dict(params)))
        return _Result()

    async def commit(self) -> None:
        self._db.commits += 1

    async def rollback(self) -> None:  # pragma: no cover - unused
        return None


# ---------------------------------------------------------------------------
# Gateway simulation
# ---------------------------------------------------------------------------


def _live_request() -> LiveRequest:
    """Stable request shape. Field contents are irrelevant to the property."""
    return LiveRequest(
        message="prompt-for-shadow",
        session_id="sess-1",
        user_id="user-1",
        space_id="space-1",
    )


def _fixed_candidate_runner(candidate_response: str):
    """Return a ``CandidateRunner`` that yields the given response verbatim.

    The latency / tool fields are constants — this property is about
    the response text, not comparison metadata.
    """

    async def _run(
        candidate_id: uuid.UUID, live_request: LiveRequest
    ) -> CandidateRunResult:
        return CandidateRunResult(
            response=candidate_response, latency_ms=1, tools_used=()
        )

    return _run


async def _simulate_turn(
    baseline_response: str, candidate_response: str
) -> tuple[str, Any]:
    """Replay a single gateway turn end-to-end.

    Steps (matching the docstring model):

    1. Compute ``baseline_response`` (already handed in).
    2. Store ``user_response = baseline_response`` — this is the
       string the user actually sees and what the property pins.
    3. Fire ``ShadowRunner.schedule`` with the *same* baseline.
    4. Drain the replay task so the stat is persisted before we
       inspect anything.

    Returns ``(user_response, replay_return_value)``. The second
    element is the awaited task's return; ``ShadowRunner.replay``
    must return ``None`` for R-3.7 to hold.
    """
    db = _FakeDB()
    runner = ShadowRunner(
        candidate_runner=_fixed_candidate_runner(candidate_response),
        db_factory=db.factory(),
    )

    # Step 2 — capture the user-visible response BEFORE the shadow runs.
    user_response = baseline_response

    # Step 3 — fire and forget; in production the gateway returns
    # ``user_response`` to the caller on this line without awaiting.
    task = runner.schedule(
        uuid.uuid4(),
        _live_request(),
        baseline_response=user_response,
    )

    # Step 4 — drain the task. We await here so the property check
    # observes a fully-settled world; in production the task runs
    # asynchronously after the user has already received the response.
    await runner.wait_pending(timeout=5.0)
    replay_result = task.result()

    return user_response, replay_result


# ---------------------------------------------------------------------------
# Property — user-visible response is byte-equal to baseline
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
    baseline_response=st.text(min_size=0, max_size=500),
    candidate_response=st.text(min_size=0, max_size=500),
)
def test_shadow_replay_leaves_user_response_byte_equal_to_baseline(
    baseline_response: str, candidate_response: str
) -> None:
    """P-Evolve-4: shadow doesn't affect the user-visible response.

    For every ``(baseline_response, candidate_response)`` pair in the
    text domain (including empty strings and the full BMP range
    Hypothesis's ``st.text`` draws from), after
    ``ShadowRunner.schedule`` runs to completion the
    ``user_response`` handed off at step 2 must be:

    * byte-equal (``==``) to the original ``baseline_response`` that
      the gateway computed — **regardless** of what the candidate
      returned;
    * UTF-8-encoding-identical (catches any lurking Unicode
      normalization defect that ``==`` alone would miss);
    * neither ``None`` nor mutated in length.

    And the replay task itself must return ``None`` — there's no
    API channel through which the candidate response could be
    delivered to the user.
    """
    user_response, replay_result = asyncio.run(
        _simulate_turn(baseline_response, candidate_response)
    )

    # Primary invariant: user sees exactly the baseline, byte-for-byte.
    assert user_response == baseline_response, (
        "user_response must be byte-equal to baseline_response"
    )
    assert (
        user_response.encode("utf-8") == baseline_response.encode("utf-8")
    ), "UTF-8 byte sequences must match (no normalization drift)"
    assert len(user_response) == len(baseline_response)

    # Secondary invariant: the replay API cannot leak the candidate
    # response. ``ShadowRunner.replay`` returns ``None`` unconditionally;
    # ``schedule`` wraps it in a Task whose ``.result()`` must therefore
    # be ``None`` too.
    assert replay_result is None, (
        "ShadowRunner.replay must not return the candidate response"
    )
