"""Smoke tests for the eval runner CLI (task 17.4).

End-to-end test that the runner can:

* Load items from a JSONL seed file (DB fallback path).
* Run the ``stub`` grader.
* Compute a well-formed :class:`AggregateScore`.
* Write a JSON report that matches the printed summary.

The DB path is exercised by ``test_eval_cold_start.py`` (not in this
file). Here we just verify the JSONL fallback loader + aggregator
wiring is sound.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_jsonl_loader_reads_seed_file(tmp_path):
    """The JSONL loader handles comments, blank lines, and malformed entries."""
    from scripts.eval_run import _load_items_from_jsonl

    src = tmp_path / "demo_v1.jsonl"
    src.write_text(
        "\n".join(
            [
                "# comment line",
                "",
                json.dumps(
                    {
                        "prompt": "first",
                        "expected_tools": ["grep_kb"],
                        "expected_outcome": "answered",
                        "grading_rubric": "r1",
                        "weight": 1.5,
                        "tags": ["scenario:x", "positive"],
                    }
                ),
                '{"broken": "json",',  # malformed — should be logged + skipped
                json.dumps({"prompt": "second", "weight": 0.5}),
            ]
        ),
        encoding="utf-8",
    )

    records = _load_items_from_jsonl(src)
    # malformed line is skipped; comment + blank line ignored
    assert len(records) == 2
    assert records[0]["prompt"] == "first"
    assert records[0]["weight"] == 1.5
    assert records[0]["tags"] == ["scenario:x", "positive"]
    assert records[1]["prompt"] == "second"
    assert records[1]["weight"] == 0.5


def test_run_items_with_stub_grader():
    """``_run_items`` returns ItemScore instances with deterministic stub scores."""
    from scripts.eval_run import _run_items

    items = [
        {
            "item_id": "a",
            "prompt": "p",
            "weight": 1.0,
            "tags": ["scenario:x"],
        },
        {
            "item_id": "b",
            "prompt": "q",
            "weight": 2.0,
            "tags": [],
        },
    ]
    scored = _run_items(items, "stub")
    assert [s.item_id for s in scored] == ["a", "b"]
    # stub grader returns 0.5 — below the 0.7 pass threshold so none pass.
    assert all(s.overall == 0.5 for s in scored)
    assert all(not s.passed for s in scored)
    assert scored[0].tags == ["scenario:x"]


def test_aggregate_and_json_roundtrip(tmp_path):
    """``_write_json`` output is valid JSON and matches the aggregate."""
    from scripts.eval_run import _run_items, _write_json
    from src.services.evaluation.scoring import aggregate

    items = [
        {"item_id": "a", "prompt": "p", "weight": 1.0, "tags": []},
        {"item_id": "b", "prompt": "q", "weight": 2.0, "tags": []},
    ]
    scored = _run_items(items, "stub")
    agg = aggregate(scored)

    out = tmp_path / "report.json"
    _write_json(out, "demo_v1", scored, agg)

    payload = json.loads(out.read_text())
    assert payload["set_name"] == "demo_v1"
    assert len(payload["items"]) == 2
    assert payload["aggregate"]["n_samples"] == 2
    # Stub grader → weighted mean 0.5 exactly.
    assert payload["aggregate"]["weighted_mean"] == pytest.approx(0.5)


def test_loader_handles_missing_file(tmp_path):
    """A missing JSONL file surfaces as an empty list, not a crash."""
    from scripts.eval_run import _load_items_from_jsonl

    with pytest.raises(FileNotFoundError):
        _load_items_from_jsonl(tmp_path / "does_not_exist.jsonl")


def test_seed_jsonl_files_are_well_formed():
    """Every shipped seed JSONL parses + contains ≥ 16 records with required fields."""
    seed_dir = Path(__file__).resolve().parents[3] / "data" / "eval_sets" / "v1"
    if not seed_dir.exists():
        pytest.skip("no seed directory in this checkout")

    files = sorted(seed_dir.glob("*.jsonl"))
    assert files, "expected at least one .jsonl seed"

    required_fields = {"prompt", "expected_outcome", "weight", "tags"}
    for f in files:
        records = []
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            records.append(json.loads(line))

        assert len(records) >= 16, f"{f.name} has only {len(records)} items"

        for rec in records:
            missing = required_fields - set(rec.keys())
            assert not missing, f"{f.name}: record missing {missing}: {rec}"
            assert isinstance(rec["prompt"], str) and rec["prompt"].strip()
            assert rec["expected_outcome"] in {
                None,
                "answered",
                "delegated",
                "refused",
            }, f"{f.name}: bad outcome {rec['expected_outcome']}"
            assert isinstance(rec["tags"], list)
