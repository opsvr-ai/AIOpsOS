"""Unit tests for :class:`Promoter` rollback — task 23.3.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23.3
(Phase L — Promoter + Rollback).

**Validates: Requirements 3.9, 3.19**

Scope:

* ``Promoter.rollback_prompt`` flips the current active ``status`` to
  ``retired`` and the previous active row back to ``active`` in a
  **single** DB transaction (R-3.19) and publishes one Kafka event
  (R-3.9 + R-3.19).
* ``Promoter.rollback`` (skill) marks the current ``skill_versions``
  row retired, restores the previous one, restores the on-disk
  ``SKILL.md``, and invokes ``tool_manager.invalidate_cache`` + a
  Kafka event.
* Rollback is idempotent: when no active version exists, or when no
  previous active version exists, the call is a no-op with
  ``RollbackResult.ok is False`` and no Kafka publish.

These are pure unit tests — they use an in-memory fake DB session +
a recording Kafka producer; no Postgres, no broker required. Design
of the fake mirrors ``tests/evolution/test_candidate_store.py`` so
the style stays familiar.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.services.evolution.promoter import Promoter, RollbackResult


# ---------------------------------------------------------------------------
# In-memory fake DB
# ---------------------------------------------------------------------------


@dataclass
class _SkillCandidateRow:
    id: uuid.UUID
    name: str
    status: str


@dataclass
class _SkillVersionRow:
    id: uuid.UUID
    skill_name: str
    candidate_id: uuid.UUID | None
    skill_prompt: str
    activated_at: datetime | None
    retired_at: datetime | None


@dataclass
class _PromptVersionRow:
    id: uuid.UUID
    sub_agent_name: str
    system_prompt: str
    status: str
    activated_at: datetime | None
    retired_at: datetime | None


class _Row:
    """Shape-only holder compatible with ``_row_to_dict`` attribute scrape."""

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
    """In-memory tables used by the Promoter.

    * ``prompt_versions``  — ``sub_agent_prompt_versions``
    * ``skill_versions``   — ``skill_versions``
    * ``skill_candidates`` — cascaded-retire target for skill rollback
    """

    prompt_versions: dict[uuid.UUID, _PromptVersionRow] = field(default_factory=dict)
    skill_versions: dict[uuid.UUID, _SkillVersionRow] = field(default_factory=dict)
    skill_candidates: dict[uuid.UUID, _SkillCandidateRow] = field(default_factory=dict)

    # Observability: count commits so tests can assert single-transaction
    # semantics (R-3.19).
    commit_count: int = 0
    # Track per-session writes to prove both prompt updates landed in the
    # same session.
    session_call_log: list[tuple[int, str]] = field(default_factory=list)
    _session_seq: int = 0

    def factory(self):
        db = self

        @asynccontextmanager
        async def _factory():
            db._session_seq += 1
            session = _FakeSession(db, session_id=db._session_seq)
            try:
                yield session
            finally:
                # Session closed implicitly; no cleanup needed.
                pass

        return _factory


class _FakeSession:
    """Minimal SQL dispatcher for the exact statements the Promoter issues."""

    def __init__(self, db: _FakeDB, *, session_id: int) -> None:
        self._db = db
        self._id = session_id

    async def execute(
        self, stmt: Any, params: dict[str, Any] | None = None
    ) -> _Result:
        sql = " ".join(str(stmt).split()).lower()
        params = params or {}
        self._db.session_call_log.append((self._id, sql[:80]))

        # ---------- reads -------------------------------------------------
        if sql.startswith(
            "select id, sub_agent_name, system_prompt, status, activated_at, retired_at from sub_agent_prompt_versions"
        ):
            return self._read_prompt_versions(sql, params)
        if sql.startswith(
            "select id, skill_name, candidate_id, skill_prompt, activated_at, retired_at from skill_versions"
        ):
            return self._read_skill_versions(sql, params)

        # ---------- writes ------------------------------------------------
        if "update sub_agent_prompt_versions" in sql:
            return self._write_prompt_version(sql, params)
        if "update skill_versions" in sql:
            return self._write_skill_version(sql, params)
        if "update skill_candidates" in sql:
            return self._write_skill_candidate(sql, params)

        return _Result([])

    async def commit(self) -> None:
        self._db.commit_count += 1
        self._db.session_call_log.append((self._id, "COMMIT"))

    # -- reads --------------------------------------------------------------

    def _read_prompt_versions(
        self, sql: str, params: dict[str, Any]
    ) -> _Result:
        name = str(params.get("name", ""))
        # get-active branch
        if "status = 'active'" in sql and "limit 1" in sql:
            rows = [
                r
                for r in self._db.prompt_versions.values()
                if r.sub_agent_name == name and r.status == "active"
            ]
            return _Result(_wrap_prompt_rows(rows[:1]))
        # get-previous branch
        if "activated_at is not null" in sql and "id <> :exclude_id" in sql:
            exclude_id = params.get("exclude_id")
            candidates = [
                r
                for r in self._db.prompt_versions.values()
                if r.sub_agent_name == name
                and r.activated_at is not None
                and r.id != exclude_id
            ]
            candidates.sort(
                key=lambda r: r.activated_at or datetime.min, reverse=True
            )
            return _Result(_wrap_prompt_rows(candidates[:1]))
        return _Result([])

    def _read_skill_versions(
        self, sql: str, params: dict[str, Any]
    ) -> _Result:
        name = str(params.get("name", ""))
        # active branch: retired_at IS NULL + activated_at IS NOT NULL
        if "retired_at is null" in sql and "id <> :exclude_id" not in sql:
            matches = [
                r
                for r in self._db.skill_versions.values()
                if r.skill_name == name
                and r.retired_at is None
                and r.activated_at is not None
            ]
            matches.sort(
                key=lambda r: r.activated_at or datetime.min, reverse=True
            )
            return _Result(_wrap_skill_rows(matches[:1]))
        # previous branch
        if "id <> :exclude_id" in sql:
            exclude_id = params.get("exclude_id")
            matches = [
                r
                for r in self._db.skill_versions.values()
                if r.skill_name == name
                and r.id != exclude_id
                and r.retired_at is not None
                and r.activated_at is not None
            ]
            matches.sort(
                key=lambda r: r.activated_at or datetime.min, reverse=True
            )
            return _Result(_wrap_skill_rows(matches[:1]))
        return _Result([])

    # -- writes -------------------------------------------------------------

    def _write_prompt_version(
        self, sql: str, params: dict[str, Any]
    ) -> _Result:
        row_id = params.get("id")
        row = self._db.prompt_versions.get(row_id)
        if row is None:
            return _Result([])
        if "status = 'retired'" in sql:
            # Only flips if currently active (matches WHERE guard).
            if row.status == "active":
                row.status = "retired"
                row.retired_at = datetime.now(UTC)
        elif "status = 'active'" in sql:
            row.status = "active"
            row.activated_at = datetime.now(UTC)
            row.retired_at = None
        return _Result([])

    def _write_skill_version(
        self, sql: str, params: dict[str, Any]
    ) -> _Result:
        row_id = params.get("id")
        row = self._db.skill_versions.get(row_id)
        if row is None:
            return _Result([])
        if "retired_at = now()" in sql and "retired_at is null" in sql:
            # retire-current branch, guarded on retired_at IS NULL
            if row.retired_at is None:
                row.retired_at = datetime.now(UTC)
        elif "retired_at = null" in sql and "activated_at = now()" in sql:
            row.retired_at = None
            row.activated_at = datetime.now(UTC)
        return _Result([])

    def _write_skill_candidate(
        self, sql: str, params: dict[str, Any]
    ) -> _Result:
        row_id = params.get("id")
        row = self._db.skill_candidates.get(row_id)
        if row is None:
            return _Result([])
        if "status = 'retired'" in sql:
            row.status = "retired"
        return _Result([])


def _wrap_prompt_rows(rows: list[_PromptVersionRow]) -> list[_Row]:
    return [
        _Row(
            id=r.id,
            sub_agent_name=r.sub_agent_name,
            system_prompt=r.system_prompt,
            status=r.status,
            activated_at=r.activated_at,
            retired_at=r.retired_at,
        )
        for r in rows
    ]


def _wrap_skill_rows(rows: list[_SkillVersionRow]) -> list[_Row]:
    return [
        _Row(
            id=r.id,
            skill_name=r.skill_name,
            candidate_id=r.candidate_id,
            skill_prompt=r.skill_prompt,
            activated_at=r.activated_at,
            retired_at=r.retired_at,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Fake Kafka producer — records every send_and_wait call
# ---------------------------------------------------------------------------


class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes]] = []

    async def send_and_wait(self, topic: str, value: Any, **kwargs: Any) -> None:
        # ``kwargs`` absorbs ``key=`` / ``headers=`` variants so we stay
        # compatible with any future send-site tweaks.
        self.sent.append((topic, value))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _mk_prompt_row(
    *,
    sub_agent_name: str,
    prompt: str,
    status: str,
    activated_at: datetime | None = None,
    retired_at: datetime | None = None,
) -> _PromptVersionRow:
    return _PromptVersionRow(
        id=uuid.uuid4(),
        sub_agent_name=sub_agent_name,
        system_prompt=prompt,
        status=status,
        activated_at=activated_at,
        retired_at=retired_at,
    )


def _mk_skill_version(
    *,
    skill_name: str,
    prompt: str,
    activated_at: datetime | None = None,
    retired_at: datetime | None = None,
    candidate_id: uuid.UUID | None = None,
) -> _SkillVersionRow:
    return _SkillVersionRow(
        id=uuid.uuid4(),
        skill_name=skill_name,
        candidate_id=candidate_id,
        skill_prompt=prompt,
        activated_at=activated_at,
        retired_at=retired_at,
    )


def _make_promoter(
    db: _FakeDB,
    *,
    producer: _FakeProducer | None = None,
    skills_root: Path | None = None,
) -> tuple[Promoter, _FakeProducer, list[int]]:
    """Construct a Promoter wired against fakes.

    The third element of the tuple is a mutable list that records
    ``tool_manager.invalidate_cache`` invocations — useful for the
    skill-rollback assertions.
    """
    prod = producer or _FakeProducer()
    invalidate_calls: list[int] = []

    async def _fake_invalidate() -> None:
        invalidate_calls.append(len(invalidate_calls) + 1)

    promoter = Promoter(
        db_factory=db.factory(),
        producer=prod,
        skills_root_dir=skills_root,
        tool_manager_invalidate=_fake_invalidate,
    )
    return promoter, prod, invalidate_calls


# ---------------------------------------------------------------------------
# rollback_prompt — happy path
# ---------------------------------------------------------------------------


def test_rollback_prompt_transitions_in_single_transaction() -> None:
    """R-3.19: both UPDATEs run in one session and land before the event."""
    now = datetime.now(UTC)
    db = _FakeDB()
    # Previous active row (the one we expect to restore). It was
    # activated earlier and has since been retired.
    prev = _mk_prompt_row(
        sub_agent_name="ops",
        prompt="PREV",
        status="retired",
        activated_at=now - timedelta(hours=2),
        retired_at=now - timedelta(minutes=30),
    )
    curr = _mk_prompt_row(
        sub_agent_name="ops",
        prompt="CURR",
        status="active",
        activated_at=now - timedelta(minutes=30),
    )
    db.prompt_versions[prev.id] = prev
    db.prompt_versions[curr.id] = curr

    promoter, producer, _invalidate = _make_promoter(db)

    result = _run(promoter.rollback_prompt("ops"))

    assert isinstance(result, RollbackResult)
    assert result.ok is True
    assert result.kind == "prompt_patch"
    assert result.name == "ops"
    assert result.retired_version_id == curr.id
    assert result.restored_version_id == prev.id

    # DB state reflects the swap.
    assert db.prompt_versions[curr.id].status == "retired"
    assert db.prompt_versions[curr.id].retired_at is not None
    assert db.prompt_versions[prev.id].status == "active"
    assert db.prompt_versions[prev.id].retired_at is None
    assert db.prompt_versions[prev.id].activated_at is not None

    # Single-transaction guarantee (R-3.19): exactly one commit.
    assert db.commit_count == 1

    # Every write in the log shares one session id.
    write_sessions = {
        sid
        for sid, sql in db.session_call_log
        if sql.startswith("update ") or sql == "COMMIT"
    }
    assert len(write_sessions) == 1, (
        f"expected a single session for all writes, got {write_sessions}"
    )

    # Kafka event emitted with the correct shape.
    assert result.event_published is True
    assert len(producer.sent) == 1
    topic, payload_bytes = producer.sent[0]
    assert topic == "ops.agent.promotion"

    import json as _json
    payload = _json.loads(payload_bytes.decode("utf-8"))
    # PromptReloader-compatible fields — ensure replicas converge.
    assert payload["kind"] == "prompt_patch"
    assert payload["target_ref"] == "ops"
    assert payload["new_version_id"] == str(prev.id)
    assert payload["to_status"] == "active"
    assert payload["event_id"].startswith("rollback-prompt-")
    # Rollback-specific discriminator for auditors.
    assert payload["event_kind"] == "rollback"
    assert payload["sub_agent_name"] == "ops"
    assert payload["active_version_id"] == str(prev.id)
    assert payload["retired_version_id"] == str(curr.id)


def test_rollback_prompt_with_no_active_is_idempotent_noop() -> None:
    """Calling rollback when nothing is active is an ok=False no-op."""
    db = _FakeDB()
    promoter, producer, _invalidate = _make_promoter(db)

    result = _run(promoter.rollback_prompt("monitor"))

    assert result.ok is False
    assert result.retired_version_id is None
    assert result.restored_version_id is None
    assert db.commit_count == 0
    assert producer.sent == []


def test_rollback_prompt_with_no_previous_is_idempotent_noop() -> None:
    """An active row with no history of prior actives is a no-op."""
    now = datetime.now(UTC)
    db = _FakeDB()
    # Only one row; it's active and has never been anything else.
    curr = _mk_prompt_row(
        sub_agent_name="monitor",
        prompt="ONLY",
        status="active",
        activated_at=now - timedelta(minutes=10),
    )
    db.prompt_versions[curr.id] = curr
    promoter, producer, _invalidate = _make_promoter(db)

    result = _run(promoter.rollback_prompt("monitor"))

    assert result.ok is False
    assert result.retired_version_id == curr.id
    assert result.restored_version_id is None
    # DB unchanged.
    assert db.prompt_versions[curr.id].status == "active"
    assert db.prompt_versions[curr.id].retired_at is None
    # No Kafka event, no commit.
    assert db.commit_count == 0
    assert producer.sent == []


def test_rollback_prompt_picks_most_recent_prior_active() -> None:
    """Previous-active resolution orders by ``activated_at DESC``."""
    now = datetime.now(UTC)
    db = _FakeDB()

    older = _mk_prompt_row(
        sub_agent_name="knowledge",
        prompt="OLDER",
        status="retired",
        activated_at=now - timedelta(days=3),
        retired_at=now - timedelta(days=2),
    )
    newer = _mk_prompt_row(
        sub_agent_name="knowledge",
        prompt="NEWER",
        status="retired",
        activated_at=now - timedelta(hours=6),
        retired_at=now - timedelta(hours=1),
    )
    curr = _mk_prompt_row(
        sub_agent_name="knowledge",
        prompt="CURR",
        status="active",
        activated_at=now - timedelta(minutes=1),
    )
    db.prompt_versions[older.id] = older
    db.prompt_versions[newer.id] = newer
    db.prompt_versions[curr.id] = curr

    promoter, _producer, _invalidate = _make_promoter(db)
    result = _run(promoter.rollback_prompt("knowledge"))

    assert result.ok is True
    assert result.restored_version_id == newer.id, (
        "should restore the most recently activated prior version"
    )


# ---------------------------------------------------------------------------
# rollback (skill) — happy path
# ---------------------------------------------------------------------------


def test_rollback_skill_restores_previous_and_marks_current_retired(
    tmp_path: Path,
) -> None:
    """R-3.9: skill rollback flips current to retired + restores prior.

    Also asserts the on-disk ``SKILL.md`` is restored and that
    ``tool_manager.invalidate_cache`` was invoked.
    """
    now = datetime.now(UTC)
    db = _FakeDB()

    candidate_row = _SkillCandidateRow(
        id=uuid.uuid4(), name="triage", status="active"
    )
    db.skill_candidates[candidate_row.id] = candidate_row

    prev = _mk_skill_version(
        skill_name="triage",
        prompt="previous-version-prompt",
        activated_at=now - timedelta(days=1),
        retired_at=now - timedelta(hours=3),
    )
    curr = _mk_skill_version(
        skill_name="triage",
        prompt="current-prompt",
        activated_at=now - timedelta(hours=3),
        candidate_id=candidate_row.id,
    )
    db.skill_versions[prev.id] = prev
    db.skill_versions[curr.id] = curr

    promoter, producer, invalidate_calls = _make_promoter(
        db, skills_root=tmp_path
    )
    result = _run(promoter.rollback("triage"))

    assert result.ok is True
    assert result.kind == "skill"
    assert result.retired_version_id == curr.id
    assert result.restored_version_id == prev.id

    # DB state: current retired, prev restored, matching candidate row
    # flipped to retired.
    assert db.skill_versions[curr.id].retired_at is not None
    assert db.skill_versions[prev.id].retired_at is None
    assert db.skill_versions[prev.id].activated_at is not None
    assert db.skill_candidates[candidate_row.id].status == "retired"

    # Single transaction (R-3.19).
    assert db.commit_count == 1

    # Filesystem restore: SKILL.md written under tmp skills dir with
    # the previous version's prompt.
    md_path = tmp_path / "triage" / "SKILL.md"
    assert md_path.exists()
    body = md_path.read_text(encoding="utf-8")
    assert "previous-version-prompt" in body
    assert "name: triage" in body

    # tool_manager.invalidate_cache fired once.
    assert len(invalidate_calls) == 1

    # Kafka event emitted.
    assert result.event_published is True
    assert len(producer.sent) == 1
    topic, payload_bytes = producer.sent[0]
    assert topic == "ops.agent.promotion"

    import json as _json
    payload = _json.loads(payload_bytes.decode("utf-8"))
    assert payload["kind"] == "skill"
    assert payload["event_kind"] == "rollback"
    assert payload["target_ref"] == "triage"
    assert payload["skill_name"] == "triage"
    assert payload["active_version_id"] == str(prev.id)
    assert payload["retired_version_id"] == str(curr.id)


def test_rollback_skill_with_no_previous_is_noop(tmp_path: Path) -> None:
    """Only one skill version in history → nothing to restore."""
    now = datetime.now(UTC)
    db = _FakeDB()
    curr = _mk_skill_version(
        skill_name="single",
        prompt="only",
        activated_at=now - timedelta(minutes=15),
    )
    db.skill_versions[curr.id] = curr

    promoter, producer, invalidate_calls = _make_promoter(
        db, skills_root=tmp_path
    )
    result = _run(promoter.rollback("single"))

    assert result.ok is False
    assert result.retired_version_id == curr.id
    assert result.restored_version_id is None

    # DB untouched — current still active, no commit.
    assert db.skill_versions[curr.id].retired_at is None
    assert db.commit_count == 0

    # No fs write.
    assert not (tmp_path / "single" / "SKILL.md").exists()
    # No cache invalidation, no kafka publish.
    assert invalidate_calls == []
    assert producer.sent == []


def test_rollback_skill_with_no_active_is_noop(tmp_path: Path) -> None:
    """Skill has no active version — nothing to roll back."""
    db = _FakeDB()
    promoter, producer, invalidate_calls = _make_promoter(
        db, skills_root=tmp_path
    )
    result = _run(promoter.rollback("unknown"))

    assert result.ok is False
    assert result.retired_version_id is None
    assert result.restored_version_id is None
    assert db.commit_count == 0
    assert producer.sent == []
    assert invalidate_calls == []
