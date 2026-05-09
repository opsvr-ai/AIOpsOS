"""Property-based tests for the promotion epsilon rule — task 22.5.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 22.5
(Phase K — Evaluator). Correctness property **P-Evolve-2**: a
candidate may only promote (``shadow → ab`` or ``ab → active``) when
its latest ``skill_evaluations.candidate_score`` is within
:data:`~src.services.evolution.promotion_rules.PROMOTION_EPSILON` of
the paired ``baseline_score``.

**Validates: Requirements 3.6**

R-3.6 in concrete terms::

    WHEN c.status transitions (shadow → ab | ab → active)
    THE latest skill_evaluations.candidate_score
    SHALL ≥ baseline_score - ε   where  ε = 0.02

Test surface
------------

Two hypothesis properties. Both import the pure predicate
:func:`src.services.evolution.promotion_rules.can_promote` so the
test drives the rule itself, not a mock of it:

* ``test_can_promote_matches_epsilon_rule`` — for all
  ``(baseline_score, candidate_score) ∈ [0.0, 1.0]``, ``can_promote``
  returns ``True`` iff ``candidate_score >= baseline_score - 0.02``.
  Asserts both directions (no false positives, no false negatives)
  and locks in the documented edge cases from the spec.
* ``test_promoter_stub_rejects_regressions`` — drives a minimal
  Promoter stub that couples the R-3.6 check to R-3.4's state-machine
  (i.e. "refuse to advance past ``shadow`` when the score guard fails")
  and asserts that rejected candidates stay in ``shadow`` rather than
  leaping to ``ab``.

Why a stub rather than the real
:class:`~src.services.evolution.promoter.Promoter`? The Promoter
lands in Phase L (task 23). Task 22.5 is specifically about locking
in the *rule* before the Promoter is wired, so downstream work can
rely on ``can_promote`` being the single source of truth for R-3.6.
A test that faithfully reproduces the promotion decision path
around ``can_promote`` is sufficient to validate P-Evolve-2; the
Promoter itself is covered by the P-Evolve-3 / P-Evolve-4 tests
in task 23.6 / 23.7.

Hypothesis profile: ``max_examples=200``, ``deadline=None`` as
specified in the task notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from src.services.evolution.promotion_rules import (
    PROMOTION_EPSILON,
    can_promote,
)


pytestmark = [pytest.mark.property]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


# Scores live in [0.0, 1.0] per the weighted-mean aggregator in
# :mod:`src.services.evaluation.scoring`. ``allow_nan=False`` +
# ``allow_infinity=False`` keep the search space meaningful — a NaN
# score is a broken evaluator row, not something the promotion rule
# should be reasoning about, and its behaviour is documented in
# :func:`can_promote`'s docstring independently.
_score_strategy = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


# ---------------------------------------------------------------------------
# Property 1 — can_promote directly encodes R-3.6
# ---------------------------------------------------------------------------


@hsettings(max_examples=200, deadline=None)
@given(baseline=_score_strategy, candidate=_score_strategy)
def test_can_promote_matches_epsilon_rule(
    baseline: float, candidate: float
) -> None:
    """Validates: Requirements 3.6.

    For any ``(baseline, candidate)`` pair in ``[0.0, 1.0]``,
    :func:`can_promote` returns ``True`` iff
    ``candidate >= baseline - PROMOTION_EPSILON``.

    Checks both directions — false positives (the rule permitting a
    transition the spec forbids) and false negatives (the rule
    blocking a transition the spec permits) — so a bug in either
    direction is caught.
    """
    allowed = can_promote(baseline, candidate)
    reference = candidate >= baseline - PROMOTION_EPSILON

    # Both directions, plus a direct equality so a shrinker points at
    # the exact counter-example instead of a boolean ``assert allowed``.
    assert allowed is reference, (
        f"can_promote({baseline!r}, {candidate!r}) returned {allowed}, "
        f"expected {reference} for baseline - epsilon = "
        f"{baseline - PROMOTION_EPSILON!r}"
    )


# ---------------------------------------------------------------------------
# Spec-anchored edge cases — locked in as explicit unit checks so any
# change to PROMOTION_EPSILON or the comparison direction trips this
# test in isolation (before the hypothesis search even starts).
# ---------------------------------------------------------------------------


def test_epsilon_value_is_0_02() -> None:
    """Spec lock: ε = 0.02 per R-3.6.

    If the epsilon ever changes, this test (and the surrounding
    property tests' reference arithmetic) must be updated together.
    """
    assert PROMOTION_EPSILON == 0.02


def test_boundary_exact_tie_allows_promotion() -> None:
    """``b - c == 0.02`` exactly → promotion allowed.

    R-3.6 uses ``≥`` not ``>``; a candidate that sits exactly on the
    epsilon boundary is within the budget we agreed to spend.
    """
    assert can_promote(baseline_score=0.8, candidate_score=0.78) is True


def test_candidate_beats_baseline_always_promotes() -> None:
    """``c > b`` → always promote; the regression guard is vacuous."""
    assert can_promote(baseline_score=0.5, candidate_score=0.6) is True
    assert can_promote(baseline_score=0.0, candidate_score=1.0) is True


def test_maximum_regression_never_promotes() -> None:
    """``c=0.0, b=1.0`` → regression of 1.0 ≫ ε, must refuse."""
    assert can_promote(baseline_score=1.0, candidate_score=0.0) is False


def test_just_outside_epsilon_refuses_promotion() -> None:
    """``b - c`` slightly > ε → refuse.

    Picked so the difference (``0.03``) is unambiguously larger than
    ``0.02`` under binary floating-point, steering clear of rounding
    traps around e.g. ``0.1 + 0.2``.
    """
    assert can_promote(baseline_score=0.8, candidate_score=0.77) is False


# ---------------------------------------------------------------------------
# Property 2 — a Promoter stub honours R-3.6 + R-3.4 jointly
# ---------------------------------------------------------------------------
#
# Why a stub?
# -----------
# The full :class:`Promoter` (task 23.x) isn't wired yet. Task 22.5's
# scope is specifically the *rule* — not the Promoter's full decision
# tree (shadow sample collection, A/B rollout percent, safety checks).
# The stub below models the one decision point that matters for
# P-Evolve-2: when :func:`can_promote` returns ``False``, the candidate
# must not advance along the ``shadow → ab`` edge and must remain in
# ``shadow``. A future refactor can replace ``_StubPromoter.step`` with
# a thin adapter around ``Promoter.step`` without changing the test.


@dataclass
class _StubCandidate:
    """Narrow-view candidate row as seen by the Promoter.

    Only the fields the rule looks at — the state machine edge label
    (``status``) and the latest evaluation scores — are modelled.
    Everything else (kind, name, tags, tool_config snapshots) is
    irrelevant to R-3.6.
    """

    status: str
    baseline_score: float
    candidate_score: float
    # Records every state transition this candidate underwent so the
    # test can assert not just "landed at ab" but "never passed
    # through ab" for rejected candidates.
    history: list[str] = field(default_factory=list)


class _StubPromoter:
    """Minimal Promoter double — wraps :func:`can_promote` + R-3.4.

    ``step`` models the single decision point under test: given a
    candidate in ``shadow`` or ``ab`` with a freshly-recorded
    evaluation, advance it one edge along
    :data:`~src.services.evolution.candidate_store.STATE_TRANSITIONS`
    iff the R-3.6 predicate allows it. When the predicate refuses,
    the candidate is left exactly where it was.

    This deliberately doesn't model ``proposed → shadow`` (that edge
    isn't guarded by R-3.6 — the candidate has no evaluation yet
    at that point) nor the terminal states.
    """

    # The two edges R-3.6 guards, in spec order.
    _NEXT: dict[str, str] = {"shadow": "ab", "ab": "active"}

    def step(self, candidate: _StubCandidate) -> str:
        """Attempt to advance *candidate* one edge. Returns the new
        status. The candidate's ``status`` attribute is updated in
        place and the transition is appended to its ``history``.
        """
        current = candidate.status
        if current not in self._NEXT:
            # Non-promotable status — leave untouched.
            return current

        if not can_promote(
            candidate.baseline_score, candidate.candidate_score
        ):
            # R-3.6 refuses the transition. Candidate stays put;
            # the history does not record a ghost edge.
            return current

        nxt = self._NEXT[current]
        candidate.status = nxt
        candidate.history.append(f"{current}->{nxt}")
        return nxt


@hsettings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    start_status=st.sampled_from(["shadow", "ab"]),
    baseline=_score_strategy,
    candidate_score=_score_strategy,
)
def test_promoter_stub_rejects_regressions(
    start_status: str, baseline: float, candidate_score: float
) -> None:
    """Validates: Requirements 3.6.

    Property P-Evolve-2 end-to-end: given any candidate in
    ``shadow`` or ``ab`` with arbitrary baseline/candidate scores,
    the promoter advances one edge iff ``can_promote`` permits; when
    ``can_promote`` refuses, the candidate stays at ``start_status``
    and never records a transition.

    This is the regression guard P-Evolve-2 pins down: no rejected
    candidate may leap from ``shadow`` to ``ab`` (or from ``ab`` to
    ``active``) on a score regression.
    """
    promoter = _StubPromoter()
    cand = _StubCandidate(
        status=start_status,
        baseline_score=baseline,
        candidate_score=candidate_score,
    )

    final = promoter.step(cand)
    should_promote = can_promote(baseline, candidate_score)

    if should_promote:
        expected_next = {"shadow": "ab", "ab": "active"}[start_status]
        assert final == expected_next, (
            f"expected promotion {start_status} -> {expected_next} "
            f"(baseline={baseline!r}, candidate={candidate_score!r}), "
            f"got {final}"
        )
        assert cand.status == expected_next
        assert cand.history == [f"{start_status}->{expected_next}"]
    else:
        # Rejected candidates remain at start_status. This is the
        # assertion P-Evolve-2 exists to enforce — no score-regressing
        # candidate may advance along the state machine.
        assert final == start_status, (
            f"regression candidate (baseline={baseline!r}, "
            f"candidate={candidate_score!r}) leapt from {start_status} "
            f"to {final}; R-3.6 violated"
        )
        assert cand.status == start_status
        assert cand.history == []
