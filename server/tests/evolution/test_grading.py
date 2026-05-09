"""Unit tests for task 22.2 — grading prompt harness.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 22.2
(Phase K — Evaluator).

**Validates: Requirements 3.6**

Covers :mod:`src.services.evolution.grading`:

* :func:`grade` with a scripted LLM returns the parsed score and
  per-rubric breakdown.
* Deterministic settings (``temperature=0``, ``seed=42``) are applied
  to the LLM prior to invocation.
* Cache hit path: a second call with identical ``(run, item,
  active_version)`` doesn't invoke the LLM.
* Cache key format matches
  ``eval:grade:{run_sha}:{item_id}:{active_version}``.
* Malformed LLM output is caught and surfaces as
  :class:`GradingResult` with ``score=0.0`` and a diagnostic rationale,
  never raising out of :func:`grade`.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.services.evolution.grading import (
    CACHE_KEY_PREFIX,
    GRADING_CACHE_TTL_SECONDS,
    GRADING_SEED,
    GRADING_TEMPERATURE,
    GradingResult,
    GradingRun,
    cache_key_for,
    compute_run_sha,
    grade,
)


# ---------------------------------------------------------------------------
# Test helpers — scripted LLM + in-memory Redis
# ---------------------------------------------------------------------------


@dataclass
class _Response:
    """Minimal stand-in for a LangChain chat response object."""

    content: str


@dataclass
class _ScriptedLLM:
    """LLM double that records every invocation and returns scripted JSON.

    Starts with deterministic-setting attributes **unset** so the tests
    can assert that :func:`grade` is the one that populates them (via
    ``_apply_deterministic_settings``).
    """

    response: str = ""
    calls: list[list[Any]] = field(default_factory=list)
    # Initialise to sentinels that are obviously wrong so we can verify
    # grade() overwrote them with the documented constants.
    temperature: float = 0.99
    seed: int = -1

    async def ainvoke(self, messages: list[Any]) -> _Response:
        # Snapshot the messages + current settings for later inspection.
        self.calls.append(list(messages))
        return _Response(content=self.response)


class _InMemoryRedis:
    """Tiny dict-backed Redis double.

    Only implements the narrow surface :func:`grade` uses: ``get`` and
    ``set(key, value, ex=...)``. Every call is logged so tests can
    assert hit/miss patterns via ``ops``.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ops: list[tuple[str, str]] = []  # (op_name, key)

    async def get(self, key: str) -> str | None:
        self.ops.append(("get", key))
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.ops.append(("set", key))
        self.store[key] = value
        self.last_ttl = ex


def _run(coro):
    """Drive an async test body without requiring asyncio marker."""
    return asyncio.run(coro)


def _make_item(
    *,
    item_id: str = "item-42",
    prompt: str = "check kafka lag",
    expected_tools: list[str] | None = None,
    expected_outcome: str = "answered",
    grading_rubric: Any = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "prompt": prompt,
        "expected_tools": expected_tools or [],
        "expected_outcome": expected_outcome,
        "grading_rubric": grading_rubric,
    }


def _make_run(
    *,
    output: str = "kafka lag is 0",
    tools_used: list[str] | None = None,
    outcome: str = "answered",
) -> GradingRun:
    return GradingRun(
        output=output, tools_used=tools_used or [], outcome=outcome
    )


# ---------------------------------------------------------------------------
# Constants are what the task requires
# ---------------------------------------------------------------------------


def test_exported_constants_match_spec():
    """Task 22.2 pins temperature=0, seed=42, TTL 24h."""
    assert GRADING_TEMPERATURE == 0.0
    assert GRADING_SEED == 42
    assert GRADING_CACHE_TTL_SECONDS == 86_400


# ---------------------------------------------------------------------------
# Cache-key derivation
# ---------------------------------------------------------------------------


def test_compute_run_sha_is_deterministic_and_depends_on_content():
    """Two identical runs hash identically; a change in any field differs."""
    a = GradingRun(output="hi", tools_used=["grep_kb"], outcome="answered")
    b = GradingRun(output="hi", tools_used=["grep_kb"], outcome="answered")
    c = GradingRun(output="hi", tools_used=["grep_kb"], outcome="delegated")
    d = GradingRun(output="hii", tools_used=["grep_kb"], outcome="answered")
    e = GradingRun(output="hi", tools_used=["search_logs"], outcome="answered")

    assert compute_run_sha(a) == compute_run_sha(b)
    assert compute_run_sha(a) != compute_run_sha(c)
    assert compute_run_sha(a) != compute_run_sha(d)
    assert compute_run_sha(a) != compute_run_sha(e)
    # Shape is a 64-char hex digest.
    assert len(compute_run_sha(a)) == 64


def test_cache_key_for_follows_documented_format():
    run = _make_run(output="out", tools_used=["t1", "t2"], outcome="answered")
    key = cache_key_for(run, item_id="i-1", active_version="v-123")
    expected_sha = compute_run_sha(run)
    assert key == f"{CACHE_KEY_PREFIX}{expected_sha}:i-1:v-123"


# ---------------------------------------------------------------------------
# Happy path — parse score + per_rubric
# ---------------------------------------------------------------------------


def test_grade_returns_parsed_score_and_per_rubric():
    llm_output = json.dumps(
        {
            "score": 0.82,
            "per_rubric": {"correctness": 0.9, "style": 0.7},
            "rationale": "Mostly correct with minor style issues.",
        }
    )
    llm = _ScriptedLLM(response=llm_output)
    redis = _InMemoryRedis()
    item = _make_item(
        grading_rubric=[
            {"name": "correctness", "description": "is it right?", "weight": 2.0},
            {"name": "style", "description": "is it concise?", "weight": 1.0},
        ]
    )
    run = _make_run(output="some answer", tools_used=["grep_kb"])

    result = _run(grade(run, item, llm=llm, redis=redis, active_version="v1"))

    assert isinstance(result, GradingResult)
    # Within tolerance of the declared 0.82 (weighted recomputed mean
    # would be (0.9*2 + 0.7*1) / 3 = 0.8333 — within the 0.05 tolerance
    # so the declared score is preserved).
    assert result.score == pytest.approx(0.82)
    assert result.per_rubric == {"correctness": 0.9, "style": 0.7}
    assert "style" in result.rationale.lower()
    # LLM was invoked exactly once.
    assert len(llm.calls) == 1


def test_grade_recomputes_weighted_mean_when_declared_score_differs():
    """A declared score way off from the weighted mean is overridden by
    the authoritative recompute. Prevents a hallucinated composite
    from disagreeing with its per-rubric components."""
    llm_output = json.dumps(
        {
            "score": 0.05,  # wildly inconsistent with per_rubric
            "per_rubric": {"correctness": 1.0, "style": 1.0},
            "rationale": "Everything looks great.",
        }
    )
    llm = _ScriptedLLM(response=llm_output)
    redis = _InMemoryRedis()
    item = _make_item(
        grading_rubric=[
            {"name": "correctness", "description": "d", "weight": 1.0},
            {"name": "style", "description": "d", "weight": 1.0},
        ]
    )

    result = _run(grade(_make_run(), item, llm=llm, redis=redis))

    # Authoritative composite from rubrics is 1.0; declared 0.05 is
    # discarded because it's outside the 0.05 tolerance band.
    assert result.score == pytest.approx(1.0)
    assert result.per_rubric == {"correctness": 1.0, "style": 1.0}


def test_grade_fills_missing_rubric_entries_with_zero():
    """If the LLM omits a rubric, grade still returns one entry per
    declared rubric so downstream consumers can rely on the shape."""
    llm_output = json.dumps(
        {
            "per_rubric": {"correctness": 0.8},  # 'style' missing
            "rationale": "ok",
        }
    )
    llm = _ScriptedLLM(response=llm_output)
    item = _make_item(
        grading_rubric=[
            {"name": "correctness", "description": "", "weight": 1.0},
            {"name": "style", "description": "", "weight": 1.0},
        ]
    )

    result = _run(grade(_make_run(), item, llm=llm, redis=_InMemoryRedis()))

    assert set(result.per_rubric.keys()) == {"correctness", "style"}
    assert result.per_rubric["correctness"] == pytest.approx(0.8)
    assert result.per_rubric["style"] == 0.0


def test_grade_clips_out_of_range_scores_to_unit_interval():
    llm_output = json.dumps(
        {
            "score": 1.5,
            "per_rubric": {"correctness": -0.2, "style": 2.5},
            "rationale": "noisy",
        }
    )
    llm = _ScriptedLLM(response=llm_output)
    item = _make_item(
        grading_rubric=[
            {"name": "correctness", "description": "", "weight": 1.0},
            {"name": "style", "description": "", "weight": 1.0},
        ]
    )

    result = _run(grade(_make_run(), item, llm=llm, redis=_InMemoryRedis()))

    assert 0.0 <= result.score <= 1.0
    for v in result.per_rubric.values():
        assert 0.0 <= v <= 1.0


def test_grade_accepts_plain_string_rubric_from_jsonl_items():
    """DB rows / JSONL loaders carry ``grading_rubric`` as a free-form
    string; the harness normalises that to a single ``overall`` rubric
    without raising."""
    llm_output = json.dumps(
        {
            "score": 0.6,
            "per_rubric": {"overall": 0.6},
            "rationale": "partial pass",
        }
    )
    llm = _ScriptedLLM(response=llm_output)
    item = _make_item(grading_rubric="Pass if agent used grep_kb.")

    result = _run(grade(_make_run(), item, llm=llm, redis=_InMemoryRedis()))

    assert result.score == pytest.approx(0.6)
    assert set(result.per_rubric.keys()) == {"overall"}


# ---------------------------------------------------------------------------
# Deterministic settings
# ---------------------------------------------------------------------------


def test_grade_applies_temperature_zero_and_seed_to_llm():
    """R-3.6 hinges on reproducible scoring — ``grade`` must pin the
    LLM to the documented deterministic settings before invocation."""
    llm = _ScriptedLLM(response=json.dumps({"score": 0.5, "per_rubric": {"overall": 0.5}, "rationale": ""}))
    item = _make_item(grading_rubric="rubric text")

    _run(grade(_make_run(), item, llm=llm, redis=_InMemoryRedis()))

    # Asserted against the module constants, not hard-coded values, so
    # changing the constants (which would be a spec change) shows up
    # as a diff here.
    assert llm.temperature == GRADING_TEMPERATURE
    assert llm.seed == GRADING_SEED


# ---------------------------------------------------------------------------
# Cache hit / miss paths
# ---------------------------------------------------------------------------


def test_grade_cache_hit_skips_llm_invocation():
    """Second call with identical (run, item, active_version) is served
    from cache — LLM is invoked exactly once across two calls."""
    llm_output = json.dumps(
        {
            "score": 0.75,
            "per_rubric": {"overall": 0.75},
            "rationale": "ok",
        }
    )
    llm = _ScriptedLLM(response=llm_output)
    redis = _InMemoryRedis()
    item = _make_item(grading_rubric="rubric")
    run = _make_run()

    r1 = _run(grade(run, item, llm=llm, redis=redis, active_version="v1"))
    r2 = _run(grade(run, item, llm=llm, redis=redis, active_version="v1"))

    assert r1.score == pytest.approx(r2.score)
    assert r1.per_rubric == r2.per_rubric
    # LLM called once — second call was a cache hit.
    assert len(llm.calls) == 1


def test_grade_cache_miss_then_hit_uses_documented_key_format():
    """After a miss+write, the stored key is exactly
    ``eval:grade:{run_sha}:{item_id}:{active_version}``."""
    llm = _ScriptedLLM(
        response=json.dumps(
            {"score": 0.5, "per_rubric": {"overall": 0.5}, "rationale": ""}
        )
    )
    redis = _InMemoryRedis()
    run = _make_run(output="hi", tools_used=["t1"], outcome="answered")
    item = _make_item(item_id="i-99", grading_rubric="rubric")

    _run(grade(run, item, llm=llm, redis=redis, active_version="v-xyz"))

    expected_key = cache_key_for(run, "i-99", "v-xyz")
    assert expected_key in redis.store

    # Human-readable sanity — prefix + three colon segments.
    assert expected_key.startswith(CACHE_KEY_PREFIX)
    payload = redis.store[expected_key]
    parsed = json.loads(payload)
    assert parsed["per_rubric"] == {"overall": 0.5}
    # TTL set via ``ex=`` on the set call.
    assert redis.last_ttl == GRADING_CACHE_TTL_SECONDS


def test_grade_cache_key_differentiates_on_active_version():
    """Same run + item but different active_version must not collide
    in the cache — baseline runs serve different active prompts."""
    llm = _ScriptedLLM(
        response=json.dumps(
            {"score": 0.5, "per_rubric": {"overall": 0.5}, "rationale": ""}
        )
    )
    redis = _InMemoryRedis()
    run = _make_run()
    item = _make_item(grading_rubric="rubric")

    _run(grade(run, item, llm=llm, redis=redis, active_version="v1"))
    _run(grade(run, item, llm=llm, redis=redis, active_version="v2"))

    # Two distinct cache entries — second call was a miss, LLM called
    # twice in total.
    assert len(llm.calls) == 2
    keys = [op[1] for op in redis.ops if op[0] == "set"]
    assert len(set(keys)) == 2


# ---------------------------------------------------------------------------
# Malformed LLM output
# ---------------------------------------------------------------------------


def test_grade_malformed_output_returns_zero_with_diagnostic_rationale():
    """Non-JSON content → GradingResult(score=0.0, rationale=diagnostic)."""
    llm = _ScriptedLLM(response="I am very confident: the answer is ~0.8.")
    item = _make_item(grading_rubric="rubric")

    result = _run(grade(_make_run(), item, llm=llm, redis=_InMemoryRedis()))

    assert isinstance(result, GradingResult)
    assert result.score == 0.0
    assert "grading_error" in result.rationale


def test_grade_llm_invoke_raises_returns_zero_result():
    class _RaisingLLM:
        temperature = 0.0
        seed = 0

        async def ainvoke(self, messages: list[Any]) -> Any:
            raise RuntimeError("model timed out")

    item = _make_item(grading_rubric="rubric")
    result = _run(grade(_make_run(), item, llm=_RaisingLLM(), redis=_InMemoryRedis()))

    assert result.score == 0.0
    assert "grading_error" in result.rationale


def test_grade_tolerates_fenced_code_block_output():
    """Some providers wrap JSON in ``` even when asked not to.

    The parser strips the fence and grades normally so a provider
    quirk doesn't cost us a malformed-output penalty.
    """
    body = json.dumps(
        {"score": 0.4, "per_rubric": {"overall": 0.4}, "rationale": "partial"}
    )
    llm = _ScriptedLLM(response=f"```json\n{body}\n```")
    item = _make_item(grading_rubric="rubric")

    result = _run(grade(_make_run(), item, llm=llm, redis=_InMemoryRedis()))

    assert result.score == pytest.approx(0.4)
    assert result.per_rubric == {"overall": 0.4}


# ---------------------------------------------------------------------------
# Redis failure silently degrades
# ---------------------------------------------------------------------------


def test_grade_redis_failure_falls_back_to_llm():
    """A broken Redis must not block grading — the cache layer is
    advisory, not a correctness surface."""

    class _BrokenRedis:
        async def get(self, key: str) -> str | None:
            raise RuntimeError("redis down")

        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            raise RuntimeError("redis down")

    llm = _ScriptedLLM(
        response=json.dumps(
            {"score": 0.3, "per_rubric": {"overall": 0.3}, "rationale": "noisy"}
        )
    )
    item = _make_item(grading_rubric="rubric")

    result = _run(grade(_make_run(), item, llm=llm, redis=_BrokenRedis()))

    # LLM was called because cache was unreachable; result is still valid.
    assert len(llm.calls) == 1
    assert result.score == pytest.approx(0.3)
