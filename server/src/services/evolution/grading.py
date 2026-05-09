"""Grading prompt harness — task 22.2.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 22.2
(Phase K — Evaluator). Covers:

* **R-3.6** — Promotion transitions (``shadow → ab``, ``ab → active``)
  require ``candidate_score >= baseline_score - ε`` (ε=0.02). This
  harness produces the per-item scores those aggregates are built from.
* **Design.md § Evaluator** — ``GradeLLM(run, item)`` called with
  deterministic settings (``temperature=0``, fixed seed) and cached
  per ``(run_sha, item_id, active_version)`` for 24h so two successive
  evaluation cycles on an unchanged candidate / item pair don't burn
  duplicate tokens.

Split responsibilities with :mod:`src.workers.tasks.evaluator` (task
22.1): the evaluator orchestrates baseline/candidate agent runs and
calls :func:`grade` once per (run, item) pair. Everything to do with
*producing* a score from one run against one item lives here.

Design goals:

* Pure async. No Celery decorator, no background loops — caller owns
  concurrency. Makes the harness reusable from one-off scripts, from
  :mod:`tests.evolution.test_no_score_regression`, and from the
  Evaluator worker alike.
* Deterministic. :data:`GRADING_TEMPERATURE` = ``0.0``, :data:`GRADING_SEED`
  = ``42`` are applied to the model before invocation. An LLM that
  honours both yields reproducible rubric scores, which is what makes
  the R-3.6 epsilon comparison meaningful.
* Redis cache is an advisory optimisation, never a correctness surface.
  Cache misses fall through to the LLM; Redis errors fall back to
  "always miss" without raising. Tests that inject a fake Redis can
  assert hit/miss by inspecting cache state.
* Malformed LLM output never raises out of :func:`grade`. A best-effort
  :class:`GradingResult` with ``score=0.0`` and a diagnostic rationale
  is returned instead so the Evaluator can record the item as "LLM
  failed" without losing the rest of the batch.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


GRADING_TEMPERATURE: float = 0.0
"""Temperature passed to the LLM-as-judge.

``0.0`` pins the model to its most-likely token at every step. Combined
with :data:`GRADING_SEED`, this is the pair that makes grading
reproducible across evaluator runs — which in turn is what makes the
R-3.6 "no score regression" promotion guard reliable.
"""

GRADING_SEED: int = 42
"""Fixed seed for LLM-as-judge. Providers that support ``seed`` (OpenAI
function-calling endpoints, DeepSeek Chat) will return deterministic
responses. Providers that ignore it won't — the cache still covers
them because identical inputs produce the same cache key.
"""

GRADING_CACHE_TTL_SECONDS: int = 86_400
"""24h TTL on grading cache entries. Matches design.md § Risks ("eval
caching relevant for 24h"). Keeps bucket growth bounded even on eval
sets that churn frequently.
"""

CACHE_KEY_PREFIX: str = "eval:grade:"
"""Redis key prefix for grading cache entries.

Full key format: ``eval:grade:{run_sha}:{item_id}:{active_version}``
where ``run_sha`` is :func:`compute_run_sha` and ``active_version`` is
a caller-provided identifier for "which active prompt version is
serving baseline" — when it changes, baseline runs invalidate
automatically.
"""


SYSTEM_PROMPT = """You are a grading judge for an AIOps agent evaluation harness.

You receive:
1. The eval item (user prompt, expected tools, expected outcome,
   per-rubric criteria).
2. One agent run's final output and the tools it used.

You grade the run against every rubric in the item and produce a
weighted composite score in [0.0, 1.0]. Lower is worse, higher is
better. Be calibrated: 1.0 is reserved for runs that leave the
evaluator with no concrete improvement to request.

Return ONE JSON object, no code fences, no commentary, with this exact
shape:

{
  "score": <float in [0.0, 1.0]>,
  "per_rubric": {"<rubric_name>": <float in [0.0, 1.0]>, ...},
  "rationale": "<one paragraph explaining the score>"
}

Rules:
- "score" must equal the weight-weighted mean of "per_rubric" values.
- "per_rubric" must contain exactly one entry for each rubric the item
  defines (using the rubric's ``name`` as key).
- If a rubric can't be evaluated from the run (e.g. rubric checks tool
  behaviour but the run produced no tool call), score it 0.0 and say
  so in the rationale.
- Do not add unsolicited fields. Extra fields will be ignored but may
  waste tokens — avoid them.
"""


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GradingRun:
    """Snapshot of one agent run against one eval item.

    Carries only what the grader needs to score the run — no raw
    LangGraph state, no full message history. Keep the payload small:
    the cache key is derived from a subset of these fields so bloated
    inputs mean bloated cache entries.
    """

    output: str
    """Final assistant output the user would see."""

    tools_used: list[str] = field(default_factory=list)
    """Ordered list of tool names invoked during the run.

    Order is significant for the cache key (``tool_a,tool_b`` and
    ``tool_b,tool_a`` may represent materially different behaviours),
    so :func:`compute_run_sha` joins them as-is. Callers that want a
    canonical order should sort before constructing the run.
    """

    outcome: str = "answered"
    """One of ``"answered"``, ``"delegated"``, ``"refused"``, ``"error"``
    mirroring :attr:`EvalSetItem.expected_outcome` vocabulary.
    """

    latency_ms: int | None = None
    """Optional — not part of the cache key, not part of the default
    grading prompt. Included so downstream analytics can co-locate
    timing with scores without re-running grading.
    """

    tokens_in: int | None = None
    """Optional input-token count. Not part of the cache key."""

    tokens_out: int | None = None
    """Optional output-token count. Not part of the cache key."""


@dataclass(slots=True)
class GradingResult:
    """Output of :func:`grade`.

    ``score`` is the weighted composite in ``[0.0, 1.0]``. ``per_rubric``
    maps each rubric name to its individual score (also clipped to
    ``[0.0, 1.0]``). ``rationale`` is LLM-generated prose explaining the
    score — useful for audit logs / debugging a rejected candidate, not
    machine-parsed downstream.
    """

    score: float
    per_rubric: dict[str, float] = field(default_factory=dict)
    rationale: str = ""


# ---------------------------------------------------------------------------
# Cache-key + SHA helpers
# ---------------------------------------------------------------------------


def compute_run_sha(run: GradingRun) -> str:
    """SHA-256 digest identifying a :class:`GradingRun` for cache lookup.

    ``run_sha = sha256(run.output + "|".join(run.tools_used) + run.outcome)``
    per the task spec. Returns the hex digest (64 chars). Kept as a
    free function so tests / the cache-hit assertion in the Evaluator
    can reconstruct the expected key without re-importing the
    internal helpers.
    """
    payload = (
        (run.output or "")
        + "|".join(run.tools_used or [])
        + (run.outcome or "")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_key_for(run: GradingRun, item_id: Any, active_version: str) -> str:
    """Compose the documented ``eval:grade:{run_sha}:{item_id}:{active_version}`` key."""
    return (
        f"{CACHE_KEY_PREFIX}{compute_run_sha(run)}"
        f":{item_id}:{active_version}"
    )


# ---------------------------------------------------------------------------
# grade — public entry point
# ---------------------------------------------------------------------------


async def grade(
    run: GradingRun,
    item: Any,
    *,
    llm: Any | None = None,
    redis: Any | None = None,
    active_version: str = "default",
) -> GradingResult:
    """Grade one :class:`GradingRun` against one eval item.

    Parameters
    ----------
    run :
        The agent run to grade.
    item :
        The eval item. Duck-typed — must expose ``id`` / ``prompt`` /
        ``expected_tools`` / ``expected_outcome`` / ``grading_rubric``.
        Works with :class:`src.models.evolution.EvalSetItem` (whose
        ``grading_prompt`` column is aliased to ``grading_rubric`` in
        JSONL loaders) and with plain dicts.
    llm :
        LangChain-style chat model (``.ainvoke(messages)``). If
        omitted, :func:`_default_llm` is used.
    redis :
        Redis client with ``get(key)`` / ``set(key, value, ex=...)``.
        If omitted, the process-wide client from
        :func:`src.core.redis.get_redis` is used lazily. Any client
        error (unreachable server, etc.) degrades silently to "cache
        miss"; the harness never fails open on a broken cache.
    active_version :
        Identifier for the currently-active configuration whose
        presence on the cache key isolates baseline scores when the
        active prompt version changes. Typical values: a prompt
        version uuid, a sub-agent prompt version-no, or the string
        ``"default"`` when no version is in play.

    Returns
    -------
    GradingResult
        Never raises. Malformed LLM output returns a ``score=0.0``
        :class:`GradingResult` with a diagnostic rationale so the
        Evaluator can persist the row and continue.
    """
    item_id = _extract_item_id(item)
    cache_key = cache_key_for(run, item_id, active_version)

    # Cache read — best effort, silently tolerant of Redis errors.
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached

    # Compose grading prompt and invoke LLM.
    rubrics = _normalize_rubrics(_extract_rubric(item))
    messages = _build_messages(run=run, item=item, rubrics=rubrics)

    try:
        model = _apply_deterministic_settings(llm) if llm is not None else await _default_llm_deterministic()
    except Exception:
        logger.exception("grading: failed to acquire / configure LLM")
        return GradingResult(
            score=0.0,
            per_rubric={},
            rationale="grading_error: could not load grading LLM",
        )

    try:
        raw = await model.ainvoke(messages)
    except Exception:
        logger.exception("grading: LLM ainvoke raised")
        return GradingResult(
            score=0.0,
            per_rubric={},
            rationale="grading_error: LLM call raised",
        )

    parsed = _parse_llm_response(raw)
    if parsed is None:
        return GradingResult(
            score=0.0,
            per_rubric={},
            rationale="grading_error: malformed LLM output",
        )

    result = _coerce_result(parsed, rubrics=rubrics)

    # Cache write — best effort.
    await _cache_set(redis, cache_key, result)

    return result


# ---------------------------------------------------------------------------
# Internal helpers — LLM setup
# ---------------------------------------------------------------------------


def _apply_deterministic_settings(llm: Any) -> Any:
    """Return a model configured with ``temperature=0, seed=42``.

    The caller may pass either a raw ChatOpenAI-style model (mutable
    attributes) or a pre-configured Runnable. We set attributes when
    they exist so deterministic settings don't silently no-op on
    providers that honour them; downstream ``.ainvoke`` picks them up.

    Providers that ignore ``seed`` still win from temperature 0 plus
    the 24h cache on identical inputs.
    """
    for attr, value in (("temperature", GRADING_TEMPERATURE), ("seed", GRADING_SEED)):
        try:
            if hasattr(llm, attr):
                setattr(llm, attr, value)
        except Exception:
            # Some Runnables are frozen; we still invoke them — the
            # cache will dedupe any determinism drift we can't control.
            logger.debug("grading: could not set %s on llm", attr, exc_info=True)
    return llm


async def _default_llm_deterministic() -> Any:
    """Lazy-import the default model and apply grading settings."""
    from src.core.model_factory import get_default_model

    model = await get_default_model()
    return _apply_deterministic_settings(model)


# ---------------------------------------------------------------------------
# Internal helpers — item shape & prompt construction
# ---------------------------------------------------------------------------


def _extract_item_id(item: Any) -> str:
    """Return a string id for the item.

    Duck-typed: accepts attribute access or dict keys. Falls back to
    ``"unknown"`` so a missing id never blows up cache writes — the
    cache entry becomes effectively per-run but still functional.
    """
    for accessor in (_getattr, _getitem):
        value = accessor(item, "id")
        if value is not None:
            return str(value)
    return "unknown"


def _extract_rubric(item: Any) -> Any:
    """Extract the ``grading_rubric`` payload from the item.

    The DB model exposes it as ``grading_prompt`` (a free-form text
    column); loaders from JSONL normalise to ``grading_rubric``. We
    accept either name to keep the harness agnostic.
    """
    for key in ("grading_rubric", "grading_prompt"):
        for accessor in (_getattr, _getitem):
            value = accessor(item, key)
            if value is not None:
                return value
    return None


def _extract_item_prompt(item: Any) -> str:
    for accessor in (_getattr, _getitem):
        value = accessor(item, "prompt")
        if value is not None:
            return str(value)
    return ""


def _extract_expected_tools(item: Any) -> list[str]:
    for accessor in (_getattr, _getitem):
        value = accessor(item, "expected_tools")
        if value:
            return [str(v) for v in value]
    return []


def _extract_expected_outcome(item: Any) -> str:
    for accessor in (_getattr, _getitem):
        value = accessor(item, "expected_outcome")
        if value is not None:
            return str(value)
    return ""


def _getattr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _getitem(obj: Any, name: str) -> Any:
    try:
        if isinstance(obj, dict):
            return obj.get(name)
    except Exception:
        return None
    return None


# Rubric representation.  Internally we always work with a normalised
# list of ``{name, description, weight}`` dicts so the prompt + scoring
# side doesn't have to branch on string vs list input.
@dataclass(slots=True)
class _NormalizedRubric:
    name: str
    description: str
    weight: float


def _normalize_rubrics(raw: Any) -> list[_NormalizedRubric]:
    """Accept multiple ``grading_rubric`` shapes and return a uniform list.

    Supported inputs:

    * ``None`` → a single ``overall`` rubric so the judge still has
      something to score against.
    * ``str`` (the JSONL/DB shape — a free-form rubric paragraph) →
      wrapped as ``{name: "overall", description: <str>, weight: 1.0}``.
    * ``list[dict]`` with ``{name, description, weight}`` entries →
      used directly; missing ``weight`` defaults to ``1.0``; negative /
      zero weights coerced to ``1.0`` so they don't drop out of the
      aggregate silently.
    * Any other shape → treated as the single-overall-rubric case with
      ``str(raw)`` as the description. Defensive by design — the
      harness never raises for a malformed item.
    """
    if raw is None:
        return [_NormalizedRubric(name="overall", description="", weight=1.0)]
    if isinstance(raw, str):
        return [
            _NormalizedRubric(
                name="overall", description=raw.strip(), weight=1.0
            )
        ]
    if isinstance(raw, list):
        out: list[_NormalizedRubric] = []
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                # Render the raw value as description under a
                # synthetic name rather than dropping it — grading
                # over partial rubrics is still better than grading
                # over none.
                out.append(
                    _NormalizedRubric(
                        name=f"rubric_{i}",
                        description=str(entry),
                        weight=1.0,
                    )
                )
                continue
            name = str(entry.get("name") or f"rubric_{i}").strip() or f"rubric_{i}"
            description = str(entry.get("description") or "").strip()
            weight_raw = entry.get("weight", 1.0)
            try:
                weight = float(weight_raw)
            except (TypeError, ValueError):
                weight = 1.0
            if not weight or weight <= 0:
                weight = 1.0
            out.append(
                _NormalizedRubric(
                    name=name[:64], description=description[:2000], weight=weight
                )
            )
        if not out:
            return [_NormalizedRubric(name="overall", description="", weight=1.0)]
        return out
    # Unknown shape — stringify.
    return [
        _NormalizedRubric(
            name="overall", description=str(raw), weight=1.0
        )
    ]


def _build_messages(
    *, run: GradingRun, item: Any, rubrics: list[_NormalizedRubric]
) -> list[Any]:
    """Assemble the system + human message pair for the grader LLM.

    The user message is a single JSON block so the model can locate
    each field reliably. The rubrics block lists every criterion the
    judge must score — the system prompt binds the judge to produce
    exactly these keys in ``per_rubric``.
    """
    rubric_block = [
        {
            "name": r.name,
            "description": r.description,
            "weight": r.weight,
        }
        for r in rubrics
    ]
    payload = {
        "item": {
            "prompt": _extract_item_prompt(item),
            "expected_tools": _extract_expected_tools(item),
            "expected_outcome": _extract_expected_outcome(item),
            "rubrics": rubric_block,
        },
        "run": {
            "output": run.output,
            "tools_used": list(run.tools_used or []),
            "outcome": run.outcome,
        },
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=body),
    ]


# ---------------------------------------------------------------------------
# Internal helpers — LLM output parsing
# ---------------------------------------------------------------------------


def _parse_llm_response(response: Any) -> dict | None:
    """Parse ``response.content`` as JSON; tolerate fenced code blocks."""
    content = getattr(response, "content", response)
    if isinstance(content, (list, tuple)):
        content = "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    text = str(content or "").strip()
    if not text:
        return None
    # Strip triple-backtick fencing if the model added it despite the
    # instructions.
    if text.startswith("```"):
        _, _, rest = text.partition("\n")
        if rest.rstrip().endswith("```"):
            text = rest.rstrip()[: -3].rstrip()
        else:
            text = rest
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(
            "grading: LLM returned non-JSON content (head=%r)", text[:200]
        )
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_result(
    parsed: dict, *, rubrics: list[_NormalizedRubric]
) -> GradingResult:
    """Project the LLM JSON into a :class:`GradingResult`.

    Defensive:

    * Unknown rubric keys in the LLM output are dropped.
    * Missing rubric keys are filled with ``0.0`` so the caller always
      gets one entry per declared rubric.
    * Scores clipped to ``[0.0, 1.0]`` — a model that returns ``1.2``
      under stress shouldn't distort the weighted mean.
    * If the LLM provides an explicit ``score``, we use it (clipped).
      Otherwise we recompute the weighted mean from ``per_rubric``.
      Both are allowed because different providers format this
      differently.
    """
    raw_rubric = parsed.get("per_rubric") or {}
    per_rubric: dict[str, float] = {}
    if isinstance(raw_rubric, dict):
        for rubric in rubrics:
            raw_value = raw_rubric.get(rubric.name, 0.0)
            per_rubric[rubric.name] = _clip_score(raw_value)
    else:
        for rubric in rubrics:
            per_rubric[rubric.name] = 0.0

    # Compute authoritative composite from per_rubric via weighted mean
    # so the score always reconciles with its components.
    composite = _weighted_mean(per_rubric, rubrics)

    # If the model volunteered a ``score`` and it's within a small
    # tolerance of the recomputed composite, prefer the model's value
    # (it may contain additional calibration we couldn't recover).
    raw_score = parsed.get("score")
    if raw_score is not None:
        declared = _clip_score(raw_score)
        if abs(declared - composite) <= 0.05:
            composite = declared

    rationale = str(parsed.get("rationale") or "").strip()[:2000]

    return GradingResult(
        score=composite, per_rubric=per_rubric, rationale=rationale
    )


def _clip_score(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _weighted_mean(
    per_rubric: dict[str, float], rubrics: Iterable[_NormalizedRubric]
) -> float:
    total_w = 0.0
    weighted_sum = 0.0
    for rubric in rubrics:
        w = rubric.weight if rubric.weight > 0 else 1.0
        total_w += w
        weighted_sum += w * per_rubric.get(rubric.name, 0.0)
    if total_w == 0.0:
        return 0.0
    return max(0.0, min(1.0, weighted_sum / total_w))


# ---------------------------------------------------------------------------
# Internal helpers — Redis cache (silent failure)
# ---------------------------------------------------------------------------


async def _get_redis_client(redis: Any | None) -> Any | None:
    """Return a usable Redis client or ``None``.

    Lazy-imports :func:`src.core.redis.get_redis` so tests can import
    this module without a live Redis (and so test fixtures can inject
    a fake client explicitly via ``redis=``).
    """
    if redis is not None:
        return redis
    try:
        from src.core.redis import get_redis

        return await get_redis()
    except Exception:
        logger.debug("grading: no redis client available", exc_info=True)
        return None


async def _cache_get(redis: Any | None, key: str) -> GradingResult | None:
    """Read a cached :class:`GradingResult` or return ``None``."""
    client = await _get_redis_client(redis)
    if client is None:
        return None
    try:
        raw = await client.get(key)
    except Exception:
        logger.debug("grading: cache get failed for %s", key, exc_info=True)
        return None
    if raw is None:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        logger.warning("grading: cache entry not JSON for %s", key)
        return None
    try:
        return GradingResult(
            score=float(payload.get("score", 0.0)),
            per_rubric={
                str(k): float(v)
                for k, v in (payload.get("per_rubric") or {}).items()
            },
            rationale=str(payload.get("rationale") or ""),
        )
    except Exception:
        logger.warning("grading: cache entry malformed for %s", key)
        return None


async def _cache_set(redis: Any | None, key: str, result: GradingResult) -> None:
    """Persist a :class:`GradingResult`. Errors are swallowed."""
    client = await _get_redis_client(redis)
    if client is None:
        return
    payload = json.dumps(
        {
            "score": result.score,
            "per_rubric": result.per_rubric,
            "rationale": result.rationale,
        },
        ensure_ascii=False,
    )
    try:
        await client.set(key, payload, ex=GRADING_CACHE_TTL_SECONDS)
    except Exception:
        logger.debug("grading: cache set failed for %s", key, exc_info=True)


__all__ = [
    "GRADING_CACHE_TTL_SECONDS",
    "GRADING_SEED",
    "GRADING_TEMPERATURE",
    "CACHE_KEY_PREFIX",
    "GradingResult",
    "GradingRun",
    "cache_key_for",
    "compute_run_sha",
    "grade",
]
