"""Unit tests for :class:`src.agent.sub_agents.skill_review_agent.SkillReviewAgent`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 21.6
(Phase J — ReflectionWorker).

**Validates: Requirements 3.10**

Covers R-3.10: when ``SkillReviewAgent`` recognises a reusable pattern
the resulting skill MUST land in
``skill_candidates(status='proposed', proposal_source='skill_review_agent')``
with its SKILL.md under ``data/skills/.candidate/<name>/`` — never in
the active ``tools`` table and never in the main ``data/skills/``
tree.

Tests:

* ``review`` creates a ``skill_candidates`` row with the expected
  status + proposal_source when the LLM suggests a skill pattern.
* No row is inserted into the ``tools`` table (no activation).
* The SKILL.md only appears under ``data/skills/.candidate/<name>/``;
  the main ``data/skills/`` directory is untouched.
* ``review`` is no-op when the LLM returns no suggestions, but it
  still resets the ``skill_review_due`` flag so downstream schedulers
  don't re-trigger the same session forever.
* Suggestions missing required fields or with a too-short
  ``skill_prompt`` are dropped silently without raising.
* A failing ``propose`` call on one suggestion doesn't stop the
  others from being processed.
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

from src.agent.sub_agents import skill_review_agent as sra
from src.agent.sub_agents.skill_review_agent import (
    PROPOSAL_SOURCE,
    SkillReviewAgent,
)
from src.services.evolution.candidate_store import (
    SkillCandidateStore,
    TAG_KEY_TOOL_CONFIG_PATCH,
)


# ---------------------------------------------------------------------------
# Fake DB — mirrors the shape used by ``tests/evolution/test_candidate_store.py``.
#
# The store only ever INSERTs into ``skill_candidates`` /
# ``sub_agent_prompt_versions`` and SELECTs by id / status. Re-using
# that minimal surface here keeps the test narrow while still
# exercising the real store code path.
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
    status: str
    system_prompt: str
    manifest_sha256: str | None


@dataclass
class _ToolRow:
    """Row in the ``tools`` table — present only to assert it stays empty.

    The propose-only refactor (R-3.10) MUST NOT insert into ``tools``.
    Tests hand the agent a fake DB that would accept such an insert,
    and then assert afterwards that nothing landed in ``tools``.
    """

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
    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(stmt).split()).lower()
        params = params or {}

        if sql.startswith("insert into skill_candidates"):
            return self._insert_skill_candidate(params)
        if sql.startswith("insert into sub_agent_prompt_versions"):
            return self._insert_prompt_version(params)
        if sql.startswith("insert into tools"):
            # Propose-only MUST NOT trigger inserts here. Fake accepts
            # the row so the test can assert on ``tools`` being empty
            # (a raise would mask the error origin).
            tid = params.get("id") or uuid.uuid4()
            tid = tid if isinstance(tid, uuid.UUID) else uuid.UUID(str(tid))
            name = str(params.get("name") or "")
            self._db.tools[name] = _ToolRow(
                id=tid, name=name, config=params.get("config") or {}
            )
            return _Result([])

        if sql.startswith("select config from tools"):
            name = str(params["name"])
            tool = self._db.tools.get(name)
            if tool is None:
                return _Result([])
            return _Result([_Row(config=tool.config)])

        return _Result([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    # -- INSERT dispatch --------------------------------------------------

    def _insert_skill_candidate(self, params: dict) -> _Result:
        rid = params["id"]
        rid = rid if isinstance(rid, uuid.UUID) else uuid.UUID(str(rid))

        tags = (
            json.loads(params["tags"])
            if isinstance(params.get("tags"), str)
            else list(params.get("tags") or [])
        )
        tool_names = (
            json.loads(params["tool_names"])
            if isinstance(params.get("tool_names"), str)
            else list(params.get("tool_names") or [])
        )

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
        rid = params["id"]
        rid = rid if isinstance(rid, uuid.UUID) else uuid.UUID(str(rid))
        row = _PromptVersionRow(
            id=rid,
            sub_agent_name=str(params["name"]),
            system_prompt=str(params["prompt"]),
            status="proposed",
            manifest_sha256=params.get("manifest"),
        )
        self._db.prompt_versions[rid] = row
        return _Result([])


# ---------------------------------------------------------------------------
# Stubs — LLM + session-side factory
# ---------------------------------------------------------------------------


@dataclass
class _FakeLLMResponse:
    content: str


class _FakeLLM:
    """Returns a pre-configured content string on ``ainvoke``.

    ``content`` is usually a JSON blob the agent's parser consumes
    (stripped of triple-backtick fences first if present).
    """

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> _FakeLLMResponse:
        self.calls.append(messages)
        return _FakeLLMResponse(content=self._content)


@dataclass
class _FakeMessage:
    role: str
    content: str
    created_at: Any = None


class _SessionScalars:
    def __init__(self, msgs: list[_FakeMessage]) -> None:
        self._msgs = msgs

    def all(self) -> list[_FakeMessage]:
        return list(self._msgs)


class _SessionResult:
    def __init__(self, msgs: list[_FakeMessage]) -> None:
        self._msgs = msgs

    def scalars(self) -> _SessionScalars:
        return _SessionScalars(self._msgs)


class _FakeAppSession:
    """Minimal stand-in for the app's ``async_session_factory`` session.

    Only needs to handle:
    * ``SELECT ... FROM messages`` → return the pre-loaded conversation.
    * ``UPDATE sessions ... skill_review_due`` → record the reset.
    """

    def __init__(self, messages: list[_FakeMessage], resets: list[str]) -> None:
        self._messages = messages
        self._resets = resets

    async def execute(self, stmt: Any, params: dict | None = None) -> Any:
        # SQLAlchemy Core statements stringify to their compiled SQL;
        # pick out whichever one this is via rough keyword match.
        s = str(stmt).lower()
        if "from messages" in s or "message" in s and "select" in s:
            return _SessionResult(self._messages)
        if "update sessions" in s or "skill_review_due" in s:
            # Capture the session id from the WHERE clause params if
            # SQLAlchemy surfaces them; otherwise record the sentinel.
            self._resets.append("reset")
            return _Result([])
        # Default: empty result keeps the agent's other branches
        # behaving as "nothing to do".
        return _Result([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _install_fake_app_session(
    monkeypatch: pytest.MonkeyPatch,
    messages: list[_FakeMessage],
) -> list[str]:
    """Patch ``async_session_factory`` in the agent module.

    Returns a list that the test can inspect to see how many times
    ``skill_review_due`` was reset.
    """
    resets: list[str] = []

    @asynccontextmanager
    async def _factory():
        yield _FakeAppSession(messages, resets)

    monkeypatch.setattr(sra, "async_session_factory", _factory)
    return resets


# ---------------------------------------------------------------------------
# Helpers — tiny factories for conversation + LLM payloads
# ---------------------------------------------------------------------------


def _conversation_messages() -> list[_FakeMessage]:
    """A short but non-empty conversation the LLM can "analyse"."""
    return [
        _FakeMessage(role="user", content="帮我查一下 postgres 慢查询"),
        _FakeMessage(role="assistant", content="好的，先看 pg_stat_statements ..."),
        _FakeMessage(role="user", content="然后呢？"),
        _FakeMessage(role="assistant", content="再结合 explain analyze ..."),
    ]


def _skill_suggestion_payload(
    name: str = "postgres-slow-query-triage",
    *,
    extra: list[dict[str, Any]] | None = None,
) -> str:
    """Render an LLM JSON output with one good skill suggestion."""
    skills = [
        {
            "name": name,
            "description": "Diagnose slow PostgreSQL queries step by step.",
            "skill_prompt": (
                "你是 Postgres 慢查询分析专家。\n"
                "1. 读取 pg_stat_statements 中的 Top-N 慢查询。\n"
                "2. 对每条执行 EXPLAIN ANALYZE。\n"
                "3. 给出索引建议或重写方案。\n"
                "工具：read_file, execute_sql。\n"
                "输出：markdown 报告。"
            ),
            "category": "database",
            "tags": ["postgresql", "performance"],
        }
    ]
    if extra:
        skills.extend(extra)
    return json.dumps({"skills": skills, "summary": "ok"}, ensure_ascii=False)


def _run(coro):
    return asyncio.run(coro)


def _make_agent(
    db: _FakeDB,
    tmp_path: Path,
    llm: _FakeLLM,
) -> SkillReviewAgent:
    store = SkillCandidateStore(
        db_factory=db.factory(),
        skills_root_dir=tmp_path,
    )
    return SkillReviewAgent(model=llm, candidate_store=store)


# ---------------------------------------------------------------------------
# Tests — happy path: single suggestion becomes one candidate row
# ---------------------------------------------------------------------------


def test_review_creates_skill_candidate_proposed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recognised skill pattern yields one ``skill_candidates`` row
    with ``status='proposed'`` and ``proposal_source='skill_review_agent'``.
    """
    resets = _install_fake_app_session(monkeypatch, _conversation_messages())
    db = _FakeDB()
    llm = _FakeLLM(content=_skill_suggestion_payload("pg-triage"))
    agent = _make_agent(db, tmp_path, llm)

    result = _run(agent.review(session_id="session-A"))

    assert result["skills_proposed"] == 1
    assert len(db.skill_candidates) == 1
    row = next(iter(db.skill_candidates.values()))
    assert row.kind == "skill"
    assert row.name == "pg-triage"
    assert row.status == "proposed"
    assert row.proposal_source == PROPOSAL_SOURCE == "skill_review_agent"
    # Flag was reset so downstream review schedulers don't re-fire.
    assert resets, "skill_review_due flag should be reset after review"


# ---------------------------------------------------------------------------
# Tests — R-3.10: no direct activation (tools table untouched + no main SKILL.md)
# ---------------------------------------------------------------------------


def test_review_does_not_activate_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-3.10: no row inserted into ``tools`` and no SKILL.md in the
    main ``data/skills/`` tree — only under ``.candidate/``.
    """
    resets = _install_fake_app_session(monkeypatch, _conversation_messages())
    db = _FakeDB()
    llm = _FakeLLM(content=_skill_suggestion_payload("docker-log-analysis"))
    agent = _make_agent(db, tmp_path, llm)

    result = _run(agent.review(session_id="session-B"))

    assert result["skills_proposed"] == 1

    # No activation: tools table never written to.
    assert db.tools == {}, "propose-only path must NOT insert into tools"

    # Candidate SKILL.md lives under .candidate/, not the main tree.
    candidate_md = tmp_path / ".candidate" / "docker-log-analysis" / "SKILL.md"
    assert candidate_md.exists()
    body = candidate_md.read_text(encoding="utf-8")
    assert "status: candidate" in body

    # Main data/skills/<name>/ is untouched.
    main_md = tmp_path / "docker-log-analysis" / "SKILL.md"
    assert not main_md.exists(), (
        "R-3.10: main data/skills/ must NOT receive a new skill from review"
    )
    # Also assert no stray SKILL.md anywhere else under tmp_path except
    # under .candidate/.
    stray = [
        p
        for p in tmp_path.rglob("SKILL.md")
        if ".candidate" not in p.parts
    ]
    assert stray == [], f"unexpected SKILL.md outside .candidate/: {stray}"
    assert resets  # flag reset on completion


# ---------------------------------------------------------------------------
# Tests — empty LLM output is a clean no-op with flag reset
# ---------------------------------------------------------------------------


def test_review_no_patterns_identified_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resets = _install_fake_app_session(monkeypatch, _conversation_messages())
    db = _FakeDB()
    llm = _FakeLLM(content=json.dumps({"skills": [], "summary": "nothing"}))
    agent = _make_agent(db, tmp_path, llm)

    result = _run(agent.review(session_id="session-C"))

    assert result["skills_proposed"] == 0
    assert result["summary"] == "no patterns identified"
    assert db.skill_candidates == {}
    assert db.tools == {}
    assert resets, "flag still reset even when no candidates emitted"


def test_review_no_messages_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resets = _install_fake_app_session(monkeypatch, [])  # empty session
    db = _FakeDB()
    llm = _FakeLLM(content=_skill_suggestion_payload())
    agent = _make_agent(db, tmp_path, llm)

    result = _run(agent.review(session_id="session-D"))

    assert result["skills_proposed"] == 0
    assert db.skill_candidates == {}
    # LLM never invoked — no messages to analyse.
    assert llm.calls == []
    assert resets, "empty session still resets the flag"


# ---------------------------------------------------------------------------
# Tests — suggestions missing required fields / too-short prompt are dropped
# ---------------------------------------------------------------------------


def test_review_drops_suggestion_missing_required_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_app_session(monkeypatch, _conversation_messages())
    db = _FakeDB()
    # Two suggestions: one valid, one missing ``description``.
    bad = {
        "name": "nameless",
        # description intentionally missing
        "skill_prompt": "x" * 200,
        "tags": [],
    }
    llm = _FakeLLM(content=_skill_suggestion_payload("good-one", extra=[bad]))
    agent = _make_agent(db, tmp_path, llm)

    result = _run(agent.review(session_id="session-E"))

    assert result["skills_proposed"] == 1
    assert list(db.skill_candidates.values())[0].name == "good-one"


def test_review_drops_suggestion_with_too_short_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_app_session(monkeypatch, _conversation_messages())
    db = _FakeDB()
    # Only suggestion has a ``skill_prompt`` that's too short to pass
    # the candidate store schema. Agent should drop it before propose
    # and return zero proposals without raising.
    llm = _FakeLLM(
        content=json.dumps(
            {
                "skills": [
                    {
                        "name": "too-short",
                        "description": "desc",
                        "skill_prompt": "too short",
                        "tags": [],
                    }
                ],
                "summary": "ok",
            }
        )
    )
    agent = _make_agent(db, tmp_path, llm)

    result = _run(agent.review(session_id="session-F"))

    assert result["skills_proposed"] == 0
    assert db.skill_candidates == {}


# ---------------------------------------------------------------------------
# Tests — a failing propose doesn't abort the batch
# ---------------------------------------------------------------------------


def test_review_continues_when_one_propose_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_app_session(monkeypatch, _conversation_messages())
    db = _FakeDB()

    class _FlakyStore(SkillCandidateStore):
        """Raises on the first propose, succeeds on subsequent calls."""

        def __init__(self, **kw: Any) -> None:
            super().__init__(**kw)
            self.calls = 0

        async def propose(self, proposal, *, proposal_source="reflection_worker"):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated DB error on first candidate")
            return await super().propose(
                proposal, proposal_source=proposal_source
            )

    flaky = _FlakyStore(db_factory=db.factory(), skills_root_dir=tmp_path)
    llm = _FakeLLM(
        content=_skill_suggestion_payload(
            "first-skill",
            extra=[
                {
                    "name": "second-skill",
                    "description": "another diagnosable pattern",
                    "skill_prompt": (
                        "你是第二个技能。"
                        "执行 A；然后 B；最后 C。"
                        "工具：read_file。"
                        "输出：报告。"
                    ) * 2,
                    "tags": ["ops"],
                }
            ],
        )
    )
    agent = SkillReviewAgent(model=llm, candidate_store=flaky)

    result = _run(agent.review(session_id="session-G"))

    # Exactly one candidate landed in the DB (the second suggestion).
    assert result["skills_proposed"] == 1
    assert len(db.skill_candidates) == 1
    assert next(iter(db.skill_candidates.values())).name == "second-skill"
    assert flaky.calls == 2


# ---------------------------------------------------------------------------
# Tests — agent handles code-fenced LLM output
# ---------------------------------------------------------------------------


def test_review_parses_llm_response_with_code_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_app_session(monkeypatch, _conversation_messages())
    db = _FakeDB()
    payload = _skill_suggestion_payload("fenced-skill")
    fenced = f"```json\n{payload}\n```"
    llm = _FakeLLM(content=fenced)
    agent = _make_agent(db, tmp_path, llm)

    result = _run(agent.review(session_id="session-H"))

    assert result["skills_proposed"] == 1
    assert next(iter(db.skill_candidates.values())).name == "fenced-skill"
