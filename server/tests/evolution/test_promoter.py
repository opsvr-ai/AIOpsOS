"""Unit tests for task 23.1 — :class:`Promoter`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23.1
(Phase L — Promoter).

**Validates: Requirements 3.2, 3.7, 3.8**

Surface covered:

* :meth:`Promoter.step` dispatches by current status and applies the
  phase-specific gates (R-3.7 shadow sampling, R-3.8 AB win-rate
  threshold, no user-visible side effects on shadow).
* :meth:`Promoter.activate_skill` moves the ``.candidate/<name>/`` tree
  into ``data/skills/<name>/`` and invalidates
  ``tool_manager.invalidate_cache`` (R-3.8).
* :meth:`Promoter.activate_prompt_patch` retires the previous active
  row, activates the candidate row transactionally, and emits an
  ``ops.agent.promotion`` Kafka event (R-3.8, R-3.15 enabler).
* :meth:`Promoter.activate_tool_config` shallow-merges the patch into
  ``tools.config`` JSONB and invalidates the tool manager cache.

Implementation choices:

* The tests share the same narrow in-memory DB pattern as
  :mod:`tests.evolution.test_candidate_store` — we embed it here
  because :class:`Promoter` issues additional SQL (prompt-version
  retire/activate, tool-config merge) that the sibling test's fake
  doesn't model.
* Stats are supplied via a lightweight :class:`_StubStatsProvider` so
  the promoter's R-3.6 / R-3.8 gates can be exercised deterministically
  without standing up the shadow runner.
* The Kafka producer is an ``AsyncMock`` so assertions target
  ``send_and_wait`` directly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.services.evolution.candidate_store import (
    InvalidStateTransition,
    SkillCandidateStore,
    TAG_KEY_TOOL_CONFIG_PATCH,
    TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT,
)
from src.services.evolution.promoter import (
    AB_MIN_SAMPLES,
    PROMOTION_TOPIC,
    SHADOW_MIN_SAMPLES,
    Promoter,
    PromoterStepResult,
)


# ---------------------------------------------------------------------------
# In-memory DB fake — narrow slice of what Promoter touches
# ---------------------------------------------------------------------------


@dataclass
class _SkillCandidateRow:
    id: uuid.UUID
    name: str
    status: str
    kind: str = "skill"
    target_ref: str | None = None
    tags: list[Any] = field(default_factory=list)


@dataclass
class _PromptVersionRow:
    id: uuid.UUID
    sub_agent_name: str
    status: str
    system_prompt: str = ""
    activated_at: Any | None = None
    retired_at: Any | None = None
    parent_version_id: uuid.UUID | None = None


@dataclass
class _ToolRow:
    id: uuid.UUID
    name: str
    config: dict[str, Any]


class _Row:
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


@dataclass
class _FakeDB:
    skill_candidates: dict[uuid.UUID, _SkillCandidateRow] = field(default_factory=dict)
    prompt_versions: dict[uuid.UUID, _PromptVersionRow] = field(default_factory=dict)
    tools: dict[str, _ToolRow] = field(default_factory=dict)

    def factory(self):
        db = self

        @asynccontextmanager
        async def _factory():
            yield _FakeSession(db)

        return _factory


class _FakeSession:
    """Dispatch SQL the Promoter + SkillCandidateStore emit.

    Deliberately narrow: we only handle shapes that the promoter or
    its transitive calls through :class:`SkillCandidateStore` produce.
    An unknown statement returns an empty result, which surfaces as
    a crash at the assertion layer — easier to debug than silent
    success on a typo.
    """

    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    async def execute(
        self, stmt: Any, params: dict[str, Any] | None = None
    ) -> _Result:
        sql = " ".join(str(stmt).split()).lower()
        params = params or {}

        # ---- SkillCandidateStore reads ---------------------------------
        if sql.startswith(
            "select id, kind, name, status, target_ref, tags from skill_candidates"
        ):
            rid = _to_uuid(params["id"])
            row = self._db.skill_candidates.get(rid)
            if row is None:
                return _Result([])
            return _Result(
                [
                    _Row(
                        id=row.id,
                        kind=row.kind,
                        name=row.name,
                        status=row.status,
                        target_ref=row.target_ref,
                        tags=row.tags,
                    )
                ]
            )

        if sql.startswith(
            "select id, sub_agent_name, status, system_prompt from sub_agent_prompt_versions"
        ):
            rid = _to_uuid(params["id"])
            row = self._db.prompt_versions.get(rid)
            if row is None:
                return _Result([])
            return _Result(
                [
                    _Row(
                        id=row.id,
                        sub_agent_name=row.sub_agent_name,
                        status=row.status,
                        system_prompt=row.system_prompt,
                    )
                ]
            )

        # ---- SkillCandidateStore writes --------------------------------
        if sql.startswith("update skill_candidates"):
            rid = _to_uuid(params["id"])
            row = self._db.skill_candidates.get(rid)
            if row is None or row.status != params.get("current_status"):
                return _Result([])
            row.status = str(params["new_status"])
            return _Result([])

        # ---- Promoter.activate_prompt_patch ----------------------------
        if sql.startswith(
            "select id from sub_agent_prompt_versions where sub_agent_name"
        ):
            name = str(params["name"])
            excl = _to_uuid(params["id"])
            candidates = [
                r
                for r in self._db.prompt_versions.values()
                if r.sub_agent_name == name and r.status == "active" and r.id != excl
            ]
            if not candidates:
                return _Result([])
            return _Result([_Row(id=candidates[0].id)])

        if sql.startswith(
            "update sub_agent_prompt_versions set status = 'retired'"
        ):
            rid = _to_uuid(params["id"])
            row = self._db.prompt_versions.get(rid)
            if row is None:
                return _Result([])
            row.status = "retired"
            row.retired_at = "NOW"
            return _Result([])

        if sql.startswith(
            "update sub_agent_prompt_versions set status = 'active'"
        ):
            rid = _to_uuid(params["id"])
            row = self._db.prompt_versions.get(rid)
            if row is None or row.status != "ab":
                return _Result([])
            row.status = "active"
            row.activated_at = "NOW"
            prev = params.get("prev_id")
            row.parent_version_id = _to_uuid(prev) if prev else None
            return _Result([])

        if sql.startswith("update sub_agent_prompt_versions set status"):
            # Path used by SkillCandidateStore.update_status (status-only).
            # NOTE: this must come AFTER the 'retired' / 'active' specific
            # matchers above because those share the same statement prefix.
            rid = _to_uuid(params["id"])
            row = self._db.prompt_versions.get(rid)
            if row is None or row.status != params.get("current_status"):
                return _Result([])
            row.status = str(params["new_status"])
            return _Result([])

        # ---- Promoter.activate_tool_config -----------------------------
        if sql.startswith("select config from tools where name"):
            name = str(params["name"])
            tool = self._db.tools.get(name)
            if tool is None:
                return _Result([])
            return _Result([_Row(config=tool.config)])

        if sql.startswith("update tools set config"):
            name = str(params["name"])
            tool = self._db.tools.get(name)
            if tool is None:
                return _Result([])
            cfg = params["cfg"]
            tool.config = json.loads(cfg) if isinstance(cfg, str) else dict(cfg)
            return _Result([])

        # list_by_status / snapshot / others not exercised by these tests
        return _Result([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:  # pragma: no cover - unused
        return None


def _to_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# ---------------------------------------------------------------------------
# Stats provider stub
# ---------------------------------------------------------------------------


class _StubStatsProvider:
    """Injectable stats source — one fixed payload per candidate id.

    Keeps the tests' intent obvious: each test seeds a stats dict and
    the promoter reads it back verbatim. A missing entry returns an
    empty dict, which the promoter interprets as "no data, stay put".
    """

    def __init__(self, stats: dict[uuid.UUID, dict[str, Any]] | None = None) -> None:
        self._stats: dict[uuid.UUID, dict[str, Any]] = dict(stats or {})

    def set(self, candidate_id: uuid.UUID, stats: dict[str, Any]) -> None:
        self._stats[candidate_id] = stats

    async def get_stats(self, candidate_id: uuid.UUID) -> dict[str, Any]:
        return dict(self._stats.get(candidate_id, {}))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _seed_skill(
    db: _FakeDB, *, name: str = "triage_helper", status: str = "shadow"
) -> uuid.UUID:
    rid = uuid.uuid4()
    db.skill_candidates[rid] = _SkillCandidateRow(
        id=rid, name=name, status=status, kind="skill"
    )
    return rid


def _seed_prompt_patch(
    db: _FakeDB,
    *,
    sub_agent: str = "monitor",
    status: str = "ab",
    name: str = "monitor_patch_v1",
) -> uuid.UUID:
    """Seed both the candidate row (skill_candidates) and the prompt_version row.

    The Promoter reads the candidate's ``kind`` / ``target_ref`` from
    ``skill_candidates`` (via :meth:`SkillCandidateStore.get`'s
    fallthrough) and then writes to ``sub_agent_prompt_versions``. In
    this test fake we mirror the production CandidateStore design
    where prompt_patch rows live ONLY in ``sub_agent_prompt_versions``,
    so the store's ``get`` path hits the prompt-versions table.
    """
    rid = uuid.uuid4()
    db.prompt_versions[rid] = _PromptVersionRow(
        id=rid,
        sub_agent_name=sub_agent,
        status=status,
        system_prompt="candidate prompt body ...",
    )
    return rid


def _seed_tool_config(
    db: _FakeDB,
    *,
    tool: str = "grep_kb",
    status: str = "ab",
    patch: dict[str, Any] | None = None,
    pre_snapshot: dict[str, Any] | None = None,
    name: str = "grep_kb_retry_budget",
) -> uuid.UUID:
    rid = uuid.uuid4()
    tags = [
        {TAG_KEY_TOOL_CONFIG_PATCH: patch or {"retries": 5, "timeout": 60}},
        {TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT: pre_snapshot or {"retries": 2}},
    ]
    db.skill_candidates[rid] = _SkillCandidateRow(
        id=rid,
        name=name,
        status=status,
        kind="tool_config",
        target_ref=tool,
        tags=tags,
    )
    return rid


def _make_promoter(
    db: _FakeDB,
    *,
    tmp_path: Path,
    stats: _StubStatsProvider | None = None,
    producer: Any | None = None,
) -> Promoter:
    store = SkillCandidateStore(
        db_factory=db.factory(), skills_root_dir=tmp_path
    )
    return Promoter(
        store,
        stats_provider=stats or _StubStatsProvider(),
        db_factory=db.factory(),
        skills_root_dir=tmp_path,
        kafka_producer=producer,
    )


# ===========================================================================
# Promoter.step — dispatch + gating
# ===========================================================================


def test_step_shadow_insufficient_samples_stays(tmp_path: Path) -> None:
    """R-3.7: shadow holds until sample count OR timeout is reached."""
    db = _FakeDB()
    cid = _seed_skill(db, status="shadow")
    stats = _StubStatsProvider(
        {
            cid: {
                "samples": SHADOW_MIN_SAMPLES - 1,
                "baseline_score": 0.8,
                "candidate_score": 0.8,
                "error_rate_delta": 0.0,
                "age_hours": 1.0,
            }
        }
    )
    promoter = _make_promoter(db, tmp_path=tmp_path, stats=stats)

    result = _run(promoter.step(cid))
    assert isinstance(result, PromoterStepResult)
    assert result.action == "stay"
    assert result.to_status == "shadow"
    # Row unchanged.
    assert db.skill_candidates[cid].status == "shadow"


def test_step_shadow_promotes_to_ab_on_metrics_pass(tmp_path: Path) -> None:
    """R-3.7: sample threshold hit + scores within epsilon + safe → shadow→ab."""
    db = _FakeDB()
    cid = _seed_skill(db, status="shadow")
    stats = _StubStatsProvider(
        {
            cid: {
                "samples": SHADOW_MIN_SAMPLES,
                "baseline_score": 0.80,
                "candidate_score": 0.81,
                "error_rate_delta": 0.0,
                "age_hours": 5.0,
            }
        }
    )
    promoter = _make_promoter(db, tmp_path=tmp_path, stats=stats)

    result = _run(promoter.step(cid))
    assert result.action == "promoted"
    assert result.to_status == "ab"
    assert db.skill_candidates[cid].status == "ab"


def test_step_shadow_rejects_on_regression(tmp_path: Path) -> None:
    """Shadow fails the R-3.6 epsilon check → rejected."""
    db = _FakeDB()
    cid = _seed_skill(db, status="shadow")
    stats = _StubStatsProvider(
        {
            cid: {
                "samples": SHADOW_MIN_SAMPLES,
                "baseline_score": 0.90,
                # 0.90 - 0.80 = 0.10 regression, well past epsilon (0.02)
                "candidate_score": 0.80,
                "error_rate_delta": 0.0,
                "age_hours": 5.0,
            }
        }
    )
    promoter = _make_promoter(db, tmp_path=tmp_path, stats=stats)

    result = _run(promoter.step(cid))
    assert result.action == "rejected"
    assert db.skill_candidates[cid].status == "rejected"


def test_step_ab_insufficient_samples_stays(tmp_path: Path) -> None:
    """AB phase waits for sample threshold OR age timeout."""
    db = _FakeDB()
    cid = _seed_skill(db, status="ab")
    stats = _StubStatsProvider(
        {
            cid: {
                "samples": AB_MIN_SAMPLES - 1,
                "win_rate": 0.7,
                "error_rate_delta": 0.0,
                "age_days": 1.0,
            }
        }
    )
    promoter = _make_promoter(db, tmp_path=tmp_path, stats=stats)

    result = _run(promoter.step(cid))
    assert result.action == "stay"
    assert db.skill_candidates[cid].status == "ab"


def test_step_ab_rejects_on_low_win_rate(tmp_path: Path) -> None:
    """AB with win_rate < 0.55 → rejected."""
    db = _FakeDB()
    cid = _seed_skill(db, status="ab")
    stats = _StubStatsProvider(
        {
            cid: {
                "samples": AB_MIN_SAMPLES,
                "win_rate": 0.50,
                "error_rate_delta": 0.0,
                "age_days": 1.0,
            }
        }
    )
    promoter = _make_promoter(db, tmp_path=tmp_path, stats=stats)

    result = _run(promoter.step(cid))
    assert result.action == "rejected"
    assert db.skill_candidates[cid].status == "rejected"


def test_step_ab_rejects_on_high_error_rate(tmp_path: Path) -> None:
    """AB with error_rate_delta > 0.005 → rejected even if win rate passes."""
    db = _FakeDB()
    cid = _seed_skill(db, status="ab")
    stats = _StubStatsProvider(
        {
            cid: {
                "samples": AB_MIN_SAMPLES,
                "win_rate": 0.60,
                "error_rate_delta": 0.01,
                "age_days": 1.0,
            }
        }
    )
    promoter = _make_promoter(db, tmp_path=tmp_path, stats=stats)

    result = _run(promoter.step(cid))
    assert result.action == "rejected"


def test_step_ab_activates_skill_on_pass(tmp_path: Path) -> None:
    """R-3.8 skill path: ab → active moves .candidate dir and invalidates cache."""
    db = _FakeDB()
    cid = _seed_skill(db, name="new_kb_lookup", status="ab")

    # Materialise .candidate/new_kb_lookup/SKILL.md under the skills root.
    candidate_dir = tmp_path / ".candidate" / "new_kb_lookup"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "SKILL.md").write_text("---\nname: new_kb_lookup\n---\nbody\n")

    stats = _StubStatsProvider(
        {
            cid: {
                "samples": AB_MIN_SAMPLES,
                "win_rate": 0.60,
                "error_rate_delta": 0.001,
                "age_days": 2.0,
            }
        }
    )
    invalidations: list[bool] = []

    class _FakeToolMgr:
        async def invalidate_cache(self) -> None:
            invalidations.append(True)

    import src.services.tool_manager as tm

    original = tm.tool_manager
    tm.tool_manager = _FakeToolMgr()  # type: ignore[assignment]
    try:
        promoter = _make_promoter(db, tmp_path=tmp_path, stats=stats)
        result = _run(promoter.step(cid))
    finally:
        tm.tool_manager = original

    assert result.action == "promoted"
    assert result.to_status == "active"
    assert db.skill_candidates[cid].status == "active"

    # Candidate dir moved into the main skills tree.
    final_dir = tmp_path / "new_kb_lookup"
    assert final_dir.exists()
    assert (final_dir / "SKILL.md").exists()
    assert not (tmp_path / ".candidate" / "new_kb_lookup").exists()

    assert invalidations == [True]


# ===========================================================================
# activate_skill — unit (bypasses step gates)
# ===========================================================================


def test_activate_skill_moves_dir_and_invalidates(tmp_path: Path) -> None:
    db = _FakeDB()
    cid = _seed_skill(db, name="supersede_me", status="ab")

    candidate_dir = tmp_path / ".candidate" / "supersede_me"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "SKILL.md").write_text("body")
    # Pre-existing destination that must be overwritten by move.
    old_dst = tmp_path / "supersede_me"
    old_dst.mkdir()
    (old_dst / "SKILL.md").write_text("old body")

    invalidations: list[bool] = []

    class _FakeToolMgr:
        async def invalidate_cache(self) -> None:
            invalidations.append(True)

    import src.services.tool_manager as tm

    original = tm.tool_manager
    tm.tool_manager = _FakeToolMgr()  # type: ignore[assignment]
    try:
        promoter = _make_promoter(db, tmp_path=tmp_path)
        _run(promoter.activate_skill(cid))
    finally:
        tm.tool_manager = original

    assert db.skill_candidates[cid].status == "active"
    assert (tmp_path / "supersede_me" / "SKILL.md").read_text() == "body"
    assert not candidate_dir.exists()
    assert invalidations == [True]


def test_activate_skill_rejects_wrong_status(tmp_path: Path) -> None:
    db = _FakeDB()
    cid = _seed_skill(db, status="shadow")  # must be ab
    promoter = _make_promoter(db, tmp_path=tmp_path)

    with pytest.raises(InvalidStateTransition):
        _run(promoter.activate_skill(cid))


def test_activate_skill_rejects_wrong_kind(tmp_path: Path) -> None:
    db = _FakeDB()
    cid = _seed_tool_config(db)  # not skill
    promoter = _make_promoter(db, tmp_path=tmp_path)

    with pytest.raises(ValueError, match="not a skill candidate"):
        _run(promoter.activate_skill(cid))


# ===========================================================================
# activate_prompt_patch — DB transaction + Kafka event
# ===========================================================================


def test_activate_prompt_patch_flips_rows_and_emits_event(tmp_path: Path) -> None:
    db = _FakeDB()
    # Pre-seed an existing active row for the same sub-agent.
    prev_id = uuid.uuid4()
    db.prompt_versions[prev_id] = _PromptVersionRow(
        id=prev_id,
        sub_agent_name="monitor",
        status="active",
        system_prompt="old",
        activated_at="OLD",
    )
    # Candidate row at status=ab.
    cid = _seed_prompt_patch(db, sub_agent="monitor", status="ab")

    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()

    promoter = _make_promoter(db, tmp_path=tmp_path, producer=producer)

    _run(promoter.activate_prompt_patch(cid))

    # Previous active retired.
    assert db.prompt_versions[prev_id].status == "retired"
    assert db.prompt_versions[prev_id].retired_at == "NOW"
    # Candidate is now active with parent chain set.
    promoted = db.prompt_versions[cid]
    assert promoted.status == "active"
    assert promoted.activated_at == "NOW"
    assert promoted.parent_version_id == prev_id

    # Kafka event emitted on the promotion topic with a prompt_patch payload.
    assert producer.send_and_wait.await_count == 1
    topic, body = producer.send_and_wait.call_args.args
    assert topic == PROMOTION_TOPIC
    payload = json.loads(body.decode("utf-8"))
    assert payload["kind"] == "prompt_patch"
    assert payload["sub_agent_name"] == "monitor"
    assert payload["new_version_id"] == str(cid)
    assert payload["prev_version_id"] == str(prev_id)
    assert payload["to_status"] == "active"
    # event_id present so reloaders dedupe replays (R-3.18).
    assert payload["event_id"] == f"promote-prompt-{cid}"


def test_activate_prompt_patch_with_no_prior_active(tmp_path: Path) -> None:
    db = _FakeDB()
    cid = _seed_prompt_patch(db, sub_agent="analysis", status="ab")

    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()

    promoter = _make_promoter(db, tmp_path=tmp_path, producer=producer)
    _run(promoter.activate_prompt_patch(cid))

    promoted = db.prompt_versions[cid]
    assert promoted.status == "active"
    assert promoted.parent_version_id is None

    # Event still emitted, prev_version_id = None.
    assert producer.send_and_wait.await_count == 1
    payload = json.loads(producer.send_and_wait.call_args.args[1].decode("utf-8"))
    assert payload["prev_version_id"] is None


def test_activate_prompt_patch_rejects_wrong_status(tmp_path: Path) -> None:
    db = _FakeDB()
    cid = _seed_prompt_patch(db, sub_agent="ops", status="shadow")
    promoter = _make_promoter(db, tmp_path=tmp_path, producer=AsyncMock())

    with pytest.raises(InvalidStateTransition):
        _run(promoter.activate_prompt_patch(cid))


# ===========================================================================
# activate_tool_config — JSONB merge + cache invalidation
# ===========================================================================


def test_activate_tool_config_merges_and_invalidates(tmp_path: Path) -> None:
    db = _FakeDB()
    db.tools["grep_kb"] = _ToolRow(
        id=uuid.uuid4(),
        name="grep_kb",
        config={"retries": 2, "timeout": 10, "cache": True},
    )
    cid = _seed_tool_config(
        db,
        tool="grep_kb",
        status="ab",
        patch={"retries": 5, "timeout": 60},
    )

    invalidations: list[bool] = []

    class _FakeToolMgr:
        async def invalidate_cache(self) -> None:
            invalidations.append(True)

    import src.services.tool_manager as tm

    original = tm.tool_manager
    tm.tool_manager = _FakeToolMgr()  # type: ignore[assignment]
    try:
        promoter = _make_promoter(db, tmp_path=tmp_path)
        _run(promoter.activate_tool_config(cid))
    finally:
        tm.tool_manager = original

    # Shallow merge: patch keys override, unrelated keys preserved.
    assert db.tools["grep_kb"].config == {
        "retries": 5,
        "timeout": 60,
        "cache": True,
    }
    assert db.skill_candidates[cid].status == "active"
    assert invalidations == [True]


def test_activate_tool_config_rejects_wrong_status(tmp_path: Path) -> None:
    db = _FakeDB()
    cid = _seed_tool_config(db, status="shadow")
    promoter = _make_promoter(db, tmp_path=tmp_path)

    with pytest.raises(InvalidStateTransition):
        _run(promoter.activate_tool_config(cid))


def test_activate_tool_config_missing_target_raises(tmp_path: Path) -> None:
    """No target_ref → the promoter refuses rather than wildcard-merging."""
    db = _FakeDB()
    rid = uuid.uuid4()
    db.skill_candidates[rid] = _SkillCandidateRow(
        id=rid,
        name="broken",
        status="ab",
        kind="tool_config",
        target_ref=None,
        tags=[{TAG_KEY_TOOL_CONFIG_PATCH: {"x": 1}}],
    )
    promoter = _make_promoter(db, tmp_path=tmp_path)

    with pytest.raises(ValueError, match="no\\s+target_ref"):
        _run(promoter.activate_tool_config(rid))


# ===========================================================================
# Dispatch for unmanaged statuses
# ===========================================================================


def test_step_not_applicable_for_unmanaged_status(tmp_path: Path) -> None:
    """proposed / active / retired are driven by other actors — step is a no-op."""
    db = _FakeDB()
    cid = _seed_skill(db, status="proposed")
    promoter = _make_promoter(db, tmp_path=tmp_path)

    result = _run(promoter.step(cid))
    assert result.action == "not_applicable"
    assert result.to_status == "proposed"
    assert db.skill_candidates[cid].status == "proposed"


def test_step_missing_candidate_raises(tmp_path: Path) -> None:
    db = _FakeDB()
    promoter = _make_promoter(db, tmp_path=tmp_path)
    with pytest.raises(LookupError):
        _run(promoter.step(uuid.uuid4()))
