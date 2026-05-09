"""Unit tests for :mod:`src.services.evaluation.scoring`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 17.5 /
R-4.5.

Covers:

* Weighted-mean + per-rubric + pass-rate basics.
* Edge cases: empty input, single-item set, zero / negative weights
  get coerced to ``1.0`` (no silent drops).
* Tag aggregation splits a combined set by scenario.
* ``aggregate()`` returns values that match the primitive helpers.
"""
from __future__ import annotations

import pytest

from src.services.evaluation.scoring import (
    AggregateScore,
    ItemScore,
    aggregate,
    pass_rate,
    per_rubric_mean,
    weighted_mean,
)


# ---------------------------------------------------------------------------
# Weighted mean
# ---------------------------------------------------------------------------


def test_weighted_mean_equal_weights():
    items = [
        ItemScore(item_id="a", overall=0.4),
        ItemScore(item_id="b", overall=0.6),
        ItemScore(item_id="c", overall=1.0),
    ]
    assert weighted_mean(items) == pytest.approx((0.4 + 0.6 + 1.0) / 3)


def test_weighted_mean_explicit_weights():
    items = [
        ItemScore(item_id="a", overall=0.2, weight=1.0),
        ItemScore(item_id="b", overall=1.0, weight=3.0),
    ]
    # (0.2*1 + 1.0*3) / 4 = 3.2 / 4 = 0.8
    assert weighted_mean(items) == pytest.approx(0.8)


def test_weighted_mean_empty_returns_zero():
    assert weighted_mean([]) == 0.0


def test_weighted_mean_zero_weight_coerced_to_one():
    """A ``weight=0`` entry must still contribute — zero would silently drop it."""
    items = [
        ItemScore(item_id="a", overall=1.0, weight=0.0),
        ItemScore(item_id="b", overall=0.0, weight=1.0),
    ]
    # Both contribute with weight 1 → mean 0.5
    assert weighted_mean(items) == pytest.approx(0.5)


def test_weighted_mean_negative_weight_coerced_to_one():
    items = [
        ItemScore(item_id="a", overall=0.0, weight=-3.0),
        ItemScore(item_id="b", overall=1.0, weight=1.0),
    ]
    assert weighted_mean(items) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Per-rubric breakdown
# ---------------------------------------------------------------------------


def test_per_rubric_mean_handles_missing_rubrics():
    items = [
        ItemScore(
            item_id="a",
            overall=0.8,
            rubric={"accuracy": 0.9, "brevity": 0.5},
        ),
        ItemScore(
            item_id="b",
            overall=0.4,
            rubric={"accuracy": 0.3},
        ),
        ItemScore(item_id="c", overall=0.1, rubric=None),
    ]
    out = per_rubric_mean(items)

    # accuracy seen on a,b: (0.9 + 0.3) / 2 = 0.6
    assert out["accuracy"] == pytest.approx(0.6)
    # brevity only on a: 0.5
    assert out["brevity"] == pytest.approx(0.5)
    # item c had no rubric → no new keys.
    assert set(out.keys()) == {"accuracy", "brevity"}


def test_per_rubric_mean_respects_weights():
    items = [
        ItemScore(
            item_id="a", overall=0, weight=3.0, rubric={"accuracy": 1.0}
        ),
        ItemScore(
            item_id="b", overall=0, weight=1.0, rubric={"accuracy": 0.0}
        ),
    ]
    assert per_rubric_mean(items)["accuracy"] == pytest.approx(0.75)


def test_per_rubric_mean_empty_items():
    assert per_rubric_mean([]) == {}


# ---------------------------------------------------------------------------
# Pass rate
# ---------------------------------------------------------------------------


def test_pass_rate_weighted():
    items = [
        ItemScore(item_id="a", passed=True, weight=2.0),
        ItemScore(item_id="b", passed=False, weight=1.0),
        ItemScore(item_id="c", passed=True, weight=1.0),
    ]
    # passed weight = 2 + 1 = 3; total = 4 → 0.75
    assert pass_rate(items) == pytest.approx(0.75)


def test_pass_rate_empty_is_zero():
    assert pass_rate([]) == 0.0


def test_pass_rate_all_failed():
    items = [
        ItemScore(item_id="a", passed=False),
        ItemScore(item_id="b", passed=False),
    ]
    assert pass_rate(items) == 0.0


# ---------------------------------------------------------------------------
# aggregate() top-level
# ---------------------------------------------------------------------------


def test_aggregate_single_item():
    items = [
        ItemScore(
            item_id="only",
            overall=0.5,
            passed=True,
            rubric={"accuracy": 0.5},
            tags=["scenario:knowledge_mgmt"],
        ),
    ]
    agg = aggregate(items)
    assert isinstance(agg, AggregateScore)
    assert agg.n_samples == 1
    assert agg.weighted_mean == pytest.approx(0.5)
    assert agg.pass_rate == pytest.approx(1.0)
    assert agg.per_rubric == {"accuracy": pytest.approx(0.5)}
    assert agg.per_tag == {"scenario:knowledge_mgmt": pytest.approx(0.5)}
    assert agg.total_weight == pytest.approx(1.0)


def test_aggregate_empty():
    agg = aggregate([])
    assert agg.n_samples == 0
    assert agg.weighted_mean == 0.0
    assert agg.pass_rate == 0.0
    assert agg.per_rubric == {}
    assert agg.per_tag == {}
    assert agg.total_weight == 0.0


def test_aggregate_per_tag_separates_scenarios():
    """A combined set with mixed scenario tags is split per-tag correctly."""
    items = [
        ItemScore(
            item_id="k1", overall=0.9, tags=["scenario:knowledge_mgmt"]
        ),
        ItemScore(
            item_id="k2", overall=0.7, tags=["scenario:knowledge_mgmt"]
        ),
        ItemScore(
            item_id="f1", overall=0.5, tags=["scenario:fault_triage"]
        ),
    ]
    agg = aggregate(items)
    assert agg.per_tag["scenario:knowledge_mgmt"] == pytest.approx(0.8)
    assert agg.per_tag["scenario:fault_triage"] == pytest.approx(0.5)


def test_aggregate_consistency_with_primitives():
    """``aggregate()`` numbers match the primitive helpers exactly."""
    items = [
        ItemScore(
            item_id="a",
            overall=0.7,
            passed=True,
            weight=2.0,
            rubric={"accuracy": 0.8, "brevity": 0.5},
            tags=["scenario:x"],
        ),
        ItemScore(
            item_id="b",
            overall=0.3,
            passed=False,
            weight=1.0,
            rubric={"accuracy": 0.2},
            tags=["scenario:y"],
        ),
    ]
    agg = aggregate(items)
    assert agg.weighted_mean == pytest.approx(weighted_mean(items))
    assert agg.pass_rate == pytest.approx(pass_rate(items))
    assert agg.per_rubric == {
        k: pytest.approx(v) for k, v in per_rubric_mean(items).items()
    }
