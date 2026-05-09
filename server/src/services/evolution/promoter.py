"""Promoter — state-machine driver for candidate evolution.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23
(Phase L — Promoter + Rollback).

Surface covered:

* Task 23.1 (``Promoter.step(candidate_id)``) — forward-motion through
  the candidate state machine: ``shadow → ab → active | rejected``.
  On activation of ``kind=active`` the promoter dispatches to one of
  three activation paths (``activate_skill`` /
  ``activate_prompt_patch`` / ``activate_tool_config``) per R-3.2 /
  R-3.7 / R-3.8.
* Task 23.3 (``Promoter.rollback`` / ``Promoter.rollback_prompt``) —
  R-3.9 / R-3.19 rollback surface: flip current active to ``retired``
  and restore the previously-activated row in a single transaction,
  then publish a promotion event so the ``PromptReloader`` fleet
  converges within 5s.

Key dependencies:

* :class:`RollbackResult` — outcome of one rollback call.
* :class:`PromoterStepResult` — outcome of one :meth:`Promoter.step` call.
* :class:`Promoter` — holds injected db factory, Kafka producer,
  skills root, stats provider, tool_manager hook.

Covers:

* **R-3.9** — roll the current active back to retired and restore the
  most recent prior active row from ``skill_versions`` (skill) or
  ``sub_agent_prompt_versions`` (prompt_patch).
* **R-3.19** — the rollback completes DB state change + Kafka event
  publication in a single call. The DB writes all run in one session
  (single transaction); the Kafka event is published only after the
  commit succeeds so readers can never observe a rolled-back Kafka
  message against a not-yet-rolled-back DB. Convergence of new
  requests to the rolled-back version takes ≤ 5s and is the
  PromptReloader's job (task 20).

Design notes:

* DB writes go through raw SQL statements compatible with the
  in-memory fake DB used across the evolution tests (see
  :mod:`tests.evolution.test_candidate_store`,
  :mod:`tests.evolution.test_rollback`). That keeps the tests fast
  and hermetic — no live Postgres required.
* The Kafka producer is injectable. Tests pass a ``FakeProducer`` with
  a recording ``send_and_wait``; production uses a lazily-started
  :class:`AIOKafkaProducer` mirroring the DLQ / TrajectorySink
  patterns.
* ``tool_manager.invalidate_cache()`` is invoked for skill rollback
  so the live ``ExecutorAgent`` stops serving the retired skill
  immediately. Prompt rollback relies on the PromptReloader round-trip
  (Kafka → ``SubAgentPromptRegistry``) for the same effect.
* The Kafka payload for a rollback deliberately carries **both** the
  standard ``prompt_patch`` fields that :class:`PromptReloader` already
  understands (``kind``, ``target_ref``, ``new_version_id``,
  ``to_status``) **and** the discriminator fields the task spec calls
  out (``event_kind="rollback"``, ``active_version_id``,
  ``retired_version_id``). Using ``kind="prompt_patch"`` is intentional
  — otherwise the reloader would silently skip the event (it filters
  ``_HANDLED_KINDS={"prompt_patch"}``) and the 5s convergence guarantee
  in R-3.19 would not hold.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text

from src.config import settings
from src.services.evolution.candidate_store import (
    CandidateRow,
    InvalidStateTransition,
    SkillCandidateStore,
    TAG_KEY_TOOL_CONFIG_PATCH,
)
from src.services.evolution.promotion_rules import can_promote
from src.services.evolution.prompt_reloader import PROMOTION_TOPIC

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public constants — promotion gates (task 23.1)
# ---------------------------------------------------------------------------


# --- Shadow phase gates (R-3.7 / design.md § Promoter pseudocode) ---
SHADOW_MIN_SAMPLES: int = 500
"""Minimum shadow-traffic comparisons before the shadow→ab gate fires."""

SHADOW_MAX_AGE_HOURS: float = 24.0
"""Age (hours) after which shadow evaluates even with < ``SHADOW_MIN_SAMPLES``."""

SHADOW_ERROR_RATE_DELTA: float = 0.01
"""Max tolerated candidate-minus-baseline error-rate gap at the shadow stage."""

# --- AB phase gates (R-3.8 / design.md § Promoter pseudocode) --------
AB_MIN_SAMPLES: int = 2000
"""Minimum AB samples before the ab→active gate fires."""

AB_MAX_AGE_DAYS: float = 7.0
"""Age (days) after which AB evaluates even with < ``AB_MIN_SAMPLES``."""

AB_WIN_RATE_THRESHOLD: float = 0.55
"""Minimum win-rate the candidate must beat the baseline with at AB."""

AB_ERROR_RATE_DELTA: float = 0.005
"""Max tolerated candidate-minus-baseline error-rate gap at the AB stage."""

AB_ROLLOUT_PERCENT: int = 10
"""Rollout percentage used by :class:`ShadowABRouter` for AB candidates.

Exposed here so admin tooling and metrics labels reference one
constant. The router performs its own stable-hash bucket check; the
promoter itself doesn't bucket traffic.
"""


# ---------------------------------------------------------------------------
# Stats provider protocol
# ---------------------------------------------------------------------------


class ShadowStatsProvider(Protocol):
    """Minimal interface the promoter needs for shadow / AB stats.

    The default implementation queries a ``shadow_stats`` table; the
    :class:`ShadowRunner` (task 23.2) is the canonical writer. Tests
    inject a fake implementation so the promoter can be exercised
    without any shadow replay machinery.

    The returned dict should include at least:

    * ``samples`` — number of shadow / AB comparisons observed
    * ``baseline_score`` / ``candidate_score`` — aggregate scores for
      the R-3.6 epsilon check
    * ``error_rate_delta`` — candidate error rate minus baseline
      error rate (positive = candidate is worse)
    * ``win_rate`` — fraction of samples where the candidate beat
      the baseline (AB-only)
    * ``age_hours`` / ``age_days`` — freshness from first sample;
      allows timeout-based promotion when sample count is low

    Missing keys default to 0.0 / 0 so an empty stats row counts as
    "not enough data, stay put".
    """

    async def get_stats(self, candidate_id: uuid.UUID) -> dict[str, Any]: ...


class _DefaultShadowStatsProvider:
    """DB-backed default. Safe to import even if the table is missing."""

    def __init__(self, db_factory: Any | None = None) -> None:
        self._db_factory = db_factory

    async def get_stats(self, candidate_id: uuid.UUID) -> dict[str, Any]:
        """Query a ``shadow_stats`` row if the table exists; else empty.

        The ``shadow_stats`` schema isn't declared in this repository
        yet (task 23.2 reserves the right to land it as JSONB on
        ``skill_evaluations``), so this method degrades cleanly when
        the table is missing. Any DB error → empty stats → promoter
        keeps the candidate at its current status.
        """
        factory = self._db_factory
        if factory is None:
            from src.models.base import async_session_factory

            factory = async_session_factory

        try:
            async with factory() as session:
                rows = await session.execute(
                    text(
                        """
                        SELECT samples,
                               candidate_score,
                               baseline_score,
                               error_rate_delta,
                               win_rate,
                               age_hours,
                               age_days
                        FROM shadow_stats
                        WHERE candidate_id = :cid
                        """
                    ),
                    {"cid": candidate_id},
                )
                row = rows.first()
        except Exception:
            logger.debug(
                "promoter: shadow_stats query failed for %s; treating as empty",
                candidate_id,
            )
            return {}

        if row is None:
            return {}

        def _f(name: str) -> float:
            v = getattr(row, name, None)
            try:
                return float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        def _i(name: str) -> int:
            v = getattr(row, name, None)
            try:
                return int(v) if v is not None else 0
            except (TypeError, ValueError):
                return 0

        return {
            "samples": _i("samples"),
            "candidate_score": _f("candidate_score"),
            "baseline_score": _f("baseline_score"),
            "error_rate_delta": _f("error_rate_delta"),
            "win_rate": _f("win_rate"),
            "age_hours": _f("age_hours"),
            "age_days": _f("age_days"),
        }


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RollbackResult:
    """Outcome of a single rollback call.

    ``ok=True`` means a real rollback happened: the previously active
    version is now active again and the prior active row is retired.
    ``ok=False`` means the rollback was a no-op — either no active
    version existed, no prior version was available to restore, or a
    concurrent writer already rolled back. The ``reason`` field carries
    a short human-readable explanation useful for CLI / admin
    response bodies.

    Both version ids are ``None`` when ``ok=False`` so the caller
    can tell at a glance that nothing was changed — no need to
    cross-check the id values.
    """

    ok: bool
    kind: str  # "skill" | "prompt_patch"
    name: str  # skill_name or sub_agent_name
    retired_version_id: uuid.UUID | None
    restored_version_id: uuid.UUID | None
    reason: str
    event_published: bool = False


@dataclass(frozen=True, slots=True)
class PromoterStepResult:
    """Outcome of one :meth:`Promoter.step` call.

    Reported back to the caller (and logged for observability) so the
    control-plane UI can show "candidate stayed in shadow because
    only 342/500 samples observed" or similar at a glance.

    ``action`` values:

    * ``"promoted"`` — status advanced and persisted.
    * ``"rejected"`` — status flipped to ``rejected`` (terminal).
    * ``"stay"``     — gating checks not yet met; no DB mutation.
    * ``"not_applicable"`` — current status isn't driven by
      :meth:`Promoter.step` (``proposed`` is owned by the Evaluator;
      ``active`` / ``retired`` / ``rejected`` are terminal from the
      promoter's perspective).
    """

    candidate_id: uuid.UUID
    from_status: str
    to_status: str
    action: str  # "promoted" | "rejected" | "stay" | "not_applicable"
    reason: str


# ---------------------------------------------------------------------------
# Promoter
# ---------------------------------------------------------------------------


class Promoter:
    """Candidate state-machine driver. Rollback surface (task 23.3).

    Construction is cheap; the Kafka producer is started lazily on the
    first publish. The class is safe to share across coroutines — every
    rollback call opens its own session.

    Injection points (all optional; prod defaults apply):

    * ``db_factory``    — async ctx manager yielding a session.
      Defaults to the process-wide :data:`async_session_factory`.
    * ``producer``      — pre-started Kafka producer with
      ``send_and_wait(topic, value)``. If ``None``, a real
      :class:`AIOKafkaProducer` is constructed on first publish.
    * ``bootstrap_servers`` — Kafka bootstrap list for the lazy
      producer path. Defaults to ``settings.kafka_bootstrap_servers``.
    * ``skills_root_dir`` — filesystem root for skill artefacts.
      Defaults to :data:`src.services.skill_sync.SKILLS_DIR`.
    * ``tool_manager_invalidate`` — zero-arg async callable triggered
      after a successful skill rollback. Defaults to
      ``tool_manager.invalidate_cache``.
    """

    # Kafka topic for promotion / rollback events. Mirrors the constant
    # imported by :class:`PromptReloader` so both ends stay in lockstep.
    topic: str = PROMOTION_TOPIC

    def __init__(
        self,
        store: SkillCandidateStore | None = None,
        *,
        stats_provider: ShadowStatsProvider | None = None,
        db_factory: Any | None = None,
        producer: Any | None = None,
        kafka_producer: Any | None = None,
        bootstrap_servers: str | None = None,
        skills_root_dir: Path | None = None,
        tool_manager_invalidate: Any | None = None,
        promotion_topic: str = PROMOTION_TOPIC,
    ) -> None:
        self._explicit_db_factory = db_factory
        # ``kafka_producer`` is the task-23.1 kwarg name; ``producer`` is
        # the task-23.3 name. Accept either for ergonomic parity.
        self._producer = kafka_producer if kafka_producer is not None else producer
        self._producer_owned = False
        self._bootstrap = bootstrap_servers or settings.kafka_bootstrap_servers
        self._explicit_skills_root = (
            Path(skills_root_dir) if skills_root_dir is not None else None
        )
        self._tool_manager_invalidate = tool_manager_invalidate
        # Serialise producer construction so two racing rollbacks don't
        # each try to build a client.
        self._producer_lock = asyncio.Lock()

        # Task 23.1 — step() dependencies. Lazily-constructed so the
        # rollback-only code path (task 23.3) keeps its zero-argument
        # construction semantics and unit tests aren't forced to wire
        # a store.
        self._store = store
        self._stats = stats_provider
        self._promotion_topic = promotion_topic
        # Keep instance-level topic in sync so both rollback
        # (``self.topic``) and forward-promotion (``self._promotion_topic``)
        # paths publish to the same configured topic.
        self.topic = promotion_topic

    # ------------------------------------------------------------------
    # Resource accessors
    # ------------------------------------------------------------------

    @property
    def _db_factory(self) -> Any:
        if self._explicit_db_factory is not None:
            return self._explicit_db_factory
        # Lazy import so constructing a Promoter in a test that
        # monkeypatches the factory doesn't open a real DB connection.
        from src.models.base import async_session_factory

        return async_session_factory

    @property
    def _skills_root(self) -> Path:
        if self._explicit_skills_root is not None:
            return self._explicit_skills_root
        try:
            from src.services.skill_sync import SKILLS_DIR

            return Path(SKILLS_DIR)
        except Exception:
            return Path(__file__).resolve().parents[3] / "data" / "skills"

    # ------------------------------------------------------------------
    # Public API — rollback
    # ------------------------------------------------------------------

    async def rollback_prompt(self, sub_agent_name: str) -> RollbackResult:
        """Roll back the active prompt version for *sub_agent_name*.

        Flow (R-3.9 / R-3.19):

        1. Open one DB session (= one transaction).
        2. Look up the current ``active`` row in
           ``sub_agent_prompt_versions``. If none, no-op.
        3. Look up the most-recently-activated non-current row. If none,
           no-op (idempotent — the sub-agent has nothing to fall back to).
        4. Flip the current row to ``retired`` (guarded by
           ``WHERE status='active'`` to foil a concurrent rollback).
        5. Flip the previous row back to ``active`` (clearing
           ``retired_at``, bumping ``activated_at``).
        6. ``session.commit()`` — a single-transaction atomic swap.
        7. Publish a ``ops.agent.promotion`` event that the
           :class:`PromptReloader` treats as a standard prompt_patch
           promotion of the restored row. This is what delivers the
           ≤ 5s convergence guarantee across replicas.

        If the Kafka publish fails the DB state is already correct; we
        log the failure but still return ``ok=True`` so the caller
        knows the rollback itself landed. Production will self-heal on
        the next registry refresh tick.
        """
        curr_id: uuid.UUID | None = None
        prev_id: uuid.UUID | None = None

        async with self._db_factory() as session:
            current = await self._select_active_prompt(session, sub_agent_name)
            if current is None:
                return RollbackResult(
                    ok=False,
                    kind="prompt_patch",
                    name=sub_agent_name,
                    retired_version_id=None,
                    restored_version_id=None,
                    reason="no active version to roll back",
                )
            curr_id = _coerce_uuid(current["id"])

            previous = await self._select_previous_prompt(
                session, sub_agent_name, curr_id
            )
            if previous is None:
                # Idempotent no-op: rollback with no history is not an
                # error, it simply has nothing to do.
                return RollbackResult(
                    ok=False,
                    kind="prompt_patch",
                    name=sub_agent_name,
                    retired_version_id=curr_id,
                    restored_version_id=None,
                    reason="no previous active version available",
                )
            prev_id = _coerce_uuid(previous["id"])

            # Atomic pair of writes in a single transaction (R-3.19).
            await session.execute(
                text(
                    """
                    UPDATE sub_agent_prompt_versions
                    SET status = 'retired',
                        retired_at = now()
                    WHERE id = :id AND status = 'active'
                    """
                ),
                {"id": curr_id},
            )
            await session.execute(
                text(
                    """
                    UPDATE sub_agent_prompt_versions
                    SET status = 'active',
                        activated_at = now(),
                        retired_at = NULL
                    WHERE id = :id
                    """
                ),
                {"id": prev_id},
            )
            await session.commit()

        # Kafka publish happens *after* commit so readers can never
        # observe the event against a not-yet-applied DB state.
        published = await self._emit_rollback_event_prompt(
            sub_agent_name=sub_agent_name,
            retired_version_id=curr_id,
            restored_version_id=prev_id,
        )

        logger.info(
            "promoter.rollback_prompt: %s retired=%s restored=%s published=%s",
            sub_agent_name,
            curr_id,
            prev_id,
            published,
        )
        return RollbackResult(
            ok=True,
            kind="prompt_patch",
            name=sub_agent_name,
            retired_version_id=curr_id,
            restored_version_id=prev_id,
            reason="ok",
            event_published=published,
        )

    async def rollback(self, name: str) -> RollbackResult:
        """Roll back the active version of skill *name* (R-3.9).

        Flow, mirroring :meth:`rollback_prompt` but targeting
        ``skill_versions`` + the on-disk ``data/skills/<name>/`` tree:

        1. Within one DB session:

           a. Find the currently active row in ``skill_versions``
              (``retired_at IS NULL`` and ``activated_at IS NOT NULL``).
           b. Find the most-recent previously-activated row
              (``retired_at IS NOT NULL``) for the same skill_name.
           c. Mark the current row ``retired_at = now()``.
           d. Mark the previous row ``retired_at = NULL`` and bump
              ``activated_at``.
           e. If the current row has a ``candidate_id``, flip the
              matching ``skill_candidates`` row to ``status='retired'``.
           f. ``session.commit()`` — single-transaction swap.

        2. Write the previous version's ``skill_prompt`` back onto
           disk at ``<skills_root>/<name>/SKILL.md``. The on-disk copy
           is what DeepAgents' ``SkillsMiddleware`` reads at runtime,
           so the rollback is not effective until the file is
           restored.
        3. ``tool_manager.invalidate_cache()`` so the in-process tool
           cache picks up the restored skill on the next request.
        4. Publish a ``ops.agent.promotion`` event carrying the
           rollback discriminator so downstream observers (audit log,
           other FastAPI replicas sharing the same file volume) see
           the event.

        Idempotent: with no previous version to restore we return a
        no-op result without touching the filesystem or emitting a
        Kafka event.
        """
        curr_id: uuid.UUID | None = None
        prev_id: uuid.UUID | None = None
        prev_prompt: str | None = None

        async with self._db_factory() as session:
            current = await self._select_active_skill_version(session, name)
            if current is None:
                return RollbackResult(
                    ok=False,
                    kind="skill",
                    name=name,
                    retired_version_id=None,
                    restored_version_id=None,
                    reason="no active skill version to roll back",
                )
            curr_id = _coerce_uuid(current["id"])

            previous = await self._select_previous_skill_version(
                session, name, curr_id
            )
            if previous is None:
                return RollbackResult(
                    ok=False,
                    kind="skill",
                    name=name,
                    retired_version_id=curr_id,
                    restored_version_id=None,
                    reason="no previous active skill version available",
                )
            prev_id = _coerce_uuid(previous["id"])
            prev_prompt = str(previous.get("skill_prompt") or "")

            # a) retire current version
            await session.execute(
                text(
                    """
                    UPDATE skill_versions
                    SET retired_at = now()
                    WHERE id = :id AND retired_at IS NULL
                    """
                ),
                {"id": curr_id},
            )
            # b) restore previous version
            await session.execute(
                text(
                    """
                    UPDATE skill_versions
                    SET retired_at = NULL,
                        activated_at = now()
                    WHERE id = :id
                    """
                ),
                {"id": prev_id},
            )
            # c) flip matching skill_candidates row to retired, if any
            curr_candidate_id = current.get("candidate_id")
            if curr_candidate_id is not None:
                await session.execute(
                    text(
                        """
                        UPDATE skill_candidates
                        SET status = 'retired',
                            updated_at = now()
                        WHERE id = :id
                        """
                    ),
                    {"id": _coerce_uuid(curr_candidate_id)},
                )
            await session.commit()

        # Filesystem restore — failures here DON'T roll back the DB
        # (the DB is the source of truth; a stale SKILL.md is caught
        # by ``check_tool_consistency`` on the next sync tick). We do
        # log so operators see the problem.
        fs_restored = False
        try:
            self._restore_skill_md(name, prev_prompt or "")
            fs_restored = True
        except Exception:
            logger.exception(
                "promoter.rollback: failed to restore SKILL.md for %s", name
            )

        # Invalidate the tool cache so the next request doesn't keep
        # serving the retired skill's compiled prompt.
        try:
            if self._tool_manager_invalidate is not None:
                await self._tool_manager_invalidate()
            else:
                from src.services.tool_manager import tool_manager

                await tool_manager.invalidate_cache()
        except Exception:
            logger.exception(
                "promoter.rollback: tool_manager.invalidate_cache failed"
            )

        published = await self._emit_rollback_event_skill(
            skill_name=name,
            retired_version_id=curr_id,
            restored_version_id=prev_id,
        )

        logger.info(
            "promoter.rollback: %s retired=%s restored=%s fs=%s published=%s",
            name,
            curr_id,
            prev_id,
            fs_restored,
            published,
        )
        return RollbackResult(
            ok=True,
            kind="skill",
            name=name,
            retired_version_id=curr_id,
            restored_version_id=prev_id,
            reason="ok" if fs_restored else "ok (filesystem restore failed)",
            event_published=published,
        )

    # ------------------------------------------------------------------
    # Store / stats accessors — lazily resolved so the rollback-only
    # caller in task 23.3 doesn't have to wire them.
    # ------------------------------------------------------------------

    @property
    def store(self) -> SkillCandidateStore:
        if self._store is None:
            self._store = SkillCandidateStore(
                db_factory=self._explicit_db_factory,
                skills_root_dir=self._explicit_skills_root,
            )
        return self._store

    @property
    def stats(self) -> ShadowStatsProvider:
        if self._stats is None:
            self._stats = _DefaultShadowStatsProvider(self._explicit_db_factory)
        return self._stats

    # ------------------------------------------------------------------
    # Public API — forward motion (task 23.1)
    # ------------------------------------------------------------------

    async def step(self, candidate_id: uuid.UUID) -> PromoterStepResult:
        """Advance ``candidate_id`` one edge along the candidate state machine.

        Dispatches by the candidate's current status:

        * ``shadow``: collect stats; promote to ``ab`` when R-3.6
          epsilon holds and the error-rate delta is within budget
          (``SHADOW_ERROR_RATE_DELTA``). Reject if the gate fails and
          the sample count / age threshold has been crossed.
        * ``ab``: collect stats; activate (kind-specific path) when
          win_rate ≥ ``AB_WIN_RATE_THRESHOLD`` and error_rate_delta
          ≤ ``AB_ERROR_RATE_DELTA``. Reject on clear regressions.
        * anything else (``proposed`` / ``active`` / ``retired`` /
          ``rejected``): ``action='not_applicable'`` — those states
          are driven by other actors (Evaluator for
          ``proposed → shadow``; admin rollback for ``active → retired``).

        Raises:
            LookupError: ``candidate_id`` doesn't exist.
        """
        row = await self.store.get(candidate_id)
        if row is None:
            raise LookupError(f"candidate {candidate_id} not found")

        if row.status == "shadow":
            return await self._step_shadow(row)
        if row.status == "ab":
            return await self._step_ab(row)

        return PromoterStepResult(
            candidate_id=row.id,
            from_status=row.status,
            to_status=row.status,
            action="not_applicable",
            reason=f"status={row.status!r} not managed by Promoter.step",
        )

    # ------------------------------------------------------------------
    # Phase drivers (task 23.1)
    # ------------------------------------------------------------------

    async def _step_shadow(self, row: CandidateRow) -> PromoterStepResult:
        """Gate for ``shadow → ab``. No user-visible side effects (R-3.7)."""
        stats = await self.stats.get_stats(row.id)
        samples = int(stats.get("samples", 0) or 0)
        age_hours = float(stats.get("age_hours", 0.0) or 0.0)

        # Wait for either enough samples OR the age timeout.
        if samples < SHADOW_MIN_SAMPLES and age_hours < SHADOW_MAX_AGE_HOURS:
            return PromoterStepResult(
                candidate_id=row.id,
                from_status="shadow",
                to_status="shadow",
                action="stay",
                reason=(
                    f"shadow gating: samples={samples}/{SHADOW_MIN_SAMPLES}, "
                    f"age={age_hours:.1f}h/{SHADOW_MAX_AGE_HOURS:.1f}h"
                ),
            )

        baseline = float(stats.get("baseline_score", 0.0) or 0.0)
        candidate = float(stats.get("candidate_score", 0.0) or 0.0)
        error_rate_delta = float(stats.get("error_rate_delta", 0.0) or 0.0)

        if can_promote(baseline, candidate) and error_rate_delta <= SHADOW_ERROR_RATE_DELTA:
            await self.store.update_status(row.id, "ab")
            logger.info(
                "promoter: %s (%s) shadow -> ab: baseline=%.3f candidate=%.3f erd=%.4f",
                row.name,
                row.kind,
                baseline,
                candidate,
                error_rate_delta,
            )
            return PromoterStepResult(
                candidate_id=row.id,
                from_status="shadow",
                to_status="ab",
                action="promoted",
                reason=(
                    f"baseline={baseline:.3f} candidate={candidate:.3f} "
                    f"erd={error_rate_delta:.4f}"
                ),
            )

        # Regression or safety breach — drop it.
        await self.store.update_status(row.id, "rejected")
        logger.info(
            "promoter: %s (%s) shadow -> rejected: baseline=%.3f candidate=%.3f erd=%.4f",
            row.name,
            row.kind,
            baseline,
            candidate,
            error_rate_delta,
        )
        return PromoterStepResult(
            candidate_id=row.id,
            from_status="shadow",
            to_status="rejected",
            action="rejected",
            reason=(
                f"shadow failed: baseline={baseline:.3f} "
                f"candidate={candidate:.3f} erd={error_rate_delta:.4f}"
            ),
        )

    async def _step_ab(self, row: CandidateRow) -> PromoterStepResult:
        """Gate for ``ab → active``. On pass, runs the kind-specific activation."""
        stats = await self.stats.get_stats(row.id)
        samples = int(stats.get("samples", 0) or 0)
        age_days = float(stats.get("age_days", 0.0) or 0.0)

        if samples < AB_MIN_SAMPLES and age_days < AB_MAX_AGE_DAYS:
            return PromoterStepResult(
                candidate_id=row.id,
                from_status="ab",
                to_status="ab",
                action="stay",
                reason=(
                    f"ab gating: samples={samples}/{AB_MIN_SAMPLES}, "
                    f"age={age_days:.2f}d/{AB_MAX_AGE_DAYS:.2f}d"
                ),
            )

        win_rate = float(stats.get("win_rate", 0.0) or 0.0)
        error_rate_delta = float(stats.get("error_rate_delta", 0.0) or 0.0)

        if win_rate < AB_WIN_RATE_THRESHOLD or error_rate_delta > AB_ERROR_RATE_DELTA:
            await self.store.update_status(row.id, "rejected")
            logger.info(
                "promoter: %s (%s) ab -> rejected: win_rate=%.3f erd=%.4f",
                row.name,
                row.kind,
                win_rate,
                error_rate_delta,
            )
            return PromoterStepResult(
                candidate_id=row.id,
                from_status="ab",
                to_status="rejected",
                action="rejected",
                reason=(
                    f"ab failed: win_rate={win_rate:.3f} "
                    f"erd={error_rate_delta:.4f}"
                ),
            )

        # Passed AB. Activate per kind.
        if row.kind == "skill":
            await self.activate_skill(row.id)
        elif row.kind == "prompt_patch":
            await self.activate_prompt_patch(row.id)
        elif row.kind == "tool_config":
            await self.activate_tool_config(row.id)
        else:
            raise ValueError(f"unknown candidate kind: {row.kind!r}")

        logger.info(
            "promoter: %s (%s) ab -> active: win_rate=%.3f erd=%.4f",
            row.name,
            row.kind,
            win_rate,
            error_rate_delta,
        )
        return PromoterStepResult(
            candidate_id=row.id,
            from_status="ab",
            to_status="active",
            action="promoted",
            reason=(
                f"activated {row.kind}: win_rate={win_rate:.3f} "
                f"erd={error_rate_delta:.4f}"
            ),
        )

    # ------------------------------------------------------------------
    # Activation paths (task 23.1, R-3.8)
    # ------------------------------------------------------------------

    async def activate_skill(self, candidate_id: uuid.UUID) -> None:
        """Move ``.candidate/<name>/`` into the main skills tree.

        Ordering is deliberate:

        1. Verify the candidate is in ``ab`` (the status-flip below
           re-checks via ``WHERE status = 'ab'`` at the DB level).
        2. Move the directory so that by the time ``status=active``
           the artefact is in place for ``tool_manager`` to find.
        3. Flip status via the store (hits the state-machine check
           plus the DB guard).
        4. Invalidate the tool manager cache so the new skill is
           picked up on the next tool list.

        If the candidate directory is missing we log a warning and
        continue to the status flip — a partial materialisation is
        better than leaving the candidate stuck at ``ab`` forever.
        """
        row = await self.store.get(candidate_id)
        if row is None or row.kind != "skill":
            raise ValueError(
                f"activate_skill: {candidate_id} not a skill candidate "
                f"(got {row.kind if row else 'missing'})"
            )
        if row.status != "ab":
            raise InvalidStateTransition(row.status, "active")

        skills_root = self._skills_root
        src_path = skills_root / ".candidate" / row.name
        dst_path = skills_root / row.name

        if src_path.exists():
            # Remove any stale destination so ``shutil.move`` never
            # has to merge into an existing tree. A candidate
            # overwriting an older active skill is the intended
            # semantics for a "successor" version.
            if dst_path.exists():
                shutil.rmtree(dst_path)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst_path))
            logger.info(
                "promoter: moved skill candidate %s -> %s", src_path, dst_path
            )
        else:
            logger.warning(
                "promoter: skill candidate dir missing at %s; proceeding "
                "with status flip only",
                src_path,
            )

        await self.store.update_status(candidate_id, "active")
        await self._invalidate_tool_manager_cache()

    async def activate_prompt_patch(self, candidate_id: uuid.UUID) -> None:
        """Flip a ``prompt_patch`` candidate to ``active`` transactionally.

        In a single DB transaction:

        * Retire the previous ``active`` row for the same
          ``sub_agent_name`` (``status='retired'``, ``retired_at=now()``).
        * Activate the candidate row (``status='active'``,
          ``activated_at=now()``, ``parent_version_id = previous id``).
          The UPDATE is guarded by ``WHERE status = 'ab'`` so R-3.4
          is enforced at the DB level — a concurrent writer that
          moved the row to another status makes this UPDATE a no-op.

        After the transaction commits, publishes a single
        ``ops.agent.promotion`` Kafka event so every FastAPI replica's
        :class:`PromptReloader` refreshes its registry within 5s
        (R-3.15).
        """
        row = await self.store.get(candidate_id)
        if row is None or row.kind != "prompt_patch":
            raise ValueError(
                f"activate_prompt_patch: {candidate_id} not a prompt_patch "
                f"candidate (got {row.kind if row else 'missing'})"
            )
        if row.status != "ab":
            raise InvalidStateTransition(row.status, "active")

        sub_agent_name = row.target_ref or row.name
        prev_id: uuid.UUID | None = None

        async with self._db_factory() as session:
            # Locate the current active row (if any).
            prev_rows = await session.execute(
                text(
                    """
                    SELECT id
                    FROM sub_agent_prompt_versions
                    WHERE sub_agent_name = :name
                      AND status = 'active'
                      AND id != :id
                    LIMIT 1
                    """
                ),
                {"name": sub_agent_name, "id": candidate_id},
            )
            prev_row = prev_rows.first()
            if prev_row is not None:
                prev_id = _coerce_uuid(prev_row.id)
                await session.execute(
                    text(
                        """
                        UPDATE sub_agent_prompt_versions
                        SET status = 'retired',
                            retired_at = now()
                        WHERE id = :id
                        """
                    ),
                    {"id": prev_id},
                )

            # Guarded state-machine flip.
            await session.execute(
                text(
                    """
                    UPDATE sub_agent_prompt_versions
                    SET status = 'active',
                        activated_at = now(),
                        parent_version_id = :prev_id
                    WHERE id = :id AND status = 'ab'
                    """
                ),
                {"id": candidate_id, "prev_id": prev_id},
            )
            await session.commit()

        logger.info(
            "promoter: prompt_patch %s activated for %s (prev=%s)",
            candidate_id,
            sub_agent_name,
            prev_id,
        )

        # Kafka publish — outside the transaction — is best-effort
        # (R-3.18 reloaders are idempotent + the registry re-reads
        # on every refresh tick).
        await self._emit_promotion_event_prompt_patch(
            candidate_id=candidate_id,
            sub_agent_name=sub_agent_name,
            prev_id=prev_id,
        )

    async def activate_tool_config(self, candidate_id: uuid.UUID) -> None:
        """Merge the tool_config patch into ``tools.config`` JSONB.

        Ordering mirrors :meth:`activate_skill` — apply the mutation
        first, then flip status, then invalidate the cache. The patch
        is a shallow merge: a top-level key in the patch overwrites
        the same key in the existing config; other keys are preserved.
        This matches the snapshot semantics
        :meth:`SkillCandidateStore.snapshot_tool_config` uses so task
        23.3's rollback path can restore the pre-patch state exactly.
        """
        row = await self.store.get(candidate_id)
        if row is None or row.kind != "tool_config":
            raise ValueError(
                f"activate_tool_config: {candidate_id} not a tool_config "
                f"candidate (got {row.kind if row else 'missing'})"
            )
        if row.status != "ab":
            raise InvalidStateTransition(row.status, "active")

        tool_name = row.target_ref
        if not tool_name:
            raise ValueError(
                f"activate_tool_config: candidate {candidate_id} has no "
                "target_ref (tool name)"
            )

        patch = _extract_tool_patch(row.tags)

        async with self._db_factory() as session:
            cur_rows = await session.execute(
                text(
                    """
                    SELECT config
                    FROM tools
                    WHERE name = :name
                    """
                ),
                {"name": tool_name},
            )
            cur = cur_rows.first()
            current_cfg = (
                _coerce_config(getattr(cur, "config", None)) if cur else {}
            )
            merged = {**current_cfg, **patch}

            await session.execute(
                text(
                    """
                    UPDATE tools
                    SET config = CAST(:cfg AS jsonb)
                    WHERE name = :name
                    """
                ),
                {
                    "cfg": json.dumps(merged, ensure_ascii=False),
                    "name": tool_name,
                },
            )
            await session.commit()

        logger.info(
            "promoter: tool_config merged into %s (candidate=%s, keys=%s)",
            tool_name,
            candidate_id,
            sorted(patch.keys()),
        )

        await self.store.update_status(candidate_id, "active")
        await self._invalidate_tool_manager_cache()

    async def _invalidate_tool_manager_cache(self) -> None:
        """Best-effort call to ``tool_manager.invalidate_cache``.

        Uses the injected ``tool_manager_invalidate`` when provided
        (test doubles), otherwise falls back to the module-level
        singleton. Failures are logged but never propagated — a stale
        cache is preferable to a failed activation.
        """
        try:
            if self._tool_manager_invalidate is not None:
                await self._tool_manager_invalidate()
                return
            from src.services.tool_manager import tool_manager

            await tool_manager.invalidate_cache()
        except Exception:
            logger.exception(
                "promoter: tool_manager.invalidate_cache failed (activation path)"
            )

    async def _emit_promotion_event_prompt_patch(
        self,
        *,
        candidate_id: uuid.UUID,
        sub_agent_name: str,
        prev_id: uuid.UUID | None,
    ) -> None:
        """Publish a forward-promotion event for a prompt_patch candidate.

        Payload mirrors what :class:`PromptReloader` expects
        (``kind="prompt_patch"``, ``target_ref``, ``new_version_id``,
        ``to_status``) so every replica's registry re-reads the DB and
        swaps its ``active`` pointer within ≤ 5s (R-3.15).

        Broker failures are logged but never propagated — the DB is
        the source of truth and registries self-heal on their next
        refresh tick.
        """
        payload = {
            "kind": "prompt_patch",
            "sub_agent_name": sub_agent_name,
            "target_ref": sub_agent_name,
            "new_version_id": str(candidate_id),
            "prev_version_id": str(prev_id) if prev_id else None,
            "to_status": "active",
            "event_id": f"promote-prompt-{candidate_id}",
        }
        await self._publish(payload)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _select_active_prompt(
        self, session: Any, sub_agent_name: str
    ) -> dict[str, Any] | None:
        rows = await session.execute(
            text(
                """
                SELECT id, sub_agent_name, system_prompt, status,
                       activated_at, retired_at
                FROM sub_agent_prompt_versions
                WHERE sub_agent_name = :name AND status = 'active'
                LIMIT 1
                """
            ),
            {"name": sub_agent_name},
        )
        row = rows.first()
        if row is None:
            return None
        return _row_to_dict(row)

    async def _select_previous_prompt(
        self, session: Any, sub_agent_name: str, exclude_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """Pick the most-recently-activated non-current prompt row.

        Candidates: rows for the same sub_agent_name whose
        ``activated_at`` is non-null (i.e. were active at some point)
        and whose id is not the row we're about to retire. The
        ``rejected`` / ``proposed`` statuses are excluded via the
        ``activated_at IS NOT NULL`` predicate because proposals that
        were never activated have no activation timestamp.
        """
        rows = await session.execute(
            text(
                """
                SELECT id, sub_agent_name, system_prompt, status,
                       activated_at, retired_at
                FROM sub_agent_prompt_versions
                WHERE sub_agent_name = :name
                  AND id <> :exclude_id
                  AND activated_at IS NOT NULL
                ORDER BY activated_at DESC
                LIMIT 1
                """
            ),
            {"name": sub_agent_name, "exclude_id": exclude_id},
        )
        row = rows.first()
        if row is None:
            return None
        return _row_to_dict(row)

    async def _select_active_skill_version(
        self, session: Any, skill_name: str
    ) -> dict[str, Any] | None:
        rows = await session.execute(
            text(
                """
                SELECT id, skill_name, candidate_id, skill_prompt,
                       activated_at, retired_at
                FROM skill_versions
                WHERE skill_name = :name
                  AND retired_at IS NULL
                  AND activated_at IS NOT NULL
                ORDER BY activated_at DESC
                LIMIT 1
                """
            ),
            {"name": skill_name},
        )
        row = rows.first()
        if row is None:
            return None
        return _row_to_dict(row)

    async def _select_previous_skill_version(
        self, session: Any, skill_name: str, exclude_id: uuid.UUID
    ) -> dict[str, Any] | None:
        rows = await session.execute(
            text(
                """
                SELECT id, skill_name, candidate_id, skill_prompt,
                       activated_at, retired_at
                FROM skill_versions
                WHERE skill_name = :name
                  AND id <> :exclude_id
                  AND retired_at IS NOT NULL
                  AND activated_at IS NOT NULL
                ORDER BY activated_at DESC
                LIMIT 1
                """
            ),
            {"name": skill_name, "exclude_id": exclude_id},
        )
        row = rows.first()
        if row is None:
            return None
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # Filesystem helper
    # ------------------------------------------------------------------

    def _restore_skill_md(self, name: str, skill_prompt: str) -> None:
        """Write the previous version's prompt back as ``SKILL.md``.

        Uses the same minimal frontmatter shape that ``write_skill_file``
        emits (``name`` + ``description``) so the next
        ``sync_from_filesystem`` tick sees a well-formed skill and
        doesn't flag a protocol violation. Parent directories are
        created as needed — a freshly-rolled-back skill may have had
        its directory removed if somebody manually retired it on disk.
        """
        # Sanitize the skill name against path traversal. The DB-side
        # value should already be well-formed (``CREATE TABLE`` caps at
        # 128 chars), but we defend against injection just in case.
        safe = "".join(
            ch if ch.isalnum() or ch in "_.-" else "_" for ch in name
        ).strip("._") or "skill"

        skill_dir = self._skills_root / safe
        skill_dir.mkdir(parents=True, exist_ok=True)
        md_path = skill_dir / "SKILL.md"

        # Minimal, valid frontmatter so DeepAgents' ``SkillsMiddleware``
        # parses the file without complaint. We don't have a canonical
        # ``description`` in ``skill_versions`` so we reuse the name.
        description = name
        content = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"---\n\n"
            f"{skill_prompt.strip()}\n"
        )
        md_path.write_text(content, encoding="utf-8")
        logger.info(
            "promoter.rollback: wrote %s (%d bytes)", md_path, len(content)
        )

    # ------------------------------------------------------------------
    # Kafka helpers
    # ------------------------------------------------------------------

    async def _get_producer(self) -> Any | None:
        """Return a ready-to-use Kafka producer, or ``None`` if unavailable.

        Lazy construction mirrors the DLQ / TrajectorySink pattern —
        an injected producer is used as-is; otherwise we build and
        start an :class:`AIOKafkaProducer`. Construction failures are
        counted as "unavailable" and we return ``None`` so a broken
        broker doesn't take down the rollback call. The caller logs
        and the DB state remains the source of truth.
        """
        if self._producer is not None:
            return self._producer

        async with self._producer_lock:
            if self._producer is not None:
                return self._producer
            try:
                from aiokafka import AIOKafkaProducer

                producer = AIOKafkaProducer(
                    bootstrap_servers=self._bootstrap,
                    client_id="aiopsos-promoter",
                    acks="all",
                )
                await producer.start()
                self._producer = producer
                self._producer_owned = True
            except Exception:
                logger.exception(
                    "promoter: AIOKafkaProducer.start failed; "
                    "rollback event will not be published"
                )
                self._producer = None
                return None

        return self._producer

    async def close(self) -> None:
        """Stop the lazily-built producer, if we own it."""
        if self._producer is not None and self._producer_owned:
            try:
                await self._producer.stop()
            except Exception:
                logger.exception("promoter: producer.stop failed")
            self._producer = None
            self._producer_owned = False

    async def _publish(self, payload: dict[str, Any]) -> bool:
        """Publish *payload* to the promotion topic. Return ``True`` on success."""
        producer = await self._get_producer()
        if producer is None:
            return False
        try:
            await producer.send_and_wait(
                self.topic,
                json.dumps(payload, ensure_ascii=False, default=str).encode(
                    "utf-8"
                ),
            )
            return True
        except Exception:
            logger.exception(
                "promoter: send_and_wait failed for event %s", payload.get("event_id")
            )
            return False

    async def _emit_rollback_event_prompt(
        self,
        *,
        sub_agent_name: str,
        retired_version_id: uuid.UUID,
        restored_version_id: uuid.UUID,
    ) -> bool:
        """Publish a prompt rollback event.

        The payload carries the full PromptReloader-compatible shape
        (``kind="prompt_patch"``, ``target_ref``, ``new_version_id``,
        ``to_status``) so the existing consumer in every FastAPI
        replica re-reads the DB and flips the registry in ≤ 5s
        (R-3.19). It also carries the rollback discriminator fields
        the task spec describes (``event_kind``,
        ``active_version_id``, ``retired_version_id``,
        ``sub_agent_name``) so audit observers can distinguish a
        rollback from a forward promotion.
        """
        payload = {
            # PromptReloader-compatible fields.
            "kind": "prompt_patch",
            "target_ref": sub_agent_name,
            "new_version_id": str(restored_version_id),
            "to_status": "active",
            "event_id": f"rollback-prompt-{retired_version_id}-{restored_version_id}",
            # Rollback-specific discriminator + audit fields.
            "event_kind": "rollback",
            "sub_agent_name": sub_agent_name,
            "active_version_id": str(restored_version_id),
            "retired_version_id": str(retired_version_id),
        }
        return await self._publish(payload)

    async def _emit_rollback_event_skill(
        self,
        *,
        skill_name: str,
        retired_version_id: uuid.UUID,
        restored_version_id: uuid.UUID,
    ) -> bool:
        """Publish a skill rollback event.

        ``kind`` is ``"skill"`` so the PromptReloader ignores it
        (skills reload via ``tool_manager.invalidate_cache``, not
        through the registry). The payload still rides on the shared
        promotion topic so a single audit consumer can see both
        promotion kinds.
        """
        payload = {
            "kind": "skill",
            "event_kind": "rollback",
            "target_ref": skill_name,
            "skill_name": skill_name,
            "active_version_id": str(restored_version_id),
            "retired_version_id": str(retired_version_id),
            "event_id": f"rollback-skill-{retired_version_id}-{restored_version_id}",
        }
        return await self._publish(payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Normalise a row (asyncpg RowMapping / SQLAlchemy Row / ad-hoc dict)."""
    if isinstance(row, dict):
        return dict(row)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        try:
            return dict(mapping)
        except Exception:
            pass
    # Fall back to attribute scrape — works for ``_Row`` fakes used in tests.
    out: dict[str, Any] = {}
    for attr in (
        "id",
        "sub_agent_name",
        "skill_name",
        "candidate_id",
        "system_prompt",
        "skill_prompt",
        "status",
        "activated_at",
        "retired_at",
    ):
        if hasattr(row, attr):
            out[attr] = getattr(row, attr)
    return out


def _coerce_uuid(value: Any) -> uuid.UUID:
    """Accept ``UUID | str`` and return a ``UUID``.

    Raises :class:`ValueError` on a malformed string — the caller is
    expected to have pulled this value from a DB row, so a bad value
    here indicates DB corruption and should not be silently swallowed.
    """
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _extract_tool_patch(tags: list[Any]) -> dict[str, Any]:
    """Pull the ``{TAG_KEY_TOOL_CONFIG_PATCH: {...}}`` blob from ``tags``.

    The candidate store writes the patch + pre-snapshot as sibling
    entries in the JSONB ``tags`` column; see
    :meth:`SkillCandidateStore._persist_tool_config`. Returning an
    empty dict when the sentinel is absent lets the promoter treat a
    malformed candidate as a no-op merge rather than raising at the
    UPDATE boundary.
    """
    for item in tags or []:
        if isinstance(item, dict) and TAG_KEY_TOOL_CONFIG_PATCH in item:
            patch = item[TAG_KEY_TOOL_CONFIG_PATCH]
            if isinstance(patch, dict):
                return dict(patch)
    return {}


def _coerce_config(value: Any) -> dict[str, Any]:
    """Normalise a ``tools.config`` cell into a plain ``dict``."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


__all__ = [
    "AB_ERROR_RATE_DELTA",
    "AB_MAX_AGE_DAYS",
    "AB_MIN_SAMPLES",
    "AB_ROLLOUT_PERCENT",
    "AB_WIN_RATE_THRESHOLD",
    "PROMOTION_TOPIC",
    "Promoter",
    "PromoterStepResult",
    "RollbackResult",
    "SHADOW_ERROR_RATE_DELTA",
    "SHADOW_MAX_AGE_HOURS",
    "SHADOW_MIN_SAMPLES",
    "ShadowStatsProvider",
]
