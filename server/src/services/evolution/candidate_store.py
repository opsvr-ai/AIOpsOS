"""Persistence layer for candidate proposals — task 21.4.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 21.4
(Phase J — ReflectionWorker). Covers:

* R-3.2 — skill / prompt_patch / tool_config candidates all flow through
  the same state machine (``proposed → shadow → ab → active → retired``)
  with a shared rejection sink.
* R-3.3 — ``skill`` candidates land under ``data/skills/.candidate/``
  and are never materialised into the main ``data/skills/`` tree at
  this stage; ``prompt_patch`` candidates live exclusively in
  ``sub_agent_prompt_versions``; ``tool_config`` candidates store the
  patch in a JSONB blob on ``skill_candidates``.
* R-3.13 — a ``tool_config`` candidate records a pre-patch snapshot of
  the target tool's ``config`` at propose time. The Promoter reads
  this snapshot back during rollback so a botched activation can be
  restored exactly.

Design goals:

* Single class (:class:`SkillCandidateStore`) so the
  :class:`~src.services.evolution.reflection_logic` module, the
  :class:`~src.agent.sub_agents.skill_review_agent.SkillReviewAgent`
  (task 21.6) and the ``Promoter`` (task 23.x) all route through one
  place.
* No new import-time side effects (no DB session opened at module
  load) so the class stays cheap to construct in both tests and
  Celery workers.
* State machine lives in the store, not the Promoter, so any caller
  that flips ``status`` goes through the same transition check.
* The original :mod:`reflection_logic` free functions stay as thin
  wrappers that delegate here — existing tests that import
  ``persist_candidate_proposal`` / ``_persist_skill_candidate`` keep
  working while new code uses the class.

The store deliberately speaks raw SQL (matching the reflection_logic
helpers it replaced) so it's compatible with the in-memory DB fakes
used across the test tree. A future cleanup can switch to SQLAlchemy
ORM inserts once ``skill_candidates`` columns stop drifting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from src.services.evolution.reflection_logic import (
        CandidateProposal,
        PersistedCandidate,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public constants + exceptions
# ---------------------------------------------------------------------------


# R-3.4: the legal transition graph. Keys are the *source* status;
# values are the set of statuses that source may transition to. Any
# transition not listed here raises :class:`InvalidStateTransition`.
#
# Notes on corner cases:
#
# * ``rejected`` and ``retired`` are terminal. No outgoing edges.
# * ``proposed → rejected`` and ``proposed → shadow`` are the only
#   legal moves out of ``proposed``. The Promoter never promotes
#   ``proposed`` straight to ``active`` — that bypasses evaluation.
# * Admin-override "force promote" flows should still go through
#   ``proposed → shadow → ab → active`` one step at a time; batching
#   would mask an evaluator failure.
STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"shadow", "rejected"}),
    "shadow": frozenset({"ab", "rejected", "retired"}),
    "ab": frozenset({"active", "rejected", "retired"}),
    "active": frozenset({"retired"}),
    "retired": frozenset(),
    "rejected": frozenset(),
}

# All known statuses. Exposed for test parametrisation and so the
# Promoter can sanity-check inputs before calling ``update_status``.
ALL_STATUSES: frozenset[str] = frozenset(STATE_TRANSITIONS.keys())


# Tag sentinel keys (R-3.13). Kept as module constants so the Promoter
# (task 23.x) can read back the patch + pre-snapshot by the exact same
# name the store wrote them under.
TAG_KEY_TOOL_CONFIG_PATCH = "_tool_config_patch"
TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT = "_tool_config_pre_snapshot"
TAG_KEY_RATIONALE = "_rationale"
TAG_KEY_EXPECTED_IMPROVEMENT = "_expected_improvement"


class InvalidStateTransition(ValueError):
    """Raised when :meth:`SkillCandidateStore.update_status` is asked to
    move a candidate along an edge not in :data:`STATE_TRANSITIONS`.

    Inherits from :class:`ValueError` so callers who just want a
    "something's wrong with the input" handler don't need to pivot
    their existing except clauses, but dedicated handlers can catch
    this subclass for reporting / metrics.
    """

    def __init__(self, current: str, new: str) -> None:
        self.current = current
        self.new = new
        super().__init__(
            f"invalid state transition: {current!r} -> {new!r}"
        )


# ---------------------------------------------------------------------------
# Row dataclass — uniform view across ``skill_candidates`` and
# ``sub_agent_prompt_versions`` so callers don't need to know which
# table a candidate lives in.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """Store-side view of a candidate row.

    * ``table`` is ``"skill_candidates"`` for ``kind`` in
      ``{skill, tool_config}`` and ``"sub_agent_prompt_versions"`` for
      ``kind=prompt_patch``. Callers that need the table name to run
      further SQL can look it up here instead of rederiving from
      ``kind``.
    * ``tags`` / ``data`` carry the JSONB blob attached to the row.
      For ``tool_config`` this is where :data:`TAG_KEY_TOOL_CONFIG_PATCH`
      and :data:`TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT` live.
    """

    id: uuid.UUID
    kind: str  # skill | prompt_patch | tool_config
    name: str
    status: str
    table: str
    target_ref: str | None = None
    tags: list[Any] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers — name sanitisation + SKILL.md rendering (copied from
# reflection_logic so the store doesn't need to import back into its
# caller). The reflection_logic free functions still exist as thin
# wrappers and re-export the same behaviour.
# ---------------------------------------------------------------------------


_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.\-]")


def _safe_candidate_dirname(name: str) -> str:
    """Return a filesystem-safe directory name for a candidate.

    Strips path separators + control chars so a malicious LLM can't
    escape ``.candidate/`` via ``../etc/passwd``-style names. Matches
    the helper of the same name in ``reflection_logic`` exactly so the
    two stay in lock-step.
    """
    cleaned = _NAME_SAFE_RE.sub("_", name.strip())
    cleaned = cleaned.strip(".") or "candidate"
    return cleaned[:128]


def _default_skills_root_dir() -> Path:
    """Resolve the main ``data/skills/`` directory (lazy import)."""
    try:
        from src.services.skill_sync import SKILLS_DIR  # type: ignore

        return Path(SKILLS_DIR)
    except Exception:
        return Path(__file__).resolve().parents[3] / "data" / "skills"


def _default_db_factory() -> Any:
    """Resolve the shared async session factory. Lazy import so tests
    can instantiate the store without a configured DB engine."""
    from src.models.base import async_session_factory

    return async_session_factory


def _render_candidate_skill_md(
    name: str, proposal: "CandidateProposal"
) -> str:
    """Render the SKILL.md body for a skill candidate.

    Minimal valid DeepAgents skill frontmatter + the LLM-generated
    prompt. No ``yaml`` dependency here — the frontmatter is
    hand-written and only escapes what a skill name / description can
    legally contain (one-line strings).
    """
    data = proposal.data
    desc = (data.get("description") or "").replace("\n", " ").strip()
    tags = data.get("tags") or []
    tag_line = ", ".join(str(t) for t in tags)
    body = (data.get("skill_prompt") or "").strip()
    return (
        f"---\n"
        f"name: {name}\n"
        f"description: {desc}\n"
        f"status: candidate\n"
        f"cluster: {proposal.cluster_name}\n"
        f"tags: [{tag_line}]\n"
        f"---\n\n"
        f"{body}\n"
    )


# ---------------------------------------------------------------------------
# SkillCandidateStore
# ---------------------------------------------------------------------------


class SkillCandidateStore:
    """Shared persistence + lifecycle surface for candidate proposals.

    Construction is cheap — no DB connection is opened until a method
    is called. Two knobs are injectable for tests:

    * ``db_factory`` — async ctx manager yielding a session with
      ``.execute(stmt, params)`` + ``.commit()``. Defaults to the
      process-wide ``async_session_factory``.
    * ``skills_root_dir`` — filesystem root for skill artefacts.
      Defaults to :data:`src.services.skill_sync.SKILLS_DIR`.

    All public methods are ``async`` because the underlying DB calls
    are. No method caches state across calls, so the same instance
    can be shared across workers safely.
    """

    def __init__(
        self,
        *,
        db_factory: Any | None = None,
        skills_root_dir: Path | None = None,
    ) -> None:
        self._explicit_db_factory = db_factory
        self._skills_root = (
            Path(skills_root_dir)
            if skills_root_dir is not None
            else None
        )

    # ------------------------------------------------------------------
    # Resource accessors — resolved lazily so construction doesn't
    # need a live DB / filesystem.
    # ------------------------------------------------------------------

    @property
    def _db_factory(self) -> Any:
        if self._explicit_db_factory is not None:
            return self._explicit_db_factory
        return _default_db_factory()

    @property
    def skills_root_dir(self) -> Path:
        if self._skills_root is not None:
            return self._skills_root
        return _default_skills_root_dir()

    @asynccontextmanager
    async def _session(self):
        """Yield an async session via the configured factory.

        Small convenience so the call sites don't have to repeat the
        ``async with factory() as session`` dance. Works with the
        in-memory FakeDB too because the dance is identical.
        """
        async with self._db_factory() as session:
            yield session

    # ------------------------------------------------------------------
    # Public API — propose / get / list / update_status / snapshot
    # ------------------------------------------------------------------

    async def propose(
        self,
        proposal: "CandidateProposal",
        *,
        proposal_source: str = "reflection_worker",
    ) -> "PersistedCandidate":
        """Persist a :class:`CandidateProposal` in status ``proposed``.

        Routing mirrors R-3.2 / R-3.3:

        * ``kind="skill"``       → write SKILL.md under
          ``skills_root/.candidate/<name>/`` then INSERT into
          ``skill_candidates``.
        * ``kind="prompt_patch"``→ INSERT into
          ``sub_agent_prompt_versions`` with ``status='proposed'``. No
          FS artefact, no ``skill_candidates`` row.
        * ``kind="tool_config"`` → snapshot the current
          ``tools.config`` for the target tool (R-3.13), then INSERT
          into ``skill_candidates`` with the patch + the snapshot
          stored under the :data:`TAG_KEY_TOOL_CONFIG_PATCH` /
          :data:`TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT` sentinel tags.

        Args:
            proposal: the validated candidate to persist. Origin
                trajectories are copied into
                ``skill_candidates.origin_trajectory_ids`` when
                applicable.
            proposal_source: label stored in
                ``skill_candidates.proposal_source`` or
                ``sub_agent_prompt_versions.rationale``. The
                reflection worker uses ``"reflection_worker"``; the
                :class:`SkillReviewAgent` passes ``"skill_review_agent"``.

        Returns:
            :class:`PersistedCandidate` with the freshly-minted row id
            and artefact path (skill only).

        Raises:
            ValueError: for unknown ``proposal.kind``.
            sqlalchemy.exc.IntegrityError: on constraint violations
                (e.g. duplicate name inside the same worker run).
        """
        kind = proposal.kind
        if kind == "skill":
            return await self._persist_skill(
                proposal, proposal_source=proposal_source
            )
        if kind == "prompt_patch":
            return await self._persist_prompt_patch(
                proposal, proposal_source=proposal_source
            )
        if kind == "tool_config":
            return await self._persist_tool_config(
                proposal, proposal_source=proposal_source
            )
        raise ValueError(f"unknown candidate kind: {kind!r}")

    async def get(self, candidate_id: uuid.UUID) -> CandidateRow | None:
        """Look up one candidate row by id.

        Scans ``skill_candidates`` first, then
        ``sub_agent_prompt_versions``. Returns ``None`` if neither
        table holds the id. We never raise for "not found" — the
        Promoter treats None as "nothing to do".
        """
        async with self._session() as session:
            rows = await session.execute(
                text(
                    """
                    SELECT id, kind, name, status, target_ref, tags
                    FROM skill_candidates
                    WHERE id = :id
                    """
                ),
                {"id": candidate_id},
            )
            row = rows.first()
            if row is not None:
                tags = _coerce_tags(getattr(row, "tags", None))
                return CandidateRow(
                    id=_coerce_uuid(row.id),
                    kind=str(row.kind),
                    name=str(row.name),
                    status=str(row.status),
                    table="skill_candidates",
                    target_ref=(
                        str(row.target_ref)
                        if getattr(row, "target_ref", None)
                        else None
                    ),
                    tags=tags,
                    data={"tags": tags},
                )

            rows = await session.execute(
                text(
                    """
                    SELECT id, sub_agent_name, status, system_prompt
                    FROM sub_agent_prompt_versions
                    WHERE id = :id
                    """
                ),
                {"id": candidate_id},
            )
            row = rows.first()
            if row is not None:
                return CandidateRow(
                    id=_coerce_uuid(row.id),
                    kind="prompt_patch",
                    # ``name`` isn't tracked on the prompt-versions
                    # table; fall back to the sub_agent_name which is
                    # the effective dedup key.
                    name=str(row.sub_agent_name),
                    status=str(row.status),
                    table="sub_agent_prompt_versions",
                    target_ref=str(row.sub_agent_name),
                    tags=[],
                    data={"system_prompt": row.system_prompt},
                )

        return None

    async def list_by_status(self, status: str) -> list[CandidateRow]:
        """Return every candidate currently in ``status``.

        Queries both candidate tables — a ``status='shadow'`` query
        returns skill / tool_config shadow candidates from
        ``skill_candidates`` *and* prompt_patch shadow rows from
        ``sub_agent_prompt_versions``, all projected to the common
        :class:`CandidateRow` shape.

        Results are unordered. Callers that need a stable ordering
        should sort client-side — the DB layer leaves this free so we
        can add an index-friendly ORDER BY later without changing the
        Python contract.
        """
        if status not in ALL_STATUSES:
            # Unknown statuses never match — surfaced as empty list
            # rather than raising so the Promoter can poll a fixed
            # enum without guarding for drift.
            return []

        out: list[CandidateRow] = []
        async with self._session() as session:
            skill_rows = await session.execute(
                text(
                    """
                    SELECT id, kind, name, status, target_ref, tags
                    FROM skill_candidates
                    WHERE status = :status
                    """
                ),
                {"status": status},
            )
            for row in skill_rows.fetchall():
                tags = _coerce_tags(getattr(row, "tags", None))
                out.append(
                    CandidateRow(
                        id=_coerce_uuid(row.id),
                        kind=str(row.kind),
                        name=str(row.name),
                        status=str(row.status),
                        table="skill_candidates",
                        target_ref=(
                            str(row.target_ref)
                            if getattr(row, "target_ref", None)
                            else None
                        ),
                        tags=tags,
                        data={"tags": tags},
                    )
                )

            prompt_rows = await session.execute(
                text(
                    """
                    SELECT id, sub_agent_name, status, system_prompt
                    FROM sub_agent_prompt_versions
                    WHERE status = :status
                    """
                ),
                {"status": status},
            )
            for row in prompt_rows.fetchall():
                out.append(
                    CandidateRow(
                        id=_coerce_uuid(row.id),
                        kind="prompt_patch",
                        name=str(row.sub_agent_name),
                        status=str(row.status),
                        table="sub_agent_prompt_versions",
                        target_ref=str(row.sub_agent_name),
                        tags=[],
                        data={"system_prompt": row.system_prompt},
                    )
                )

        return out

    async def update_status(
        self, candidate_id: uuid.UUID, new_status: str
    ) -> None:
        """Move ``candidate_id`` to ``new_status`` enforcing R-3.4.

        Steps:

        1. Look up the current row (via :meth:`get`).
        2. Check the transition is allowed by
           :data:`STATE_TRANSITIONS`. Raise
           :class:`InvalidStateTransition` otherwise. Same-status
           writes are idempotent and return without touching the DB.
        3. Issue an UPDATE on the candidate's owning table, guarded
           by ``WHERE status = <current>`` so a concurrent writer
           can't silently overwrite a transition we didn't expect.

        Raises:
            LookupError: ``candidate_id`` doesn't exist in either
                candidate table.
            InvalidStateTransition: the requested edge is not in
                :data:`STATE_TRANSITIONS`.
        """
        if new_status not in ALL_STATUSES:
            raise InvalidStateTransition("<unknown>", new_status)

        current = await self.get(candidate_id)
        if current is None:
            raise LookupError(f"candidate {candidate_id} not found")

        if current.status == new_status:
            # Idempotent — no-op so replayed promotion events don't
            # bounce through the state machine twice.
            return

        allowed = STATE_TRANSITIONS.get(current.status, frozenset())
        if new_status not in allowed:
            raise InvalidStateTransition(current.status, new_status)

        async with self._session() as session:
            if current.table == "skill_candidates":
                await session.execute(
                    text(
                        """
                        UPDATE skill_candidates
                        SET status = :new_status,
                            updated_at = now()
                        WHERE id = :id AND status = :current_status
                        """
                    ),
                    {
                        "new_status": new_status,
                        "id": candidate_id,
                        "current_status": current.status,
                    },
                )
            else:  # sub_agent_prompt_versions
                await session.execute(
                    text(
                        """
                        UPDATE sub_agent_prompt_versions
                        SET status = :new_status
                        WHERE id = :id AND status = :current_status
                        """
                    ),
                    {
                        "new_status": new_status,
                        "id": candidate_id,
                        "current_status": current.status,
                    },
                )
            await session.commit()

    async def snapshot_tool_config(self, tool_name: str) -> dict[str, Any]:
        """Return the current ``tools.config`` JSON for *tool_name*.

        R-3.13: the result is copied into the ``skill_candidates``
        row at propose time so the Promoter can restore the exact
        pre-activation state on rollback. Missing tools return
        ``{}`` rather than raising — a ``tool_config`` candidate
        for a freshly-registered tool with no prior config still
        needs a deterministic snapshot (the "empty" one).
        """
        async with self._session() as session:
            rows = await session.execute(
                text(
                    """
                    SELECT config
                    FROM tools
                    WHERE name = :name
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                ),
                {"name": tool_name},
            )
            row = rows.first()
        if row is None:
            return {}

        raw = getattr(row, "config", None)
        if raw is None:
            return {}
        if isinstance(raw, dict):
            # Deep-ish copy via JSON round-trip so the caller can't
            # mutate the DB row object. For pure JSON blobs this is
            # both correct and cheap.
            try:
                return json.loads(json.dumps(raw, ensure_ascii=False))
            except (TypeError, ValueError):
                # Fall back to the raw dict if it contains something
                # exotic that survives asyncpg but not json. Worst
                # case, the caller gets a shared reference, which is
                # still better than raising on rollback snapshot.
                return dict(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    # ------------------------------------------------------------------
    # Per-kind writers (private)
    # ------------------------------------------------------------------

    async def _persist_skill(
        self,
        proposal: "CandidateProposal",
        *,
        proposal_source: str,
    ) -> "PersistedCandidate":
        """Write a skill candidate to DB + ``.candidate/`` SKILL.md.

        Layout — R-3.3:
        ``<skills_root>/.candidate/<safe_name>/SKILL.md``. The main
        skills directory is never written to here. The MD file is
        written *before* the DB row so a partial failure leaves an
        orphan file (sweepable by housekeeping) instead of an
        active-looking DB row with no on-disk content.
        """
        from src.services.evolution.reflection_logic import PersistedCandidate

        safe_name = _safe_candidate_dirname(proposal.name)
        candidate_root = self.skills_root_dir / ".candidate" / safe_name
        candidate_root.mkdir(parents=True, exist_ok=True)
        skill_md_path = candidate_root / "SKILL.md"
        content = _render_candidate_skill_md(safe_name, proposal)
        skill_md_path.write_text(content, encoding="utf-8")
        manifest_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        row_id = uuid.uuid4()
        data = proposal.data
        async with self._session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO skill_candidates (
                        id, name, proposal_source, origin_trajectory_ids,
                        status, skill_prompt, description, tags, tool_names,
                        manifest_sha256, kind, target_ref
                    ) VALUES (
                        :id, :name, :source, :origins,
                        'proposed', :prompt, :desc, CAST(:tags AS jsonb),
                        CAST(:tool_names AS jsonb), :manifest, 'skill', NULL
                    )
                    """
                ),
                {
                    "id": row_id,
                    "name": proposal.name,
                    "source": proposal_source,
                    "origins": proposal.origin_trajectory_ids,
                    "prompt": data.get("skill_prompt") or "",
                    "desc": data.get("description"),
                    "tags": json.dumps(
                        data.get("tags") or [], ensure_ascii=False
                    ),
                    "tool_names": json.dumps(
                        data.get("tool_names") or [], ensure_ascii=False
                    ),
                    "manifest": manifest_sha,
                },
            )
            await session.commit()

        logger.info(
            "candidate_store: persisted skill %r (row=%s, md=%s)",
            proposal.name,
            row_id,
            skill_md_path,
        )
        return PersistedCandidate(
            kind="skill",
            name=proposal.name,
            row_id=row_id,
            table="skill_candidates",
            artifact_path=skill_md_path,
        )

    async def _persist_tool_config(
        self,
        proposal: "CandidateProposal",
        *,
        proposal_source: str,
    ) -> "PersistedCandidate":
        """Write a tool_config candidate with its pre-patch snapshot (R-3.13).

        The ``tags`` JSONB column is a small dict-of-dicts list so the
        Promoter can locate the patch / snapshot by sentinel key
        without a schema migration. The snapshot call fires
        **before** the INSERT so a DB error midway leaves nothing to
        roll back (the INSERT never happened).
        """
        from src.services.evolution.reflection_logic import PersistedCandidate

        data = proposal.data
        tool_name = str(data.get("tool_name") or "")
        pre_snapshot = await self.snapshot_tool_config(tool_name) if tool_name else {}

        tags_payload: list[dict[str, Any]] = [
            {TAG_KEY_TOOL_CONFIG_PATCH: data.get("patch") or {}},
            {TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT: pre_snapshot},
            {TAG_KEY_RATIONALE: data.get("rationale") or ""},
            {TAG_KEY_EXPECTED_IMPROVEMENT: proposal.expected_improvement},
        ]

        row_id = uuid.uuid4()
        async with self._session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO skill_candidates (
                        id, name, proposal_source, origin_trajectory_ids,
                        status, skill_prompt, description, tags, tool_names,
                        kind, target_ref
                    ) VALUES (
                        :id, :name, :source, :origins,
                        'proposed', :prompt, :desc, CAST(:tags AS jsonb),
                        CAST(:tool_names AS jsonb), 'tool_config', :target
                    )
                    """
                ),
                {
                    "id": row_id,
                    "name": proposal.name,
                    "source": proposal_source,
                    "origins": proposal.origin_trajectory_ids,
                    "prompt": (
                        data.get("rationale") or proposal.expected_improvement
                    ),
                    "desc": (
                        data.get("rationale") or proposal.expected_improvement
                    ),
                    "tags": json.dumps(tags_payload, ensure_ascii=False),
                    "tool_names": json.dumps(
                        [tool_name] if tool_name else [],
                        ensure_ascii=False,
                    ),
                    "target": tool_name or None,
                },
            )
            await session.commit()

        logger.info(
            "candidate_store: persisted tool_config %r (row=%s, target=%s)",
            proposal.name,
            row_id,
            tool_name or "<unknown>",
        )
        return PersistedCandidate(
            kind="tool_config",
            name=proposal.name,
            row_id=row_id,
            table="skill_candidates",
            artifact_path=None,
        )

    async def _persist_prompt_patch(
        self,
        proposal: "CandidateProposal",
        *,
        proposal_source: str,
    ) -> "PersistedCandidate":
        """Write a prompt_patch candidate to ``sub_agent_prompt_versions``.

        Per R-3.3 no ``skill_candidates`` row is created — the
        prompt-versions table is the source of truth for sub-agent
        prompts. ``manifest_sha256`` over the new prompt lets the
        evaluator de-duplicate identical proposals across runs.
        """
        from src.services.evolution.reflection_logic import PersistedCandidate

        data = proposal.data
        new_prompt = data.get("new_prompt") or ""
        manifest_sha = hashlib.sha256(new_prompt.encode("utf-8")).hexdigest()
        row_id = uuid.uuid4()

        async with self._session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO sub_agent_prompt_versions (
                        id, sub_agent_name, candidate_id, system_prompt,
                        rationale, status, parent_version_id, manifest_sha256
                    ) VALUES (
                        :id, :name, NULL, :prompt,
                        :rationale, 'proposed', NULL, :manifest
                    )
                    """
                ),
                {
                    "id": row_id,
                    "name": data.get("sub_agent_name"),
                    "prompt": new_prompt,
                    "rationale": (
                        data.get("rationale")
                        or f"{proposal_source}: {proposal.expected_improvement}"
                    ),
                    "manifest": manifest_sha,
                },
            )
            await session.commit()

        logger.info(
            "candidate_store: persisted prompt_patch for %r (row=%s)",
            data.get("sub_agent_name"),
            row_id,
        )
        return PersistedCandidate(
            kind="prompt_patch",
            name=proposal.name,
            row_id=row_id,
            table="sub_agent_prompt_versions",
            artifact_path=None,
        )


# ---------------------------------------------------------------------------
# Helpers — type coercion for in-memory fakes vs asyncpg rows
# ---------------------------------------------------------------------------


def _coerce_uuid(value: Any) -> uuid.UUID:
    """Accept either a :class:`uuid.UUID` or its string form.

    asyncpg hands back native UUIDs; the in-memory FakeDB in tests
    returns whichever form the caller stored. Normalising here keeps
    :class:`CandidateRow` strictly typed.
    """
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _coerce_tags(value: Any) -> list[Any]:
    """Normalise the ``tags`` JSONB column into a Python list.

    Postgres returns JSONB as the native python type; our in-memory
    fakes usually store the same thing. A ``str`` means the row was
    serialised (some fakes do this) — JSON-parse it. Anything else
    (None / scalar) degenerates to an empty list so callers never
    have to None-check.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


__all__ = [
    "ALL_STATUSES",
    "CandidateRow",
    "InvalidStateTransition",
    "SkillCandidateStore",
    "STATE_TRANSITIONS",
    "TAG_KEY_EXPECTED_IMPROVEMENT",
    "TAG_KEY_RATIONALE",
    "TAG_KEY_TOOL_CONFIG_PATCH",
    "TAG_KEY_TOOL_CONFIG_PRE_SNAPSHOT",
]
