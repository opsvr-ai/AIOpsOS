"""CI-gated smoke test for the full eval harness (task 26.1).

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 26.1
/ R-11.1 ~ R-11.5.

Purpose
-------

Task 26.1 asks us to run ``scripts/eval_run.py --set * --baseline`` for
all five scenario eval sets and assert the weighted-mean score meets
the per-scenario R-11.x targets:

* knowledge_mgmt_v1 ≥ 6.5/10
* fault_triage_v1  ≥ 6.0/10
* incident_coord_v1 ≥ 6.0/10
* capacity_mgmt_v1 ≥ 5.5/10
* runbook_mgmt_v1  ≥ 6.0/10

Running the real thing inside PR CI is not appropriate: it requires a
live LLM judge (task 22.2), the full RuntimeGateway stack (task 14.x),
and ~200 model invocations (5 sets × 40 target items) which would
burn tokens and add minutes to CI.

Instead, this test provides an **end-to-end smoke** of the harness
itself:

1. For each of the 5 committed seed JSONL files, load every item
   through the real ``scripts/eval_run`` loader.
2. Run the ``stub`` grader (deterministic 0.5 per item) through the
   real ``_run_items`` + :func:`aggregate` code path.
3. Assert the shape of the aggregate: correct item count, per-tag
   breakdown matches the scenario tag, and weighted_mean lands in
   ``[0, 1]`` with stable content. Ship a full ``eval_run --out``
   round-trip on every set so a future pipeline regression surfaces
   immediately.

What this test **does not** assert
----------------------------------

It does **not** assert the R-11.x real-baseline score thresholds.
Those can only be checked with a live LLM run. The operator process
for that is documented in
``docs/admin-guide/evolution-runbook.md`` under "Scenario baseline
certification"; the recorded numbers go into
``docs/RELEASE_NOTES_agent_runtime_optimization_evolution_v1.md``.
Until those are captured, the R-11.x targets are tracked as a known
gap in the release notes.

Related
-------

* Committed seed files: ``data/eval_sets/v1/<set_name>.jsonl``
* CLI: ``server/scripts/eval_run.py`` (task 17.4)
* Scoring aggregator + primitive unit tests: ``test_scoring.py``
* Loader / stub-grader unit tests: ``test_eval_run_cli.py``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Make ``scripts.eval_run`` importable the same way the other
# ``tests/evaluation/`` files do.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_SEED_DIR = Path(__file__).resolve().parents[3] / "data" / "eval_sets" / "v1"


# Per-scenario certification context. Targets come from R-11.1 ~ R-11.5;
# expressed on the 0..1 scale that the scoring aggregator uses
# (the spec states the targets on a 0..10 scale).
_SCENARIOS: list[tuple[str, str, float]] = [
    ("knowledge_mgmt_v1", "scenario:knowledge_mgmt", 0.65),
    ("fault_triage_v1", "scenario:fault_triage", 0.60),
    ("incident_coord_v1", "scenario:incident_coord", 0.60),
    ("capacity_mgmt_v1", "scenario:capacity_mgmt", 0.55),
    ("runbook_mgmt_v1", "scenario:runbook_mgmt", 0.60),
]


@pytest.fixture(scope="module")
def seed_dir() -> Path:
    if not _SEED_DIR.exists():
        pytest.skip(f"no eval seed directory at {_SEED_DIR}")
    return _SEED_DIR


@pytest.mark.parametrize(
    "set_name,scenario_tag,baseline_target",
    _SCENARIOS,
    ids=[s[0] for s in _SCENARIOS],
)
def test_eval_harness_smoke_runs_per_set(
    seed_dir: Path,
    set_name: str,
    scenario_tag: str,
    baseline_target: float,
    tmp_path: Path,
) -> None:
    """End-to-end smoke: harness loads + grades + aggregates one set."""
    from scripts.eval_run import _run_items, _write_json, _load_items_from_jsonl
    from src.services.evaluation.scoring import aggregate

    jsonl = seed_dir / f"{set_name}.jsonl"
    assert jsonl.exists(), f"missing seed file {jsonl}"

    items = _load_items_from_jsonl(jsonl)
    # Each committed seed ships ≥ 16 items (8 positive + 8 negative);
    # see ``test_seed_jsonl_files_are_well_formed``.
    assert len(items) >= 16, (
        f"{set_name}.jsonl has {len(items)} items; expected ≥ 16"
    )
    # Every item must carry its scenario tag so the per-tag slice is
    # meaningful when sets are later merged into a single harness run.
    for rec in items:
        assert scenario_tag in rec.get("tags", []), (
            f"{set_name}.jsonl item {rec.get('item_id')} missing {scenario_tag}"
        )

    scored = _run_items(items, "stub")
    assert len(scored) == len(items)

    agg = aggregate(scored)
    assert agg.n_samples == len(items)
    assert 0.0 <= agg.weighted_mean <= 1.0
    # Stub grader returns 0.5 everywhere, so the weighted mean is
    # bit-exact 0.5 regardless of per-item weights.
    assert agg.weighted_mean == pytest.approx(0.5)
    # The scenario tag must own the whole set's weighted mean.
    assert scenario_tag in agg.per_tag
    assert agg.per_tag[scenario_tag] == pytest.approx(0.5)
    # The single rubric criterion from the stub grader must show up.
    assert agg.per_rubric == {"stub": pytest.approx(0.5)}

    # Real baseline targets (R-11.x) cannot be verified with a stub
    # grader; record the gap on the aggregate object to be explicit.
    assert baseline_target > agg.weighted_mean, (
        "stub grader produced a score at or above the real-baseline "
        "target — the harness smoke test is no longer a smoke test."
    )

    # Round-trip the JSON report so an operator's ``--out PATH``
    # pipeline is also exercised.
    out_path = tmp_path / f"{set_name}.report.json"
    _write_json(out_path, set_name, scored, agg)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["set_name"] == set_name
    assert payload["aggregate"]["n_samples"] == len(items)
    assert payload["aggregate"]["weighted_mean"] == pytest.approx(0.5)
    assert len(payload["items"]) == len(items)


def test_eval_harness_smoke_covers_all_five_sets(seed_dir: Path) -> None:
    """Every scenario named in R-11.1 ~ R-11.5 has a committed seed file."""
    committed = {p.stem for p in seed_dir.glob("*.jsonl")}
    expected = {s[0] for s in _SCENARIOS}
    missing = expected - committed
    assert not missing, f"missing seed files: {sorted(missing)}"


def test_eval_harness_smoke_documents_known_gap() -> None:
    """Release notes must acknowledge the R-11.x verification gap.

    The R-11.x per-scenario targets require a real LLM baseline run
    (task 22.2 grader) which is not part of PR CI. Until an operator
    captures those numbers using the runbook, the release notes must
    carry an explicit "known gap" entry so reviewers aren't surprised.
    """
    repo_root = Path(__file__).resolve().parents[3]
    release_notes = (
        repo_root
        / "docs"
        / "RELEASE_NOTES_agent_runtime_optimization_evolution_v1.md"
    )
    runbook = repo_root / "docs" / "admin-guide" / "evolution-runbook.md"

    assert release_notes.exists(), f"missing {release_notes}"
    assert runbook.exists(), f"missing {runbook}"

    text = release_notes.read_text(encoding="utf-8").lower()
    assert "r-11" in text, "release notes must reference R-11"
    assert "known gap" in text, (
        "release notes must explicitly declare the known gap"
    )

    runbook_text = runbook.read_text(encoding="utf-8").lower()
    # The runbook must tell operators how to run the real baseline.
    assert "eval_run.py" in runbook_text
    assert "baseline" in runbook_text
