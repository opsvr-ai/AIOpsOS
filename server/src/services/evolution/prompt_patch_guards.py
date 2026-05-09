"""Runtime guards for ``prompt_patch`` candidates.

Spec: `.kiro/specs/agent-runtime-optimization-evolution`, task 21.3
(Phase J — ReflectionWorker). Covers acceptance criteria:

* **R-3.11** — `prompt_patch` candidate whose ``new_prompt`` length
  differs from the current active prompt by more than 50% SHALL be
  rejected. Protects against large rewrites that smuggle in risk.
* **R-3.12** — `prompt_patch` candidate whose ``new_prompt`` contains
  forbidden fragments (e.g. "ignore prior instructions"-style
  jailbreak phrases) SHALL be rejected AND the
  ``evolution_unsafe_prompt_total`` counter SHALL be incremented.

Design goals:

1. **Second line of defense.** Pydantic already drops LLM outputs that
   mention obvious jailbreak phrases (see
   :func:`_PromptPatchCandidateData._validate_new_prompt` in
   :mod:`reflection_logic`). However, pydantic-level rejections surface
   as ``n_invalid_schema`` and do *not* touch the prometheus counter.
   This module gives us a single pipeline that *always* routes
   forbidden-fragment rejections through
   :data:`evolution_unsafe_prompt_total` and adds the DB-aware length
   delta check that pydantic alone cannot perform (it needs the
   current active prompt to compare against).

2. **Pure + async-friendly.** :func:`evaluate_prompt_patch_guards` is a
   pure function that takes a ``new_prompt`` + a ``baseline_prompt``
   string and returns a :class:`PromptPatchGuardResult`. The async
   wrapper :func:`apply_prompt_patch_guards` takes the proposal plus a
   ``db_factory`` and looks up the baseline prompt itself.

3. **Fail-safe baselines.** The spec says "相对 current 的变化率"
   (change rate vs. current). There are three cases:

   * Active prompt exists in ``sub_agent_prompt_versions`` → use it.
   * No active prompt but the sub-agent has a code-level default in
     ``_DEFAULT_SUBAGENT_PROMPTS`` (R-3.20) → use the default. This is
     the expected cold-start state.
   * No baseline at all (unknown sub-agent) → skip the length check.
     The proposal is still subject to the forbidden-fragment guard.
     A skipped length check is reported in ``warnings`` so callers can
     log it.

4. **Forbidden-fragment list is shared.** The module re-exports the
   ``_FORBIDDEN_PROMPT_FRAGMENTS`` tuple from
   :mod:`reflection_logic` so the two rejection paths stay in sync.
   Add new fragments there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.core.metrics import evolution_unsafe_prompt_total
from src.services.evolution.reflection_logic import _FORBIDDEN_PROMPT_FRAGMENTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# R-3.11: ``abs(len(new) - len(cur)) / max(len(cur), 1) > 0.5`` rejects.
# The threshold is kept as a module-level constant so tests can
# monkey-patch it + downstream tuning lands in one place.
MAX_LENGTH_DELTA_RATIO: float = 0.5


# Label values emitted on :data:`evolution_unsafe_prompt_total`. Kept
# as typed constants to catch typos at import time instead of at
# metric-scrape time.
REASON_FORBIDDEN_FRAGMENT = "forbidden_fragment"
REASON_LENGTH_DELTA = "length_delta"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromptPatchGuardResult:
    """Outcome of running the prompt_patch guards on one proposal.

    ``passed=True`` means the proposal is safe to persist / promote.
    ``passed=False`` means the caller should drop the proposal *and*
    ``reason`` / ``detail`` carry why. Each failure has already
    incremented :data:`evolution_unsafe_prompt_total{reason=...}`
    before this result is returned — callers don't need to emit the
    metric themselves.

    ``warnings`` surfaces non-fatal issues (e.g. "no baseline prompt
    available, length check skipped"). Callers are free to log them
    but must not treat them as rejections.

    ``baseline_source`` is one of:

    * ``"db"``        — the baseline was read from
      ``sub_agent_prompt_versions``;
    * ``"default"``   — the baseline came from
      ``_DEFAULT_SUBAGENT_PROMPTS``;
    * ``"none"``      — no baseline was available; the length check
      was skipped (but forbidden-fragment still ran);
    * ``"unknown"``   — the pure helper was called without a
      baseline; nothing was skipped because the caller signalled it
      already knew the baseline state.
    """

    passed: bool
    reason: str | None = None
    detail: str | None = None
    warnings: list[str] = field(default_factory=list)
    baseline_source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "detail": self.detail,
            "warnings": list(self.warnings),
            "baseline_source": self.baseline_source,
        }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _contains_forbidden_fragment(new_prompt: str) -> str | None:
    """Return the first forbidden fragment present in ``new_prompt``.

    Case-insensitive for ASCII fragments — we normalise with ``lower()``
    before substring matching so ``"IGNORE PRIOR INSTRUCTIONS"`` is
    caught. Chinese fragments are matched verbatim since ``.lower()``
    is a no-op on CJK code points.
    """
    lowered = new_prompt.lower()
    for frag in _FORBIDDEN_PROMPT_FRAGMENTS:
        if frag in lowered:
            return frag
    return None


def _length_delta_ratio(new_prompt: str, baseline_prompt: str) -> float:
    """Return ``|len(new) - len(baseline)| / max(len(baseline), 1)``.

    ``max(..., 1)`` guards against zero-length baselines: a nonzero
    ``new_prompt`` vs. an empty baseline becomes a ratio of
    ``len(new)``, which is always > 0.5 for any non-trivial prompt and
    therefore rejected — exactly the intended conservative behaviour.
    """
    delta = abs(len(new_prompt) - len(baseline_prompt))
    return delta / max(len(baseline_prompt), 1)


def evaluate_prompt_patch_guards(
    new_prompt: str,
    baseline_prompt: str | None,
    *,
    baseline_source: str = "unknown",
) -> PromptPatchGuardResult:
    """Run forbidden-fragment + length-delta guards on one proposal.

    Args:
        new_prompt: the LLM-proposed replacement ``system_prompt``.
        baseline_prompt: the current prompt (active DB row or code
            default) the proposal is patching. Pass ``None`` when no
            baseline exists — the length check is then skipped and a
            warning is appended to the result.
        baseline_source: provenance label to attach to the result so
            callers can log "compared against DB active" vs "compared
            against code default". Has no effect on guard logic.

    Returns:
        :class:`PromptPatchGuardResult`. Failure cases have already
        incremented :data:`evolution_unsafe_prompt_total{reason=...}`;
        callers should just propagate the result.

    Order of checks:

    1. Forbidden fragment first — this is the *safety* guard; size
       is a secondary concern once the content is unsafe.
    2. Length delta second — only meaningful when we have a baseline.

    A proposal that trips both guards is reported against the first
    one hit (forbidden fragment); the counter is therefore
    incremented once per rejection, not once per tripped guard.
    """
    frag = _contains_forbidden_fragment(new_prompt)
    if frag is not None:
        evolution_unsafe_prompt_total.labels(
            reason=REASON_FORBIDDEN_FRAGMENT
        ).inc()
        logger.warning(
            "prompt_patch guard: forbidden fragment %r detected "
            "(baseline_source=%s)",
            frag,
            baseline_source,
        )
        return PromptPatchGuardResult(
            passed=False,
            reason=REASON_FORBIDDEN_FRAGMENT,
            detail=f"new_prompt contains forbidden fragment {frag!r}",
            baseline_source=baseline_source,
        )

    warnings: list[str] = []
    if baseline_prompt is None:
        warnings.append(
            "no_baseline_available:length_check_skipped"
        )
        return PromptPatchGuardResult(
            passed=True,
            warnings=warnings,
            baseline_source="none",
        )

    ratio = _length_delta_ratio(new_prompt, baseline_prompt)
    if ratio > MAX_LENGTH_DELTA_RATIO:
        evolution_unsafe_prompt_total.labels(
            reason=REASON_LENGTH_DELTA
        ).inc()
        logger.warning(
            "prompt_patch guard: length delta %.2f%% exceeds %.0f%% "
            "(new=%d, baseline=%d, baseline_source=%s)",
            ratio * 100,
            MAX_LENGTH_DELTA_RATIO * 100,
            len(new_prompt),
            len(baseline_prompt),
            baseline_source,
        )
        return PromptPatchGuardResult(
            passed=False,
            reason=REASON_LENGTH_DELTA,
            detail=(
                f"new_prompt length {len(new_prompt)} differs from baseline "
                f"{len(baseline_prompt)} by {ratio * 100:.1f}% "
                f"(limit {MAX_LENGTH_DELTA_RATIO * 100:.0f}%)"
            ),
            baseline_source=baseline_source,
        )

    return PromptPatchGuardResult(
        passed=True,
        warnings=warnings,
        baseline_source=baseline_source,
    )


# ---------------------------------------------------------------------------
# Async entry point — resolves the baseline via the repository
# ---------------------------------------------------------------------------


async def _resolve_baseline_prompt(
    sub_agent_name: str,
    *,
    db_factory: Any | None,
) -> tuple[str | None, str]:
    """Return ``(baseline_prompt, baseline_source)`` for ``sub_agent_name``.

    Priority:

    1. Active row in ``sub_agent_prompt_versions`` (via the repository).
    2. Code-level default from ``_DEFAULT_SUBAGENT_PROMPTS``.
    3. ``None`` → caller skips the length check.

    Imports are deferred so this module stays testable without
    pulling in the full SQLAlchemy engine or the deep_agent module
    tree. A missing/unavailable DB (e.g. during unit tests that
    don't inject a factory) simply falls through to the code
    default lookup.
    """
    # 1) Try the DB repository.
    if db_factory is not None:
        try:
            from src.services.prompt_versions.repository import (
                SubAgentPromptVersionRepository,
            )

            repo = SubAgentPromptVersionRepository(session_factory=db_factory)
            row = await repo.get_active(sub_agent_name)
            if row is not None and row.system_prompt:
                return row.system_prompt, "db"
        except Exception:
            # Repository failure is non-fatal: fall through to the
            # code-default path. The guard should never take down the
            # reflection cycle because of a transient DB issue.
            logger.exception(
                "prompt_patch guard: failed to fetch active prompt for %r",
                sub_agent_name,
            )

    # 2) Fall back to the code-level default.
    default = _load_default_prompt(sub_agent_name)
    if default is not None:
        return default, "default"

    # 3) Nothing to compare against.
    return None, "none"


def _load_default_prompt(sub_agent_name: str) -> str | None:
    """Look up ``sub_agent_name`` in ``_DEFAULT_SUBAGENT_PROMPTS``.

    Deferred import keeps this module light — ``deep_agent`` pulls a
    significant dependency graph (LangChain + middleware + tools).
    Returning ``None`` on import failure is intentional; we'd rather
    skip the length check than crash the guard.
    """
    try:
        from src.agent import deep_agent  # type: ignore[import-not-found]

        defaults = getattr(deep_agent, "_DEFAULT_SUBAGENT_PROMPTS", None)
        if isinstance(defaults, dict):
            val = defaults.get(sub_agent_name)
            if isinstance(val, str) and val:
                return val
    except Exception:
        logger.debug(
            "prompt_patch guard: could not load default prompts for %r",
            sub_agent_name,
            exc_info=True,
        )
    return None


async def apply_prompt_patch_guards(
    *,
    sub_agent_name: str,
    new_prompt: str,
    db_factory: Any | None = None,
    baseline_prompt: str | None = None,
) -> PromptPatchGuardResult:
    """Async guard entry point that resolves the baseline automatically.

    Args:
        sub_agent_name: target sub-agent for the proposed patch.
        new_prompt: replacement system prompt proposed by the
            reflector.
        db_factory: ``async_session_factory``-compatible callable.
            When ``None``, skips the DB lookup; the guard falls back
            to :func:`_load_default_prompt`. Tests typically pass
            ``None`` plus a monkey-patched default map.
        baseline_prompt: optional override — when the caller already
            knows the baseline (e.g. inside the Promoter, which just
            loaded the previous active version) they can pass it
            directly to bypass the DB roundtrip. ``baseline_source``
            is reported as ``"injected"`` in that case.

    Returns:
        :class:`PromptPatchGuardResult`. Metric side-effects happen
        inside :func:`evaluate_prompt_patch_guards`.
    """
    if baseline_prompt is not None:
        return evaluate_prompt_patch_guards(
            new_prompt,
            baseline_prompt,
            baseline_source="injected",
        )

    resolved, source = await _resolve_baseline_prompt(
        sub_agent_name, db_factory=db_factory
    )
    return evaluate_prompt_patch_guards(
        new_prompt,
        resolved,
        baseline_source=source,
    )


__all__ = [
    "MAX_LENGTH_DELTA_RATIO",
    "PromptPatchGuardResult",
    "REASON_FORBIDDEN_FRAGMENT",
    "REASON_LENGTH_DELTA",
    "apply_prompt_patch_guards",
    "evaluate_prompt_patch_guards",
]
