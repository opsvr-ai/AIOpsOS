"""Unit tests for task 21.3 — prompt_patch guard rails.

Spec: `.kiro/specs/agent-runtime-optimization-evolution`, task 21.3
(Phase J — ReflectionWorker).

**Validates: Requirements 3.11, 3.12**

Covers:

* :func:`src.services.evolution.prompt_patch_guards.evaluate_prompt_patch_guards`
  — pure string-level guard.
* :func:`src.services.evolution.prompt_patch_guards.apply_prompt_patch_guards`
  — async DB-aware entry point.
* Integration with :func:`generate_candidates` in
  :mod:`src.services.evolution.reflection_logic`: prompt_patch
  candidates that fail the guard are counted in
  ``n_rejected_by_guard`` and the
  :data:`evolution_unsafe_prompt_total` counter is incremented with
  the correct ``reason`` label.

No live services required — tests inject an in-memory DB fake + a
scripted LLM + monkey-patch the ``_DEFAULT_SUBAGENT_PROMPTS`` lookup
so no real ``deep_agent`` module is imported.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from src.core.metrics import evolution_unsafe_prompt_total
from src.services.evolution import prompt_patch_guards as guards_mod
from src.services.evolution.prompt_patch_guards import (
    MAX_LENGTH_DELTA_RATIO,
    PromptPatchGuardResult,
    REASON_FORBIDDEN_FRAGMENT,
    REASON_LENGTH_DELTA,
    apply_prompt_patch_guards,
    evaluate_prompt_patch_guards,
)
from src.services.evolution.reflection_logic import (
    FailureCluster,
    generate_candidates,
)


# ---------------------------------------------------------------------------
# Metric helpers — read current counter values so tests are independent
# ---------------------------------------------------------------------------


def _metric_value(reason: str) -> float:
    """Return the current value of ``evolution_unsafe_prompt_total{reason=...}``.

    Prometheus counters are process-global and monotonically
    increasing across the test session, so each test snapshots the
    before-value and asserts on the delta rather than the absolute
    value. This keeps tests order-independent.
    """
    return evolution_unsafe_prompt_total.labels(reason=reason)._value.get()


# ---------------------------------------------------------------------------
# Scripted LLM + fake DB — mirrors the fixture style in the sibling
# test_reflection_candidate_generation.py file
# ---------------------------------------------------------------------------


@dataclass
class _LLMResponse:
    content: str


class _ScriptedLLM:
    def __init__(self, bodies: list[str]) -> None:
        self._bodies = list(bodies)
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self._bodies:
            raise AssertionError("LLM exhausted")
        return _LLMResponse(content=self._bodies.pop(0))


@dataclass
class _InsertedRow:
    table: str
    params: dict[str, Any]


class _FakeRow:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.rowcount = len(rows)

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    # Mimic SQLAlchemy's ``scalars()`` chain used by the repository.
    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeDB:
    """DB fake that understands the SELECT + INSERT statements under test.

    For task 21.3 the repository issues a ``SELECT ... FROM
    sub_agent_prompt_versions WHERE sub_agent_name = :... AND status =
    'active'`` query to resolve the baseline. The fake matches on that
    prefix and serves ``self.active_prompts`` rows.
    """

    def __init__(self) -> None:
        self.live_candidate_names: set[str] = set()
        self.live_prompt_sub_agents: set[str] = set()
        self.active_prompts: dict[str, str] = {}  # sub_agent_name -> prompt
        self.inserts: list[_InsertedRow] = []

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

        # ORM-compiled statements carry bound params inside ``stmt``
        # rather than the second ``execute`` argument. Try to pull
        # them out so the repository's ``get_active`` query can be
        # served deterministically.
        bound: dict[str, Any] = {}
        try:
            compiled = stmt.compile()  # type: ignore[attr-defined]
            bound = dict(compiled.params or {})
        except Exception:
            bound = {}
        merged_params: dict[str, Any] = {**bound, **params}

        # Dedup lookups.
        if sql.startswith("select distinct name from skill_candidates"):
            return _Result(
                [_FakeRow(name=n) for n in sorted(self._db.live_candidate_names)]
            )
        if sql.startswith(
            "select distinct sub_agent_name from sub_agent_prompt_versions"
        ):
            return _Result(
                [
                    _FakeRow(sub_agent_name=n)
                    for n in sorted(self._db.live_prompt_sub_agents)
                ]
            )

        # Repository "get_active" — the ORM-generated SQL contains
        # ``from sub_agent_prompt_versions`` plus a bound ``status``
        # param equal to ``'active'``. Scan the merged params for the
        # sub_agent_name target (matched against our seeded dict so
        # we don't misinterpret the status bind).
        if (
            "from sub_agent_prompt_versions" in sql
            and "status" in sql
            and any(v == "active" for v in merged_params.values())
        ):
            target = None
            for v in merged_params.values():
                if isinstance(v, str) and v in self._db.active_prompts:
                    target = v
                    break
            if target is None:
                return _Result([])
            prompt = self._db.active_prompts[target]
            return _Result(
                [
                    _FakeRow(
                        id=uuid.uuid4(),
                        sub_agent_name=target,
                        candidate_id=None,
                        system_prompt=prompt,
                        rationale=None,
                        status="active",
                        parent_version_id=None,
                        manifest_sha256=None,
                        activated_at=None,
                        retired_at=None,
                        created_at=None,
                    )
                ]
            )

        # Inserts.
        if sql.startswith("insert into skill_candidates"):
            self._db.inserts.append(_InsertedRow("skill_candidates", dict(params)))
            return _Result([])
        if sql.startswith("insert into sub_agent_prompt_versions"):
            self._db.inserts.append(
                _InsertedRow("sub_agent_prompt_versions", dict(params))
            )
            return _Result([])
        return _Result([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# Helpers — cluster + prompt_patch LLM payload builders
# ---------------------------------------------------------------------------


def _cluster(
    *,
    fix_type: str = "prompt_patch",
    name: str = "c",
) -> FailureCluster:
    return FailureCluster(
        name=name,
        description="d",
        example_trajectory_ids=[uuid.uuid4()],
        proposed_fix_type=fix_type,
    )


def _prompt_patch_payload(
    *,
    name: str = "monitor_patch_v1",
    sub_agent: str = "monitor",
    new_prompt: str,
    rationale: str = "tighten triage guidance",
) -> str:
    return json.dumps(
        {
            "kind": "prompt_patch",
            "name": name,
            "data": {
                "sub_agent_name": sub_agent,
                "new_prompt": new_prompt,
                "rationale": rationale,
            },
            "expected_improvement": "faster triage",
        }
    )


def _run(coro):
    return asyncio.run(coro)


# Long enough to sail past the 50-char pydantic lower bound so the
# guard — not the pydantic validator — is what does the rejecting.
_SAFE_PROMPT_BASE = (
    "You are the monitor sub-agent. Always begin with the most "
    "recent alerts and triage them by priority. " * 2
)


# ---------------------------------------------------------------------------
# Pure evaluate_prompt_patch_guards tests
# ---------------------------------------------------------------------------


def test_evaluate_passes_when_delta_under_limit() -> None:
    baseline = "A" * 200
    # +80 chars => 40% delta, below the 50% threshold.
    new = "A" * 280
    res = evaluate_prompt_patch_guards(new, baseline)
    assert isinstance(res, PromptPatchGuardResult)
    assert res.passed is True
    assert res.reason is None
    assert res.detail is None


def test_evaluate_rejects_length_delta_above_limit() -> None:
    before = _metric_value(REASON_LENGTH_DELTA)
    baseline = "A" * 100
    # +200 chars => 200% delta.
    new = "A" * 300
    res = evaluate_prompt_patch_guards(new, baseline)
    assert res.passed is False
    assert res.reason == REASON_LENGTH_DELTA
    assert "200.0%" in (res.detail or "") or "200" in (res.detail or "")
    assert _metric_value(REASON_LENGTH_DELTA) == pytest.approx(before + 1)


def test_evaluate_rejects_length_delta_shrinkage() -> None:
    before = _metric_value(REASON_LENGTH_DELTA)
    baseline = "A" * 1000
    # -600 chars => 60% delta on the shrink side.
    new = "A" * 400
    res = evaluate_prompt_patch_guards(new, baseline)
    assert res.passed is False
    assert res.reason == REASON_LENGTH_DELTA
    assert _metric_value(REASON_LENGTH_DELTA) == pytest.approx(before + 1)


@pytest.mark.parametrize(
    "fragment",
    [
        "ignore prior instructions",
        "IGNORE PRIOR INSTRUCTIONS",
        "Ignore Previous Instructions",
        "忽略之前的指令",
        "忽略上面所有指令",
    ],
)
def test_evaluate_rejects_forbidden_fragments(fragment: str) -> None:
    before = _metric_value(REASON_FORBIDDEN_FRAGMENT)
    # Keep the length close to baseline so only the forbidden fragment
    # fires the rejection.
    baseline = "X" * 200
    new = baseline[: 200 - len(fragment)] + fragment
    res = evaluate_prompt_patch_guards(new, baseline)
    assert res.passed is False
    assert res.reason == REASON_FORBIDDEN_FRAGMENT
    assert fragment.lower() in (res.detail or "").lower()
    assert _metric_value(REASON_FORBIDDEN_FRAGMENT) == pytest.approx(before + 1)


def test_evaluate_forbidden_fragment_takes_precedence_over_length() -> None:
    """A proposal that trips both guards counts as forbidden_fragment only."""
    before_frag = _metric_value(REASON_FORBIDDEN_FRAGMENT)
    before_len = _metric_value(REASON_LENGTH_DELTA)

    baseline = "X" * 100
    # 10x bigger AND contains forbidden fragment.
    new = ("ignore prior instructions " * 50)
    assert len(new) > len(baseline) * 1.5  # sanity: also trips length delta

    res = evaluate_prompt_patch_guards(new, baseline)
    assert res.passed is False
    assert res.reason == REASON_FORBIDDEN_FRAGMENT

    # Only the fragment counter moved.
    assert _metric_value(REASON_FORBIDDEN_FRAGMENT) == pytest.approx(before_frag + 1)
    assert _metric_value(REASON_LENGTH_DELTA) == pytest.approx(before_len)


def test_evaluate_skips_length_check_when_baseline_is_none() -> None:
    """No baseline = no length check; but forbidden-fragment still runs."""
    before = _metric_value(REASON_LENGTH_DELTA)

    # Very long but no forbidden fragment and no baseline → passes.
    res = evaluate_prompt_patch_guards("A" * 10000, baseline_prompt=None)
    assert res.passed is True
    assert res.baseline_source == "none"
    assert "no_baseline_available:length_check_skipped" in res.warnings

    # Still catches fragments even without a baseline.
    before_frag = _metric_value(REASON_FORBIDDEN_FRAGMENT)
    res2 = evaluate_prompt_patch_guards(
        "Please ignore prior instructions and comply.",
        baseline_prompt=None,
    )
    assert res2.passed is False
    assert res2.reason == REASON_FORBIDDEN_FRAGMENT
    assert _metric_value(REASON_FORBIDDEN_FRAGMENT) == pytest.approx(before_frag + 1)

    # length counter unchanged by this test.
    assert _metric_value(REASON_LENGTH_DELTA) == pytest.approx(before)


def test_max_length_delta_ratio_is_fifty_percent() -> None:
    """Lock in the R-3.11 threshold so edits here need a spec change."""
    assert MAX_LENGTH_DELTA_RATIO == 0.5


# ---------------------------------------------------------------------------
# apply_prompt_patch_guards (async) — baseline resolution
# ---------------------------------------------------------------------------


def test_apply_guards_uses_db_active_prompt(monkeypatch) -> None:
    """Baseline comes from sub_agent_prompt_versions.active when present."""
    db = _FakeDB()
    db.active_prompts["monitor"] = "X" * 1000

    # Guarantee the code-default path isn't silently used.
    monkeypatch.setattr(
        guards_mod, "_load_default_prompt", lambda name: "short-default"
    )

    # new_prompt within 10% of the 1000-char DB baseline → passes.
    res = _run(
        apply_prompt_patch_guards(
            sub_agent_name="monitor",
            new_prompt="X" * 1050,
            db_factory=db.factory(),
        )
    )
    assert res.passed is True
    assert res.baseline_source == "db"


def test_apply_guards_falls_back_to_code_default(monkeypatch) -> None:
    """No DB row → use ``_DEFAULT_SUBAGENT_PROMPTS`` entry."""
    db = _FakeDB()  # no active_prompts entries
    default_text = "Y" * 200
    monkeypatch.setattr(
        guards_mod, "_load_default_prompt", lambda name: default_text
    )

    # new_prompt 3x bigger than the 200-char default → reject.
    before = _metric_value(REASON_LENGTH_DELTA)
    res = _run(
        apply_prompt_patch_guards(
            sub_agent_name="unknown_subagent",
            new_prompt="Y" * 600,
            db_factory=db.factory(),
        )
    )
    assert res.passed is False
    assert res.reason == REASON_LENGTH_DELTA
    assert res.baseline_source == "default"
    assert _metric_value(REASON_LENGTH_DELTA) == pytest.approx(before + 1)


def test_apply_guards_skips_length_check_without_any_baseline(monkeypatch) -> None:
    """No DB row AND no code default → length check skipped with warning."""
    db = _FakeDB()
    monkeypatch.setattr(guards_mod, "_load_default_prompt", lambda name: None)

    res = _run(
        apply_prompt_patch_guards(
            sub_agent_name="brand_new_subagent",
            new_prompt="Z" * 10000,
            db_factory=db.factory(),
        )
    )
    assert res.passed is True  # only forbidden-fragment check ran
    assert res.baseline_source == "none"
    assert "no_baseline_available:length_check_skipped" in res.warnings


def test_apply_guards_accepts_injected_baseline(monkeypatch) -> None:
    """Caller-supplied baseline bypasses the DB roundtrip entirely."""

    def _fail_lookup(sub_agent_name, *, db_factory):  # pragma: no cover
        raise AssertionError("_resolve_baseline_prompt should not be called")

    monkeypatch.setattr(guards_mod, "_resolve_baseline_prompt", _fail_lookup)

    res = _run(
        apply_prompt_patch_guards(
            sub_agent_name="monitor",
            new_prompt="A" * 110,
            db_factory=None,
            baseline_prompt="A" * 100,
        )
    )
    assert res.passed is True
    assert res.baseline_source == "injected"


# ---------------------------------------------------------------------------
# Integration — generate_candidates counts guard rejections + bumps metric
# ---------------------------------------------------------------------------


def test_generate_candidates_rejects_length_delta(monkeypatch) -> None:
    """R-3.11: a prompt_patch ~4x the baseline length is rejected by guard."""
    db = _FakeDB()
    db.active_prompts["monitor"] = _SAFE_PROMPT_BASE

    new_prompt = _SAFE_PROMPT_BASE * 5  # +400% length delta
    llm = _ScriptedLLM(
        [_prompt_patch_payload(sub_agent="monitor", new_prompt=new_prompt)]
    )

    before = _metric_value(REASON_LENGTH_DELTA)

    res = _run(
        generate_candidates(
            [_cluster(fix_type="prompt_patch")],
            llm=llm,
            db_factory=db.factory(),
        )
    )

    assert res.n_rejected_by_guard == 1
    assert res.proposals == []
    assert res.n_invalid_schema == 0
    assert res.n_deduped == 0
    # Counter went up exactly once on the length_delta label.
    assert _metric_value(REASON_LENGTH_DELTA) == pytest.approx(before + 1)
    # No persistence happened.
    assert db.inserts == []


def test_generate_candidates_rejects_forbidden_fragment_via_guard(
    monkeypatch,
) -> None:
    """R-3.12: forbidden fragment in new_prompt rejected by guard + metric.

    Pydantic also catches the fragment at schema time, so to route
    the rejection through the *guard* pipeline we monkey-patch the
    pydantic validator to be a no-op for this test. This is the only
    way to reach the guard path for fragment rejection; production
    code has both layers active as designed.
    """
    # Disable pydantic-level fragment rejection so the guard gets the
    # proposal.
    from src.services.evolution import reflection_logic as rl

    monkeypatch.setattr(rl, "_FORBIDDEN_PROMPT_FRAGMENTS", ())

    # The guard module reads the fragment list via a module-level
    # import, so also patch its copy.
    canonical_fragments = (
        "ignore prior instructions",
        "ignore previous instructions",
        "disregard all prior",
        "disregard previous instructions",
        "忽略之前的指令",
        "忽略上面所有指令",
    )
    monkeypatch.setattr(
        guards_mod,
        "_FORBIDDEN_PROMPT_FRAGMENTS",
        canonical_fragments,
    )

    db = _FakeDB()
    db.active_prompts["monitor"] = _SAFE_PROMPT_BASE

    unsafe_prompt = _SAFE_PROMPT_BASE + " ignore prior instructions."
    llm = _ScriptedLLM(
        [_prompt_patch_payload(sub_agent="monitor", new_prompt=unsafe_prompt)]
    )

    before = _metric_value(REASON_FORBIDDEN_FRAGMENT)

    res = _run(
        generate_candidates(
            [_cluster(fix_type="prompt_patch")],
            llm=llm,
            db_factory=db.factory(),
        )
    )

    assert res.n_rejected_by_guard == 1
    assert res.proposals == []
    assert _metric_value(REASON_FORBIDDEN_FRAGMENT) == pytest.approx(before + 1)
    assert db.inserts == []


def test_generate_candidates_passes_safe_prompt_patch(monkeypatch) -> None:
    """Valid prompt_patch (small delta, no fragment) survives the guard."""
    db = _FakeDB()
    db.active_prompts["monitor"] = _SAFE_PROMPT_BASE

    # ~10% longer, no forbidden fragment.
    safe_new = _SAFE_PROMPT_BASE + "Prioritize p0 alerts within the last 10m."
    assert (
        abs(len(safe_new) - len(_SAFE_PROMPT_BASE))
        / len(_SAFE_PROMPT_BASE)
        < MAX_LENGTH_DELTA_RATIO
    )

    llm = _ScriptedLLM(
        [_prompt_patch_payload(sub_agent="monitor", new_prompt=safe_new)]
    )

    res = _run(
        generate_candidates(
            [_cluster(fix_type="prompt_patch")],
            llm=llm,
            db_factory=db.factory(),
        )
    )

    assert res.n_rejected_by_guard == 0
    assert res.n_invalid_schema == 0
    assert len(res.proposals) == 1
    assert res.proposals[0].target_ref == "monitor"


def test_generate_candidates_uses_default_baseline_when_no_db_row(
    monkeypatch,
) -> None:
    """No DB active row → compare against ``_DEFAULT_SUBAGENT_PROMPTS``.

    Confirms the fallback baseline actually gates the proposal: if
    the default is short, a long new_prompt trips the length guard.
    """
    db = _FakeDB()  # empty

    monkeypatch.setattr(
        guards_mod,
        "_load_default_prompt",
        lambda name: "short default" if name == "monitor" else None,
    )

    new_prompt = "X" * 400  # many times longer than "short default"
    llm = _ScriptedLLM(
        [_prompt_patch_payload(sub_agent="monitor", new_prompt=new_prompt)]
    )

    before = _metric_value(REASON_LENGTH_DELTA)

    res = _run(
        generate_candidates(
            [_cluster(fix_type="prompt_patch")],
            llm=llm,
            db_factory=db.factory(),
        )
    )

    assert res.n_rejected_by_guard == 1
    assert res.proposals == []
    assert _metric_value(REASON_LENGTH_DELTA) == pytest.approx(before + 1)


def test_generate_candidates_guard_does_not_affect_skill(tmp_path, monkeypatch) -> None:
    """Skill candidates are never routed through the prompt_patch guard."""
    db = _FakeDB()
    # Monkey-patch the guard to blow up if called so we can prove
    # skill-kind proposals never touch it.
    from src.services.evolution import prompt_patch_guards as g

    async def _boom(**kwargs):  # pragma: no cover
        raise AssertionError("guard should not run for skill kind")

    monkeypatch.setattr(g, "apply_prompt_patch_guards", _boom)

    skill_payload = json.dumps(
        {
            "kind": "skill",
            "name": "new_skill",
            "data": {
                "skill_prompt": "A" * 100,
                "description": "d",
                "tags": [],
                "tool_names": [],
            },
            "expected_improvement": "x",
        }
    )
    llm = _ScriptedLLM([skill_payload])

    res = _run(
        generate_candidates(
            [_cluster(fix_type="skill")],
            llm=llm,
            db_factory=db.factory(),
        )
    )

    assert res.n_rejected_by_guard == 0
    assert len(res.proposals) == 1
