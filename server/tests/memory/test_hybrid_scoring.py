"""PBT: hybrid scoring monotonicity for ``MemoryTier.warm_recall``.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 8.5 / R-2.8.

The canonical score is::

    score = 0.5 * sim + 0.3 * recency + 0.2 * (1.0 if pinned else 0.0)
    recency = 1 / (1 + age_days / 7)

We verify three independent monotonicity properties with Hypothesis.
The tests are pure — no DB, no Redis — so they can always run.
"""
from __future__ import annotations

import math

import pytest
from hypothesis import given, settings as hsettings, strategies as st

from src.services.memory.tier import _hybrid_score, _recency


pytestmark = [pytest.mark.property]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


_sim = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_age = st.floats(min_value=0.0, max_value=365.0, allow_nan=False, allow_infinity=False)
_delta = st.floats(min_value=0.01, max_value=0.5, allow_nan=False, allow_infinity=False)


# ---------------------------------------------------------------------------
# Property 1 — sim ↑ ⇒ score ↑ (others fixed)
# ---------------------------------------------------------------------------


@given(sim=_sim, delta=_delta, age=_age, pinned=st.booleans())
@hsettings(max_examples=200, deadline=None)
def test_increasing_sim_does_not_decrease_score(
    sim: float, delta: float, age: float, pinned: bool
) -> None:
    """**Validates: Requirements 2.8** — hybrid weight on similarity is positive."""
    sim2 = min(1.0, sim + delta)
    s1 = _hybrid_score(sim, age, pinned)
    s2 = _hybrid_score(sim2, age, pinned)
    assert s2 + 1e-9 >= s1, f"non-monotone in sim: {sim}+{delta}→{sim2} {s1}>{s2}"


# ---------------------------------------------------------------------------
# Property 2 — pinned False→True ⇒ score += 0.2 (others fixed)
# ---------------------------------------------------------------------------


@given(sim=_sim, age=_age)
@hsettings(max_examples=200, deadline=None)
def test_pinning_adds_at_least_02(sim: float, age: float) -> None:
    """**Validates: Requirements 2.8** — pinned weight is exactly 0.2."""
    unpinned = _hybrid_score(sim, age, False)
    pinned = _hybrid_score(sim, age, True)
    diff = pinned - unpinned
    assert diff >= 0.2 - 1e-6, f"pinned bonus too small: {diff}"
    assert diff <= 0.2 + 1e-6, f"pinned bonus too large: {diff}"


# ---------------------------------------------------------------------------
# Property 3 — age ↑ ⇒ recency ↓ ⇒ score ↓ (others fixed)
# ---------------------------------------------------------------------------


@given(sim=_sim, age=_age, delta=_delta, pinned=st.booleans())
@hsettings(max_examples=200, deadline=None)
def test_increasing_age_does_not_increase_score(
    sim: float, age: float, delta: float, pinned: bool
) -> None:
    """**Validates: Requirements 2.8** — recency component is monotone-decreasing in age."""
    age2 = age + delta * 30.0  # scale up the delta so float noise can't hide the trend
    r1 = _recency(age)
    r2 = _recency(age2)
    assert r2 <= r1 + 1e-9
    s1 = _hybrid_score(sim, age, pinned)
    s2 = _hybrid_score(sim, age2, pinned)
    assert s2 <= s1 + 1e-9


# ---------------------------------------------------------------------------
# Sanity (non-PBT) checks
# ---------------------------------------------------------------------------


def test_score_bounded_in_reasonable_range() -> None:
    # Max: sim=1, age=0, pinned=True → 0.5 + 0.3 + 0.2 = 1.0
    assert math.isclose(_hybrid_score(1.0, 0.0, True), 1.0, abs_tol=1e-9)
    # Min at sim=0, pinned=False, age→∞ → 0.0
    assert _hybrid_score(0.0, 1e9, False) < 1e-3


def test_recency_zero_age_is_one() -> None:
    assert math.isclose(_recency(0.0), 1.0, abs_tol=1e-9)
