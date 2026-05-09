"""IT dry-run for task 21.7 — end-to-end reflection cycle.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 21.7
(Phase J — ReflectionWorker). Covers acceptance criteria:

**Validates: Requirements 3.1, 3.3**

* **R-3.1** — ``ReflectionWorker`` pulls failing trajectories
  (``outcome in ('error','timeout')``) and LLM-clusters them into
  named groups with example trajectory ids.
* **R-3.3** — skill candidates are persisted to
  ``skill_candidates(status='proposed')`` with the rendered SKILL.md
  landing under ``<skills_root>/.candidate/<name>/`` — never in the
  main ``data/skills/`` tree.

Why this is an "IT-style" test that doesn't touch real services:
the integration surface under test is
:func:`src.services.evolution.reflection_logic.run_reflection_full_cycle`
— the orchestration seam that binds cluster_failures + generate_candidates
+ SkillCandidateStore together. Integration there means "all three stages
cooperate end-to-end through the same injected factory / LLM". Bringing
up a real Postgres + Redis + Kafka stack tests the infrastructure, not
the cycle's contract, and the bugfix pattern in sibling tests
(``test_reflection_candidate_generation.py``,
``test_candidate_store.py``) is to inject a fake DB that understands the
exact SQL statements each layer emits. This file follows the same
pattern so it can run in any environment (CI, dev laptop, no docker).

Setup:
  * Inject 10 failing ``tool_call`` trajectories into an in-memory fake
    DB (all within the 24h window, ``kind='tool_call'``,
    ``outcome='error'`` or ``'timeout'``).
  * Script the LLM to return, in order:
      1. a valid ``CLUSTER_FAILURES_PROMPT`` response producing one
         cluster with ``proposed_fix_type='skill'``,
      2. a valid ``CANDIDATE_GEN_PROMPT`` response producing one
         ``kind='skill'`` candidate.
  * Call ``run_reflection_full_cycle(persist=True, skills_root_dir=tmp_path)``.

Assertions:
  * ``result.candidates.proposals`` has >= 1 entry.
  * ``result.candidates.persisted`` has >= 1 entry with
    ``table='skill_candidates'``.
  * The candidate SKILL.md file exists at
    ``<tmp_path>/.candidate/<name>/SKILL.md``.
  * The INSERT SQL issued against ``skill_candidates`` hard-codes
    ``status='proposed'`` — verified by capturing the statement.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.services.evolution.reflection_logic import (
    CANDIDATE_GEN_PROMPT,
    CLUSTER_FAILURES_PROMPT,
    run_reflection_full_cycle,
)


# ---------------------------------------------------------------------------
# Fake DB — unified: trajectory SELECTs + candidate_store INSERTs
# ---------------------------------------------------------------------------


@dataclass
class _Trajectory:
    """In-memory stand-in for one ``agent_trajectories`` row."""

    id: uuid.UUID
    session_id: uuid.UUID
    kind: str
    outcome: str
    created_at: datetime
    data: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass
class _InsertedRow:
    """Captures one INSERT for assertion in the test body."""

    table: str
    sql: str
    params: dict[str, Any]


class _Row:
    """SQLAlchemy-Row stand-in that exposes attribute access."""

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


class _FakeDB:
    """In-memory store covering every SQL statement the cycle emits.

    The cycle hits three categories of SQL:

    1. Trajectory pulls (``agent_trajectories``) — source data.
    2. Dedup probes (``skill_candidates``, ``sub_agent_prompt_versions``).
    3. Persist INSERTs (``skill_candidates``).

    Pattern-matching on the SQL prefix lets the fake stay compact;
    we only special-case the prefixes the reflection pipeline
    actually produces.
    """

    def __init__(self) -> None:
        self.trajectories: list[_Trajectory] = []
        self.inserts: list[_InsertedRow] = []
        self.live_candidate_names: set[str] = set()
        self.live_prompt_sub_agents: set[str] = set()

    def add_trajectory(self, traj: _Trajectory) -> None:
        self.trajectories.append(traj)

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
        # Normalise SQL to a single-line lowercase string. Whitespace is
        # collapsed so the prefix match is stable across editor
        # reformatting of the source queries.
        sql_raw = str(stmt)
        sql = " ".join(sql_raw.split()).lower()
        params = params or {}

        # ---- trajectory pulls (reflection_logic._load_*) --------------
        if sql.startswith("select id, session_id, kind, outcome, created_at, data, tags from agent_trajectories"):
            if "where session_id = :sid" in sql:
                return self._per_session_failures(params)
            # default: tool_call pull
            return self._tool_call_failures(params)
        if sql.startswith("select session_id, count(*)"):
            return self._grouped_session_counts(params)

        # ---- dedup probes (candidate generation) ----------------------
        if sql.startswith("select distinct name from skill_candidates"):
            return _Result(
                [_Row(name=n) for n in sorted(self._db.live_candidate_names)]
            )
        if sql.startswith(
            "select distinct sub_agent_name from sub_agent_prompt_versions"
        ):
            return _Result(
                [
                    _Row(sub_agent_name=n)
                    for n in sorted(self._db.live_prompt_sub_agents)
                ]
            )

        # ---- candidate_store INSERTs ----------------------------------
        if sql.startswith("insert into skill_candidates"):
            self._db.inserts.append(
                _InsertedRow(
                    table="skill_candidates",
                    sql=sql_raw,
                    params=dict(params),
                )
            )
            return _Result([])
        if sql.startswith("insert into sub_agent_prompt_versions"):
            self._db.inserts.append(
                _InsertedRow(
                    table="sub_agent_prompt_versions",
                    sql=sql_raw,
                    params=dict(params),
                )
            )
            return _Result([])

        # ---- SELECT config FROM tools (tool_config snapshot) ----------
        # Not exercised by the skill-only happy path but kept as a
        # harmless default so unrelated assertions don't trip on
        # unhandled SQL if the LLM drifts under development.
        if sql.startswith("select config from tools"):
            return _Result([])

        return _Result([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:  # pragma: no cover - unused
        return None

    # -- trajectory query dispatch -----------------------------------

    def _tool_call_failures(self, params: dict) -> _Result:
        since: datetime = params["since"]
        limit = int(params.get("limit", 500))
        rows = [
            t
            for t in self._db.trajectories
            if t.kind == "tool_call"
            and t.outcome in ("error", "timeout")
            and t.created_at > since
        ]
        rows.sort(key=lambda t: t.created_at, reverse=True)
        rows = rows[:limit]
        return _Result(
            [
                _Row(
                    id=t.id,
                    session_id=t.session_id,
                    kind=t.kind,
                    outcome=t.outcome,
                    created_at=t.created_at,
                    data=t.data,
                    tags=t.tags,
                )
                for t in rows
            ]
        )

    def _grouped_session_counts(self, params: dict) -> _Result:
        since: datetime = params["since"]
        threshold = int(params.get("threshold", 3))
        counts: dict[uuid.UUID, int] = {}
        for t in self._db.trajectories:
            if t.outcome in ("error", "timeout") and t.created_at > since:
                counts[t.session_id] = counts.get(t.session_id, 0) + 1
        return _Result(
            [_Row(session_id=sid, n=n) for sid, n in counts.items() if n >= threshold]
        )

    def _per_session_failures(self, params: dict) -> _Result:
        since: datetime = params["since"]
        sid = params["sid"]
        if not isinstance(sid, uuid.UUID):
            sid = uuid.UUID(str(sid))
        limit = int(params.get("limit", 10))
        rows = [
            t
            for t in self._db.trajectories
            if t.session_id == sid
            and t.outcome in ("error", "timeout")
            and t.created_at > since
        ]
        rows.sort(key=lambda t: t.created_at, reverse=True)
        rows = rows[:limit]
        return _Result(
            [
                _Row(
                    id=t.id,
                    session_id=t.session_id,
                    kind=t.kind,
                    outcome=t.outcome,
                    created_at=t.created_at,
                    data=t.data,
                    tags=t.tags,
                )
                for t in rows
            ]
        )


# ---------------------------------------------------------------------------
# Scripted LLM — returns a canned sequence of responses
# ---------------------------------------------------------------------------


@dataclass
class _LLMResponse:
    content: str


class _ScriptedLLM:
    """Returns ``bodies`` in order on each ``ainvoke`` call.

    ``calls`` captures the message lists so the test can assert the
    right system prompt reached the model.
    """

    def __init__(self, bodies: list[str]) -> None:
        self._bodies = list(bodies)
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self._bodies:
            raise AssertionError("scripted LLM exhausted")
        return _LLMResponse(content=self._bodies.pop(0))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_failing_trajectories(n: int, *, now: datetime) -> list[_Trajectory]:
    """Create ``n`` tool_call failures spread across 2 sessions.

    Two sessions so the 24h repeated-failure SQL (``HAVING COUNT >= 3``)
    also fires — exercising the union + dedup behaviour inside
    :func:`cluster_failures` rather than only the tool_call pull path.
    """
    sid_a = uuid.uuid4()
    sid_b = uuid.uuid4()
    out: list[_Trajectory] = []
    for i in range(n):
        out.append(
            _Trajectory(
                id=uuid.uuid4(),
                session_id=sid_a if i % 2 == 0 else sid_b,
                kind="tool_call",
                outcome="timeout" if i % 3 == 0 else "error",
                created_at=now - timedelta(minutes=i + 1),
                data={
                    "tool_name": "grep_kb" if i % 2 == 0 else "search_wiki",
                    "error_message": f"upstream timeout on attempt {i}",
                },
                tags=["kb"],
            )
        )
    return out


def _cluster_response(trajectory_ids: list[uuid.UUID]) -> str:
    """Valid CLUSTER_FAILURES_PROMPT output — one skill-typed cluster."""
    return json.dumps(
        {
            "clusters": [
                {
                    "name": "kb_lookup_timeouts",
                    "description": (
                        "Repeated timeouts on grep_kb / search_wiki when "
                        "chasing long-tail queries."
                    ),
                    "example_trajectory_ids": [
                        str(tid) for tid in trajectory_ids[:5]
                    ],
                    "proposed_fix_type": "skill",
                }
            ]
        }
    )


def _skill_candidate_response(name: str) -> str:
    """Valid CANDIDATE_GEN_PROMPT output — one skill candidate."""
    return json.dumps(
        {
            "kind": "skill",
            "name": name,
            "data": {
                "skill_prompt": (
                    "Use this skill whenever a user asks a KB-style question "
                    "that previously timed out. Narrow the query window, "
                    "prefer cached entries, and fall back to the wiki "
                    "compact view before escalating." * 2
                ),
                "description": "retry-aware KB lookup skill",
                "tags": ["kb", "retry", "fallback"],
                "tool_names": ["grep_kb", "search_wiki"],
            },
            "expected_improvement": (
                "cut KB lookup timeouts by about 40% across the two affected "
                "sessions"
            ),
        }
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# The test — dry-run reflection cycle
# ---------------------------------------------------------------------------


def test_dry_run_reflection_cycle_produces_proposed_skill_candidate(
    tmp_path: Path,
) -> None:
    """10 error trajectories → at least one ``status='proposed'``
    skill candidate + SKILL.md on disk (R-3.1, R-3.3).
    """
    db = _FakeDB()
    now = datetime.now(UTC)
    trajectories = _make_failing_trajectories(10, now=now)
    for traj in trajectories:
        db.add_trajectory(traj)

    candidate_name = "kb_retry_skill"
    llm = _ScriptedLLM(
        bodies=[
            _cluster_response([t.id for t in trajectories]),
            _skill_candidate_response(candidate_name),
        ]
    )

    result = _run(
        run_reflection_full_cycle(
            llm=llm,
            db_factory=db.factory(),
            persist=True,
            skills_root_dir=tmp_path,
            now=now,
        )
    )

    # ---- reflection stage produced exactly one cluster --------------
    assert result.reflection.status == "ok", result.reflection.to_dict()
    assert result.reflection.n_trajectories_considered == 10
    assert len(result.reflection.clusters) == 1
    cluster = result.reflection.clusters[0]
    assert cluster.proposed_fix_type == "skill"
    # Every cluster id the reflector surfaces must come from the input
    # pool (R-3.1 contract).
    pool_ids = {t.id for t in trajectories}
    assert set(cluster.example_trajectory_ids).issubset(pool_ids)

    # ---- candidate stage produced at least one proposal ------------
    assert len(result.candidates.proposals) >= 1
    proposal = result.candidates.proposals[0]
    assert proposal.kind == "skill"
    assert proposal.name == candidate_name
    assert result.candidates.n_llm_failed == 0
    assert result.candidates.n_invalid_schema == 0
    assert result.candidates.n_persist_failed == 0
    assert result.candidates.n_rejected_by_guard == 0

    # ---- at least one persisted entry lives in skill_candidates -----
    assert len(result.candidates.persisted) >= 1
    persisted = result.candidates.persisted[0]
    assert persisted.kind == "skill"
    assert persisted.table == "skill_candidates"
    assert persisted.name == candidate_name
    assert persisted.artifact_path is not None

    # ---- SKILL.md lives under .candidate/ only (R-3.3) --------------
    expected_md = tmp_path / ".candidate" / candidate_name / "SKILL.md"
    assert expected_md.exists(), f"expected SKILL.md at {expected_md}"
    assert persisted.artifact_path == expected_md
    # Main skills directory must remain empty of the candidate.
    assert not (tmp_path / candidate_name / "SKILL.md").exists(), (
        "R-3.3: main data/skills/ must not receive candidate artefacts"
    )
    body = expected_md.read_text(encoding="utf-8")
    assert "status: candidate" in body
    assert f"name: {candidate_name}" in body

    # ---- row inserted with hard-coded status='proposed' -------------
    # SkillCandidateStore issues INSERT INTO skill_candidates with the
    # literal ``'proposed'`` string in the VALUES clause. Capturing the
    # statement and asserting on its normalised shape confirms the
    # candidate lands in the expected lifecycle state.
    skill_inserts = [r for r in db.inserts if r.table == "skill_candidates"]
    assert len(skill_inserts) >= 1
    insert_sql = " ".join(skill_inserts[0].sql.split()).lower()
    assert "'proposed'" in insert_sql, (
        "candidate INSERT must hard-code status='proposed' (R-3.3)"
    )
    # No prompt-version row should have been touched for a skill-only run.
    assert all(r.table != "sub_agent_prompt_versions" for r in db.inserts)

    # ---- sanity: correct system prompts reached the LLM -------------
    assert len(llm.calls) == 2
    assert llm.calls[0][0].content == CLUSTER_FAILURES_PROMPT
    assert llm.calls[1][0].content == CANDIDATE_GEN_PROMPT
