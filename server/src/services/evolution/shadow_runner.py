"""Shadow runner — task 23.2.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23.2
(Phase L — Promoter). Covers:

* **R-3.7** — WHILE candidate is in ``shadow`` status THE user-visible
  response SHALL be equivalent to baseline; candidate run results SHALL
  only be written to shadow stats.
* **P-Evolve-4** — shadow doesn't affect the user-visible response.

Design goals
------------

* **User-invisible.** :meth:`ShadowRunner.replay` **never** returns the
  candidate response. The baseline response is a read-only input that
  the runner copies into the stat payload unchanged. Any failure
  inside the candidate runner is swallowed and recorded on the stat
  itself — it must not bubble up into the caller's request path.
* **Fire-and-forget.** :meth:`ShadowRunner.schedule` enqueues a
  replay as an :func:`asyncio.create_task` and returns the task
  immediately. Callers must never ``await`` it on the hot path.
* **No new DDL.** Stats are persisted as ``skill_evaluations`` rows
  with ``eval_set_name = "shadow_traffic"`` and the comparison JSON in
  the existing ``details`` JSONB column (task 23.2 explicitly opts
  for this over a dedicated ``shadow_stats`` table).
* **Deterministic dataclasses.** :class:`LiveRequest` and
  :class:`ShadowComparisonStat` are ``frozen=True, slots=True`` so a
  stat is effectively a value object — once built, the runner can
  pass it around between the in-process task and the DB write without
  worrying about mutation.

Integration point
-----------------

The Promoter (task 23.1) constructs one :class:`ShadowRunner` per
process, injects the real candidate runner, and calls
:meth:`schedule` from the post-turn hook inside
:class:`RuntimeGateway`. The gateway already has the baseline
response in hand at that point and passes it in verbatim.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------


SHADOW_EVAL_SET_NAME: str = "shadow_traffic"
"""``skill_evaluations.eval_set_name`` label used for shadow rows.

A constant so the Promoter / dashboards can query for shadow comparisons
with a single predicate::

    SELECT details FROM skill_evaluations
    WHERE candidate_id = :cid AND eval_set_name = 'shadow_traffic'

Keeps shadow stats visible alongside evaluator rows (which use the
eval set name, e.g. ``fault_triage_v1``) without needing a new table.
"""


# ---------------------------------------------------------------------------
# Value dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveRequest:
    """Read-only description of one live chat turn.

    Only the fields the shadow runner actually needs for its
    comparison / persistence. Deliberately narrow so the gateway can
    construct one from any request representation (SSE payload, /chat
    JSON body, etc.) without adapting a larger type.
    """

    message: str
    session_id: str
    user_id: str
    space_id: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateRunResult:
    """What a :data:`CandidateRunner` returns for one replay.

    * ``response`` — candidate's final user-facing text (never shown).
    * ``latency_ms`` — wall-clock of the candidate run in ms.
    * ``tools_used`` — tool names the candidate invoked. Ordering is
      not normalised; :func:`_diff_tools` treats them as a set.
    """

    response: str
    latency_ms: int
    tools_used: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ShadowComparisonStat:
    """Immutable record of one (baseline, candidate) comparison.

    Fields exactly match the task 23.2 contract, plus ``error_message``
    so a candidate failure can be captured on the stat row instead of
    dropped on the floor.

    :meth:`to_dict` is the canonical JSONB shape stored under
    ``skill_evaluations.details``; it's the only serialisation
    consumers should rely on.
    """

    baseline_response: str
    candidate_response: str | None
    response_match: bool
    latency_delta_ms: int | None
    tools_delta: list[str]
    timestamp: datetime
    error_message: str | None = None
    # Context for diagnostics — not part of the task's minimum set
    # but useful in the persisted blob for later analysis.
    baseline_latency_ms: int | None = None
    candidate_latency_ms: int | None = None
    baseline_tools: list[str] = field(default_factory=list)
    candidate_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Project onto the JSON shape we write to ``details``."""
        return {
            "baseline_response": self.baseline_response,
            "candidate_response": self.candidate_response,
            "response_match": self.response_match,
            "latency_delta_ms": self.latency_delta_ms,
            "tools_delta": list(self.tools_delta),
            "timestamp": self.timestamp.isoformat(),
            "error_message": self.error_message,
            "baseline_latency_ms": self.baseline_latency_ms,
            "candidate_latency_ms": self.candidate_latency_ms,
            "baseline_tools": list(self.baseline_tools),
            "candidate_tools": list(self.candidate_tools),
        }


CandidateRunner = Callable[
    [uuid.UUID, LiveRequest], Awaitable[CandidateRunResult]
]
"""Callable that runs a candidate against a live request.

Injected at :class:`ShadowRunner` construction so the runner has no
dependency on the actual agent-runtime wiring. Production uses a
closure over :class:`RuntimeGateway`; tests use a deterministic fake.
"""


# ---------------------------------------------------------------------------
# ShadowRunner
# ---------------------------------------------------------------------------


class ShadowRunner:
    """Async worker replaying shadow candidates against live traffic.

    Construction is cheap — no DB session is opened until
    :meth:`replay` actually runs. Two knobs are injectable for tests:

    * ``candidate_runner`` — async callable that replays a candidate.
      Required: there's no default because the runtime construction
      differs between the gateway and tests.
    * ``db_factory`` — async ctx-manager yielding a session with
      ``.execute(stmt, params)`` + ``.commit()``. Defaults to
      :data:`~src.models.base.async_session_factory`.

    The runner keeps a set of live replay tasks so they stay alive
    until they complete (mirrors asyncio.create_task best practice —
    see :pep:`3156` notes on task garbage collection). Callers may
    await an individual task if they want synchronous behaviour (e.g.
    in tests), but the hot path never does.

    Thread-safety: all mutations to ``self._tasks`` happen on the
    asyncio event loop only. No lock required.
    """

    def __init__(
        self,
        *,
        candidate_runner: CandidateRunner,
        db_factory: Any | None = None,
    ) -> None:
        self._candidate_runner = candidate_runner
        self._explicit_db_factory = db_factory
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Resource accessors
    # ------------------------------------------------------------------

    @property
    def _db_factory(self) -> Any:
        if self._explicit_db_factory is not None:
            return self._explicit_db_factory
        from src.models.base import async_session_factory

        return async_session_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def replay(
        self,
        candidate_id: uuid.UUID,
        live_request: LiveRequest,
        baseline_response: str,
        *,
        baseline_tools: tuple[str, ...] = (),
        baseline_latency_ms: int | None = None,
    ) -> None:
        """Replay ``candidate_id`` against ``live_request`` and persist a stat.

        The runner **never** returns the candidate output. ``None``
        return is part of the R-3.7 contract: callers have no way to
        leak the candidate response back to the user.

        Parameters
        ----------
        candidate_id :
            Skill-candidate id from the ``skill_candidates`` table.
        live_request :
            Read-only description of the live turn. The ``message``
            field is replayed verbatim into the candidate runner.
        baseline_response :
            The user-visible response that was already sent to the
            user. Copied verbatim into the stat's
            ``baseline_response`` field; ``replay`` is forbidden from
            mutating it (strings are immutable in Python so this is
            automatically enforced — but we don't pass it by reference
            to anything that could coerce it into a mutable type).
        baseline_tools :
            Tool names the baseline invoked. Used for
            ``tools_delta``. Defaults to empty tuple when the gateway
            doesn't have tool-usage info available.
        baseline_latency_ms :
            Wall-clock of the baseline run. Used for
            ``latency_delta_ms``. ``None`` when the gateway didn't
            measure it.

        Error handling
        --------------
        If ``candidate_runner`` raises, the exception is caught and
        recorded on the stat as ``error_message``; ``candidate_response``
        stays ``None``. The stat is still persisted so the Promoter's
        "collect 500 shadow samples" counter still advances —
        candidate crashes are just as much a comparison data point as
        successful runs.

        If the DB write fails, the exception is logged and swallowed:
        we never punt a DB error back up into the caller's turn
        handler, which ran to completion before ``replay`` was
        scheduled.
        """
        started = time.perf_counter()
        candidate_response: str | None = None
        candidate_latency_ms: int | None = None
        candidate_tools: tuple[str, ...] = ()
        error_message: str | None = None

        try:
            result = await self._candidate_runner(candidate_id, live_request)
            candidate_response = result.response
            candidate_latency_ms = int(result.latency_ms)
            candidate_tools = tuple(result.tools_used or ())
        except asyncio.CancelledError:
            # Cancellation is not a candidate failure — let the task
            # unwind without writing a stat.
            raise
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            candidate_latency_ms = elapsed_ms
            error_message = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "shadow_runner: candidate %s replay failed: %s",
                candidate_id,
                error_message,
            )

        latency_delta_ms: int | None = None
        if baseline_latency_ms is not None and candidate_latency_ms is not None:
            latency_delta_ms = candidate_latency_ms - int(baseline_latency_ms)

        response_match = (
            candidate_response is not None
            and candidate_response == baseline_response
        )

        stat = ShadowComparisonStat(
            baseline_response=baseline_response,
            candidate_response=candidate_response,
            response_match=response_match,
            latency_delta_ms=latency_delta_ms,
            tools_delta=_diff_tools(baseline_tools, candidate_tools),
            timestamp=datetime.now(UTC),
            error_message=error_message,
            baseline_latency_ms=(
                int(baseline_latency_ms)
                if baseline_latency_ms is not None
                else None
            ),
            candidate_latency_ms=candidate_latency_ms,
            baseline_tools=list(baseline_tools),
            candidate_tools=list(candidate_tools),
        )

        try:
            await self._persist(candidate_id, live_request, stat)
        except Exception:
            logger.warning(
                "shadow_runner: failed to persist shadow stat for %s",
                candidate_id,
                exc_info=True,
            )

    def schedule(
        self,
        candidate_id: uuid.UUID,
        live_request: LiveRequest,
        baseline_response: str,
        *,
        baseline_tools: tuple[str, ...] = (),
        baseline_latency_ms: int | None = None,
    ) -> asyncio.Task[None]:
        """Enqueue a replay as an asyncio background task.

        Returns the created :class:`asyncio.Task` so tests and the
        Promoter can introspect or (optionally) await it. Production
        callers never await the returned task — doing so would
        reintroduce the user-visible latency that R-3.7 exists to
        prevent.

        The task is retained in ``self._tasks`` until done so the
        asyncio runtime doesn't garbage-collect it mid-flight.
        """
        coro = self.replay(
            candidate_id,
            live_request,
            baseline_response,
            baseline_tools=baseline_tools,
            baseline_latency_ms=baseline_latency_ms,
        )
        task = asyncio.create_task(
            coro, name=f"shadow-replay-{candidate_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_pending(self, timeout: float | None = None) -> None:
        """Wait for all currently scheduled replay tasks to finish.

        Useful in tests and on graceful shutdown. Does nothing if the
        runner has no pending tasks.
        """
        pending = list(self._tasks)
        if not pending:
            return
        await asyncio.wait(pending, timeout=timeout)

    @property
    def pending_count(self) -> int:
        """Number of replays currently outstanding."""
        return len(self._tasks)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist(
        self,
        candidate_id: uuid.UUID,
        live_request: LiveRequest,
        stat: ShadowComparisonStat,
    ) -> None:
        """Append a shadow stat row to ``skill_evaluations``.

        One row per replay. ``baseline_score`` / ``candidate_score``
        stay NULL — shadow stats aren't graded scores, they're
        output-diff samples. ``n_samples=1`` so rolled-up sample
        counters (used by the Promoter to decide "500 samples
        collected?") are the row count.

        ``passed`` is set to ``True`` iff the candidate returned
        without error AND matched the baseline byte-for-byte. This
        gives the Promoter a cheap aggregation surface:

            SELECT COUNT(*) FILTER (WHERE passed) * 1.0 / COUNT(*)
            FROM skill_evaluations
            WHERE candidate_id = :cid AND eval_set_name = 'shadow_traffic'
        """
        details = {
            "shadow_stat": stat.to_dict(),
            "live_request": {
                "session_id": live_request.session_id,
                "user_id": live_request.user_id,
                "space_id": live_request.space_id,
                # Full message text is not persisted (PII) — only a
                # deterministic prefix fingerprint so two identical
                # messages can be correlated across rows.
                "message_sha256_prefix": _sha256_prefix(live_request.message),
            },
        }
        payload = json.dumps(details, ensure_ascii=False, default=str)
        passed = stat.error_message is None and stat.response_match
        async with self._db_factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO skill_evaluations (
                        candidate_id, eval_set_name, baseline_score,
                        candidate_score, n_samples, passed, details
                    ) VALUES (
                        :candidate_id, :eval_set_name, NULL,
                        NULL, 1, :passed, CAST(:details AS jsonb)
                    )
                    """
                ),
                {
                    "candidate_id": candidate_id,
                    "eval_set_name": SHADOW_EVAL_SET_NAME,
                    "passed": passed,
                    "details": payload,
                },
            )
            await session.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _diff_tools(
    baseline: tuple[str, ...] | list[str],
    candidate: tuple[str, ...] | list[str],
) -> list[str]:
    """Return a signed symmetric difference of tool names.

    Shape: ``["+added", "-removed"]`` — candidate-only tools first,
    baseline-only tools after. Sorted within each group so the blob
    is deterministic across runs.

    Returns an empty list when both sides use the same tool set,
    regardless of order (tools are deduplicated via :class:`set`).
    """
    b = set(baseline)
    c = set(candidate)
    added = sorted(c - b)
    removed = sorted(b - c)
    return [f"+{t}" for t in added] + [f"-{t}" for t in removed]


def _sha256_prefix(message: str) -> str:
    """Return the first 16 hex chars of SHA-256(message)."""
    return hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "CandidateRunResult",
    "CandidateRunner",
    "LiveRequest",
    "SHADOW_EVAL_SET_NAME",
    "ShadowComparisonStat",
    "ShadowRunner",
]
