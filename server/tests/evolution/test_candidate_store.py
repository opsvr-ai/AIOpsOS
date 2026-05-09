"""Unit tests for task 21.4 — :class:`SkillCandidateStore`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 21.4
(Phase J — ReflectionWorker).

**Validates: Requirements 3.2, 3.3, 3.4, 3.13**

Covers :mod:`src.services.evolution.candidate_store` — the cohesive
persistence layer extracted from ``reflection_logic`` so the Promoter
(task 23.x), SkillReviewAgent (task 21.6) and reflection worker all
share one route.

Test groups:

* ``propose(...)`` routes by ``kind`` to the right table + artefact
  (R-3.2 / R-3.3).
* ``tool_config`` propose captures a pre-patch snapshot via
  :meth:`SkillCandidateStore.snapshot_tool_config` (R-3.13).
* ``update_status`` enforces the state machine from R-3.4. Every
  disallowed transition raises :class:`InvalidStateTransition`;
  every allowed edge flips the row.
* ``list_by_status`` projects across both candidate tables and only
  returns matching rows.
* ``get`` falls back to :data:`None` for unknown ids without raising.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src.services.evolution.candidate_store import (
    ALL_STATUSES,
    CandidateRow,
    InvalidStateTransition,
    SkillCandidateStore,
    STATE_TRANSITIONS,
    TAG_KEY_TOOL_CONFIG_PATCH,
    TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT,
)
from src.services.evolution.reflection_logic import CandidateProposal


# ---------------------------------------------------------------------------
# Fake DB — small in-memory fixture matching this test's narrow needs.
# Mirrors the style used in ``tests/workers/_fake_db.py`` but slimmed
# down to only the statements the store issues.
# ---------------------------------------------------------------------------


@dataclass
class _SkillCandidateRow:
    id: uuid.UUID
    name: str
    proposal_source: str
    origin_trajectory_ids: list[uuid.UUID] | None
    status: str
    skill_prompt: str
    description: str | None
    tags: list[Any]
    tool_names: list[str]
    manifest_sha256: str | None
    kind: str
    target_ref: str | None


@dataclass
class _PromptVersionRow:
    id: uuid.UUID
    sub_agent_name: str
    candidate_id: uuid.UUID | None
    system_prompt: str
    rationale: str | None
    status: str
    parent_version_id: uuid.UUID | None
    manifest_sha256: str | None


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
    """In-memory store + dispatch for the small SQL surface the store emits."""

    skill_candidates: dict[uuid.UUID, _SkillCandidateRow] = field(default_factory=dict)
    prompt_versions: dict[uuid.UUID, _PromptVersionRow] = field(default_factory=dict)
    tools: dict[str, _ToolRow] = field(default_factory=dict)
    tool_config_queries: list[str] = field(default_factory=list)

    def factory(self):
        db = self

        @asynccontextmanager
        async def _factory():
            yield _FakeSession(db)

        return _factory


class _FakeSession:
    """Dispatches raw SQL based on leading keywords.

    Keeping the match simple (leading words + a couple of unique
    tokens) means the fake doesn't have to parse real SQL — we just
    look at the shape :class:`SkillCandidateStore` emits.
    """

    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    async def execute(
        self, stmt: Any, params: dict[str, Any] | None = None
    ) -> _Result:
        sql = " ".join(str(stmt).split()).lower()
        params = params or {}

        # ---- writes --------------------------------------------------
        if sql.startswith("insert into skill_candidates"):
            return self._insert_skill_candidate(params)
        if sql.startswith("insert into sub_agent_prompt_versions"):
            return self._insert_prompt_version(params)
        if sql.startswith("update skill_candidates"):
            return self._update_skill_candidate(params)
        if sql.startswith("update sub_agent_prompt_versions"):
            return self._update_prompt_version(params)

        # ---- reads ---------------------------------------------------
        if sql.startswith("select id, kind, name, status, target_ref, tags from skill_candidates"):
            if "where id = :id" in sql:
                return self._select_skill_by_id(params)
            if "where status = :status" in sql:
                return self._select_skill_by_status(params)
        if sql.startswith(
            "select id, sub_agent_name, status, system_prompt from sub_agent_prompt_versions"
        ):
            if "where id = :id" in sql:
                return self._select_prompt_by_id(params)
            if "where status = :status" in sql:
                return self._select_prompt_by_status(params)
        if sql.startswith("select config from tools"):
            return self._select_tool_config(params)

        return _Result([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:  # pragma: no cover - unused
        return None

    # -- write dispatch -------------------------------------------------

    def _insert_skill_candidate(self, params: dict) -> _Result:
        rid = _to_uuid(params["id"])
        tags = json.loads(params["tags"]) if isinstance(params.get("tags"), str) else list(params.get("tags") or [])
        tool_names = (
            json.loads(params["tool_names"])
            if isinstance(params.get("tool_names"), str)
            else list(params.get("tool_names") or [])
        )
        # The SQL INSERT hard-codes ``kind`` and status differently for
        # skill vs tool_config. Peek at the tool_names/tags shape to
        # distinguish: tool_config rows always carry a
        # ``_tool_config_patch`` sentinel in their tags list.
        kind = "skill"
        if any(
            isinstance(item, dict) and TAG_KEY_TOOL_CONFIG_PATCH in item
            for item in tags
        ):
            kind = "tool_config"
        target_ref = params.get("target")
        row = _SkillCandidateRow(
            id=rid,
            name=str(params["name"]),
            proposal_source=str(params["source"]),
            origin_trajectory_ids=params.get("origins") or [],
            status="proposed",
            skill_prompt=str(params.get("prompt") or ""),
            description=params.get("desc"),
            tags=tags,
            tool_names=tool_names,
            manifest_sha256=params.get("manifest"),
            kind=kind,
            target_ref=str(target_ref) if target_ref else None,
        )
        self._db.skill_candidates[rid] = row
        return _Result([])

    def _insert_prompt_version(self, params: dict) -> _Result:
        rid = _to_uuid(params["id"])
        row = _PromptVersionRow(
            id=rid,
            sub_agent_name=str(params["name"]),
            candidate_id=None,
            system_prompt=str(params["prompt"]),
            rationale=str(params.get("rationale") or ""),
            status="proposed",
            parent_version_id=None,
            manifest_sha256=params.get("manifest"),
        )
        self._db.prompt_versions[rid] = row
        return _Result([])

    def _update_skill_candidate(self, params: dict) -> _Result:
        rid = _to_uuid(params["id"])
        row = self._db.skill_candidates.get(rid)
        if row is None:
            return _Result([])
        if row.status != params.get("current_status"):
            return _Result([])  # no-op — concurrent writer guard
        row.status = str(params["new_status"])
        return _Result([])

    def _update_prompt_version(self, params: dict) -> _Result:
        rid = _to_uuid(params["id"])
        row = self._db.prompt_versions.get(rid)
        if row is None:
            return _Result([])
        if row.status != params.get("current_status"):
            return _Result([])
        row.status = str(params["new_status"])
        return _Result([])

    # -- read dispatch --------------------------------------------------

    def _select_skill_by_id(self, params: dict) -> _Result:
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

    def _select_skill_by_status(self, params: dict) -> _Result:
        target = str(params["status"])
        rows = [r for r in self._db.skill_candidates.values() if r.status == target]
        return _Result(
            [
                _Row(
                    id=r.id,
                    kind=r.kind,
                    name=r.name,
                    status=r.status,
                    target_ref=r.target_ref,
                    tags=r.tags,
                )
                for r in rows
            ]
        )

    def _select_prompt_by_id(self, params: dict) -> _Result:
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

    def _select_prompt_by_status(self, params: dict) -> _Result:
        target = str(params["status"])
        rows = [r for r in self._db.prompt_versions.values() if r.status == target]
        return _Result(
            [
                _Row(
                    id=r.id,
                    sub_agent_name=r.sub_agent_name,
                    status=r.status,
                    system_prompt=r.system_prompt,
                )
                for r in rows
            ]
        )

    def _select_tool_config(self, params: dict) -> _Result:
        name = str(params["name"])
        self._db.tool_config_queries.append(name)
        tool = self._db.tools.get(name)
        if tool is None:
            return _Result([])
        return _Result([_Row(config=tool.config)])


def _to_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# ---------------------------------------------------------------------------
# Helpers — proposals
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _skill_proposal(
    *, name: str = "new_kb_lookup", cluster: str = "kb_cluster"
) -> CandidateProposal:
    return CandidateProposal(
        kind="skill",
        name=name,
        data={
            "skill_prompt": (
                "Describe what this skill does; be detailed and specific "
                "so the agent has enough context to use it well." * 2
            ),
            "description": "kb lookup skill",
            "tags": ["kb", "lookup"],
            "tool_names": ["grep_kb"],
        },
        expected_improvement="reduce kb miss rate by 30%",
        cluster_name=cluster,
        origin_trajectory_ids=[uuid.uuid4()],
    )


def _prompt_patch_proposal(
    *, name: str = "monitor_patch_v1", sub_agent: str = "monitor"
) -> CandidateProposal:
    return CandidateProposal(
        kind="prompt_patch",
        name=name,
        data={
            "sub_agent_name": sub_agent,
            "new_prompt": "You are the monitor sub-agent. " * 10,
            "rationale": "tighter triage",
        },
        expected_improvement="faster triage",
        cluster_name="c",
        origin_trajectory_ids=[uuid.uuid4()],
    )


def _tool_config_proposal(
    *,
    name: str = "grep_kb_retry_budget",
    tool: str = "grep_kb",
    patch: dict[str, Any] | None = None,
) -> CandidateProposal:
    return CandidateProposal(
        kind="tool_config",
        name=name,
        data={
            "tool_name": tool,
            "patch": patch or {"retries": 5, "timeout": 60},
            "rationale": "give the tool more budget",
        },
        expected_improvement="reduce timeouts",
        cluster_name="c",
        origin_trajectory_ids=[uuid.uuid4()],
    )


def _make_store(db: _FakeDB, skills_root: Path) -> SkillCandidateStore:
    return SkillCandidateStore(
        db_factory=db.factory(),
        skills_root_dir=skills_root,
    )


# ---------------------------------------------------------------------------
# propose — skill kind writes SKILL.md + skill_candidates
# ---------------------------------------------------------------------------


def test_propose_skill_writes_candidate_md_and_inserts_row(tmp_path: Path) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    proposal = _skill_proposal(name="triage_skill")

    persisted = _run(store.propose(proposal))

    assert persisted.kind == "skill"
    assert persisted.table == "skill_candidates"
    assert persisted.artifact_path is not None

    md_path = tmp_path / ".candidate" / "triage_skill" / "SKILL.md"
    assert md_path.exists()
    assert not (tmp_path / "triage_skill" / "SKILL.md").exists(), (
        "R-3.3: main data/skills/ must remain untouched"
    )

    body = md_path.read_text(encoding="utf-8")
    assert "status: candidate" in body
    assert "name: triage_skill" in body

    # DB row created with kind=skill + status=proposed.
    assert len(db.skill_candidates) == 1
    row = next(iter(db.skill_candidates.values()))
    assert row.kind == "skill"
    assert row.status == "proposed"
    assert row.name == "triage_skill"
    # No prompt_versions row was touched.
    assert db.prompt_versions == {}


# ---------------------------------------------------------------------------
# propose — prompt_patch kind inserts ONLY into sub_agent_prompt_versions
# ---------------------------------------------------------------------------


def test_propose_prompt_patch_inserts_only_prompt_version(tmp_path: Path) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)

    persisted = _run(store.propose(_prompt_patch_proposal(sub_agent="monitor")))

    assert persisted.kind == "prompt_patch"
    assert persisted.table == "sub_agent_prompt_versions"
    assert persisted.artifact_path is None

    # No skill_candidates row.
    assert db.skill_candidates == {}
    # One prompt-versions row with status=proposed targeting the right
    # sub-agent.
    assert len(db.prompt_versions) == 1
    row = next(iter(db.prompt_versions.values()))
    assert row.sub_agent_name == "monitor"
    assert row.status == "proposed"
    assert row.manifest_sha256 is not None and len(row.manifest_sha256) == 64


# ---------------------------------------------------------------------------
# propose — tool_config captures pre-patch snapshot (R-3.13)
# ---------------------------------------------------------------------------


def test_propose_tool_config_records_pre_patch_snapshot(tmp_path: Path) -> None:
    db = _FakeDB()
    # Seed the tool with an existing config that should become the snapshot.
    db.tools["grep_kb"] = _ToolRow(
        id=uuid.uuid4(),
        name="grep_kb",
        config={"retries": 2, "timeout": 10, "cache": True},
    )
    store = _make_store(db, tmp_path)

    persisted = _run(
        store.propose(
            _tool_config_proposal(
                tool="grep_kb", patch={"retries": 5, "timeout": 60}
            )
        )
    )

    assert persisted.kind == "tool_config"
    assert persisted.table == "skill_candidates"

    # ``snapshot_tool_config`` was consulted before the INSERT.
    assert db.tool_config_queries == ["grep_kb"]

    # Stored row contains both the patch and the pre-patch snapshot
    # under their sentinel keys.
    row = next(iter(db.skill_candidates.values()))
    assert row.kind == "tool_config"
    assert row.target_ref == "grep_kb"

    patch_dict = next(
        item[TAG_KEY_TOOL_CONFIG_PATCH]
        for item in row.tags
        if isinstance(item, dict) and TAG_KEY_TOOL_CONFIG_PATCH in item
    )
    snapshot_dict = next(
        item[TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT]
        for item in row.tags
        if isinstance(item, dict) and TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT in item
    )

    assert patch_dict == {"retries": 5, "timeout": 60}
    # Snapshot reflects the full pre-existing config — Promoter uses
    # this to restore on rollback (R-3.13).
    assert snapshot_dict == {"retries": 2, "timeout": 10, "cache": True}


def test_propose_tool_config_snapshot_empty_for_unknown_tool(
    tmp_path: Path,
) -> None:
    """Unknown tool → empty snapshot, not a crash (store keeps going)."""
    db = _FakeDB()  # no ``tools`` rows
    store = _make_store(db, tmp_path)

    persisted = _run(store.propose(_tool_config_proposal(tool="brand_new")))

    assert persisted.table == "skill_candidates"
    assert db.tool_config_queries == ["brand_new"]

    row = next(iter(db.skill_candidates.values()))
    snapshot = next(
        item[TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT]
        for item in row.tags
        if isinstance(item, dict) and TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT in item
    )
    assert snapshot == {}


def test_snapshot_tool_config_direct_call(tmp_path: Path) -> None:
    db = _FakeDB()
    db.tools["grep_kb"] = _ToolRow(
        id=uuid.uuid4(), name="grep_kb", config={"retries": 2}
    )
    store = _make_store(db, tmp_path)

    snap = _run(store.snapshot_tool_config("grep_kb"))
    assert snap == {"retries": 2}

    # Mutating the returned dict must not affect the stored config.
    snap["retries"] = 999
    again = _run(store.snapshot_tool_config("grep_kb"))
    assert again == {"retries": 2}


# ---------------------------------------------------------------------------
# propose — unknown kind raises
# ---------------------------------------------------------------------------


def test_propose_rejects_unknown_kind(tmp_path: Path) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    bad = CandidateProposal(
        kind="garbage",
        name="x",
        data={},
        expected_improvement="e",
        cluster_name="c",
        origin_trajectory_ids=[],
    )
    with pytest.raises(ValueError, match="unknown candidate kind"):
        _run(store.propose(bad))


# ---------------------------------------------------------------------------
# get — round-trip for skill + prompt_patch rows
# ---------------------------------------------------------------------------


def test_get_returns_skill_candidate_row(tmp_path: Path) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    persisted = _run(store.propose(_skill_proposal(name="s1")))

    fetched = _run(store.get(persisted.row_id))
    assert isinstance(fetched, CandidateRow)
    assert fetched.kind == "skill"
    assert fetched.name == "s1"
    assert fetched.status == "proposed"
    assert fetched.table == "skill_candidates"


def test_get_returns_prompt_patch_row(tmp_path: Path) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    persisted = _run(store.propose(_prompt_patch_proposal(sub_agent="ops")))

    fetched = _run(store.get(persisted.row_id))
    assert fetched is not None
    assert fetched.kind == "prompt_patch"
    assert fetched.name == "ops"
    assert fetched.table == "sub_agent_prompt_versions"


def test_get_unknown_id_returns_none(tmp_path: Path) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    assert _run(store.get(uuid.uuid4())) is None


# ---------------------------------------------------------------------------
# list_by_status — filtered by status, projects across both tables
# ---------------------------------------------------------------------------


def test_list_by_status_returns_matching_rows_across_tables(
    tmp_path: Path,
) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)

    # Two skill candidates and one prompt_patch, all status=proposed.
    _run(store.propose(_skill_proposal(name="s1")))
    _run(store.propose(_skill_proposal(name="s2")))
    _run(store.propose(_prompt_patch_proposal(sub_agent="knowledge")))

    # Manually flip one skill and the prompt_patch to shadow so we
    # have two rows at ``shadow`` and one at ``proposed``.
    s1_id = next(
        r.id for r in db.skill_candidates.values() if r.name == "s1"
    )
    knowledge_id = next(
        r.id for r in db.prompt_versions.values() if r.sub_agent_name == "knowledge"
    )
    _run(store.update_status(s1_id, "shadow"))
    _run(store.update_status(knowledge_id, "shadow"))

    shadow_rows = _run(store.list_by_status("shadow"))
    proposed_rows = _run(store.list_by_status("proposed"))

    shadow_names = sorted(r.name for r in shadow_rows)
    proposed_names = sorted(r.name for r in proposed_rows)

    assert shadow_names == ["knowledge", "s1"]
    assert proposed_names == ["s2"]

    # kind projection is correct for each side.
    kinds_by_name = {r.name: r.kind for r in shadow_rows}
    assert kinds_by_name == {"s1": "skill", "knowledge": "prompt_patch"}


def test_list_by_status_unknown_status_returns_empty(tmp_path: Path) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    _run(store.propose(_skill_proposal()))

    assert _run(store.list_by_status("garbage")) == []


# ---------------------------------------------------------------------------
# update_status — allowed transitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ("proposed", "shadow"),
        ("proposed", "rejected"),
    ],
)
def test_allowed_transitions_from_proposed(tmp_path: Path, path: tuple[str, str]) -> None:
    src, dst = path
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    persisted = _run(store.propose(_skill_proposal()))

    # Sanity: the newly-persisted candidate is ``proposed``.
    row_before = _run(store.get(persisted.row_id))
    assert row_before is not None
    assert row_before.status == src

    _run(store.update_status(persisted.row_id, dst))

    row_after = _run(store.get(persisted.row_id))
    assert row_after is not None
    assert row_after.status == dst


def test_full_happy_path_chain(tmp_path: Path) -> None:
    """proposed → shadow → ab → active → retired is end-to-end legal."""
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    persisted = _run(store.propose(_skill_proposal()))

    for step in ("shadow", "ab", "active", "retired"):
        _run(store.update_status(persisted.row_id, step))
        row = _run(store.get(persisted.row_id))
        assert row is not None and row.status == step


def test_update_status_idempotent_on_same_status(tmp_path: Path) -> None:
    """Writing the same status back is a no-op (not a reject)."""
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    persisted = _run(store.propose(_skill_proposal()))

    # Same status from ``proposed`` — no exception, no change.
    _run(store.update_status(persisted.row_id, "proposed"))
    row = _run(store.get(persisted.row_id))
    assert row is not None and row.status == "proposed"


# ---------------------------------------------------------------------------
# update_status — disallowed transitions (R-3.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # Proposed cannot leap over shadow/ab.
        ("proposed", "ab"),
        ("proposed", "active"),
        ("proposed", "retired"),
        # Rejected is terminal.
        ("rejected", "shadow"),
        ("rejected", "active"),
        ("rejected", "retired"),
        # Retired is terminal.
        ("retired", "active"),
        ("retired", "shadow"),
        ("retired", "proposed"),
        # shadow cannot go straight to active.
        ("shadow", "active"),
        ("shadow", "proposed"),
        # active cannot move backwards.
        ("active", "shadow"),
        ("active", "ab"),
        ("active", "proposed"),
        ("active", "rejected"),
        # ab cannot move back to shadow.
        ("ab", "shadow"),
        ("ab", "proposed"),
    ],
)
def test_disallowed_transitions_raise(tmp_path: Path, path: tuple[str, str]) -> None:
    src, dst = path
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    persisted = _run(store.propose(_skill_proposal()))

    # Walk the candidate to ``src`` using only legal edges.
    _walk_to_status(store, persisted.row_id, target=src)

    with pytest.raises(InvalidStateTransition) as exc_info:
        _run(store.update_status(persisted.row_id, dst))

    assert exc_info.value.current == src
    assert exc_info.value.new == dst

    # Row unchanged.
    row = _run(store.get(persisted.row_id))
    assert row is not None and row.status == src


def test_update_status_unknown_status_raises(tmp_path: Path) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    persisted = _run(store.propose(_skill_proposal()))

    with pytest.raises(InvalidStateTransition):
        _run(store.update_status(persisted.row_id, "bogus"))


def test_update_status_missing_candidate_raises(tmp_path: Path) -> None:
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    with pytest.raises(LookupError):
        _run(store.update_status(uuid.uuid4(), "shadow"))


def test_update_status_also_works_on_prompt_patch(tmp_path: Path) -> None:
    """Prompt patch candidates flow through the same state machine."""
    db = _FakeDB()
    store = _make_store(db, tmp_path)
    persisted = _run(store.propose(_prompt_patch_proposal(sub_agent="ops")))

    for step in ("shadow", "ab", "active", "retired"):
        _run(store.update_status(persisted.row_id, step))
        row = _run(store.get(persisted.row_id))
        assert row is not None and row.status == step


# ---------------------------------------------------------------------------
# STATE_TRANSITIONS — lock in the spec's state machine
# ---------------------------------------------------------------------------


def test_state_machine_matches_spec_r_3_4() -> None:
    """R-3.4: proposed→shadow|rejected; shadow→ab|rejected|retired;
    ab→active|rejected|retired; active→retired.
    """
    assert STATE_TRANSITIONS["proposed"] == frozenset({"shadow", "rejected"})
    assert STATE_TRANSITIONS["shadow"] == frozenset({"ab", "rejected", "retired"})
    assert STATE_TRANSITIONS["ab"] == frozenset({"active", "rejected", "retired"})
    assert STATE_TRANSITIONS["active"] == frozenset({"retired"})
    assert STATE_TRANSITIONS["retired"] == frozenset()
    assert STATE_TRANSITIONS["rejected"] == frozenset()
    assert ALL_STATUSES == frozenset(
        {"proposed", "shadow", "ab", "active", "retired", "rejected"}
    )


# ---------------------------------------------------------------------------
# helpers for tests above
# ---------------------------------------------------------------------------


def _walk_to_status(
    store: SkillCandidateStore,
    candidate_id: uuid.UUID,
    *,
    target: str,
) -> None:
    """Drive a freshly-proposed candidate to ``target`` via legal edges.

    Used by the disallowed-transition tests so we can assert that
    e.g. ``active → shadow`` is rejected even after reaching
    ``active`` via the happy path.
    """
    # Fixed legal walks from ``proposed`` to every reachable status.
    paths: dict[str, list[str]] = {
        "proposed": [],
        "shadow": ["shadow"],
        "ab": ["shadow", "ab"],
        "active": ["shadow", "ab", "active"],
        "retired": ["shadow", "ab", "active", "retired"],
        "rejected": ["rejected"],
    }
    steps = paths.get(target)
    if steps is None:
        raise AssertionError(f"no legal walk to {target}")
    for step in steps:
        _run(store.update_status(candidate_id, step))
