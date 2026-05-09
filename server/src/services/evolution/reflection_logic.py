"""Reflection cycle — failure clustering (task 21.1).

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 21.1
(Phase J — ReflectionWorker). Covers acceptance criterion **R-3.1**:

    WHEN ``ReflectionWorker`` runs THE system SHALL pull
    ``outcome in ('error', 'timeout')`` trajectories plus sessions with
    ``count >= 3`` failures in 24h, LLM-cluster them into named groups
    with example trajectory ids.

Design goals (mirror the ``consolidation_logic`` split):

* **Pure async function.** ``cluster_failures(...)`` is the real body
  and accepts every external dependency (DB factory, LLM) via kwargs
  so unit tests can exercise the logic with no live services.
* **Thin Celery wrapper.** :func:`src.workers.tasks.reflection.run_reflection_cycle`
  only does the sync/async bridge + retry. All real work happens here.
* **Narrow scope.** This module handles steps (1) source-data pull
  and (2) LLM clustering only. Candidate generation, guards, DB
  persistence and file writes live in later sibling tasks
  (21.2–21.5) and their own modules so each subtask is independently
  reviewable.

High-level flow::

    SELECT trajectories with outcome in ('error','timeout')   (24h window, LIMIT 500)
    ↓
    SELECT sessions with COUNT(error_trajectories) >= 3       (24h window)
    ↓                                                          (union into one pool)
    render compact context per trajectory
    ↓
    LLM.ainvoke(CLUSTER_FAILURES_PROMPT)
    ↓
    parse JSON → list[FailureCluster]  (name, description, example ids, proposed_fix_type)
    ↓
    filter: drop clusters with < 1 example id
    ↓
    return ReflectionResult(clusters=..., n_trajectories_considered=...)

The returned ``ReflectionResult`` is designed to be the input of the
next subtask (21.2 — "per cluster, call CANDIDATE_GEN_PROMPT"). Keeping
the two concerns separate means the cluster → candidate pipeline is
easy to unit-test piecewise and to replay against historical data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM prompt — failure clustering
# ---------------------------------------------------------------------------


CLUSTER_FAILURES_PROMPT = """你是运维智能体的失败反思器。给定一批最近失败的轨迹
（tool_call 的 error / timeout，以及 24 小时内失败 ≥ 3 次的 session 样本）。
请把这些失败聚成若干类（cluster），每一类有：

- name: 短名（≤ 40 字，snake_case 或中文短语均可），标识这类失败
- description: 1-2 句根因假设
- example_trajectory_ids: 从输入中挑 1-5 个最能代表这类的 trajectory id
- proposed_fix_type: 建议的修复形态，取值限于
    "skill"        — 提议一个新 skill
    "prompt_patch" — 建议修改某子 agent 的 system prompt
    "tool_config"  — 建议修改工具的 config（timeout / retry / budget 等）

严格输出 JSON，格式：
{
  "clusters": [
    {
      "name": "...",
      "description": "...",
      "example_trajectory_ids": ["<uuid>", ...],
      "proposed_fix_type": "skill" | "prompt_patch" | "tool_config"
    },
    ...
  ]
}

规则：
1. 输出的 `example_trajectory_ids` 必须全部来自输入给你的 id 列表，
   不要编造新的 id。
2. 每个 cluster 至少 1 个 example id；同一 id 可以同时属于多个 cluster。
3. 明显的孤立失败（只出现一次、无共性）可以不聚类。
4. 输出 clusters 数量建议 2-6 个，极端情况（输入样本全属同一类）可为 1。
5. 只输出 JSON，不要加解释。
"""


# Default SQL window for trajectory pull. Kept as a module-level constant
# so tests can override it via the ``window`` kwarg without editing the
# prompt module.
DEFAULT_WINDOW_HOURS = 24

# Upper bound on trajectories fed to the clustering LLM in a single
# pass. 500 matches the pseudocode in design.md § Reflector.
DEFAULT_MAX_TRAJECTORIES = 500

# Minimum number of input failures required to bother invoking the LLM.
# Below this we short-circuit with an empty result — clustering noise is
# not useful and burns tokens.
MIN_TRAJECTORIES_FOR_CLUSTERING = 3

# Repeated-failure session threshold: sessions with ``count >= 3`` errors
# in the window are considered "chronically failing" and their extra
# failed-turn trajectories are folded into the clustering pool on top of
# the tool_call error/timeout pull.
REPEATED_FAILURE_THRESHOLD = 3

# Bound how many trajectories we pull per chronically-failing session so
# a single session can't drown out the rest of the pool.
REPEATED_FAILURE_PER_SESSION_CAP = 10


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FailureCluster:
    """One named group of failing trajectories produced by the LLM.

    ``example_trajectory_ids`` are validated to be a subset of the input
    pool before the cluster is returned — callers (task 21.2) can rely
    on them actually existing in ``agent_trajectories``.
    """

    name: str
    description: str
    example_trajectory_ids: list[uuid.UUID]
    proposed_fix_type: str  # one of: "skill", "prompt_patch", "tool_config"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "example_trajectory_ids": [str(i) for i in self.example_trajectory_ids],
            "proposed_fix_type": self.proposed_fix_type,
        }


@dataclass
class ReflectionResult:
    """Return value of :func:`cluster_failures` / :func:`run_reflection_cycle`.

    ``status``:
        * ``ok``        — LLM ran; ``clusters`` may be empty if it
          returned none.
        * ``skipped``   — too few failures in the window (below
          :data:`MIN_TRAJECTORIES_FOR_CLUSTERING`); no LLM call made.
        * ``empty``     — no failing trajectories at all.
        * ``error``     — LLM output was unparseable; ``reason`` filled.
    """

    status: str
    n_trajectories_considered: int = 0
    clusters: list[FailureCluster] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "n_trajectories_considered": self.n_trajectories_considered,
            "clusters": [c.to_dict() for c in self.clusters],
        }
        if self.reason is not None:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True, slots=True)
class _FailingTrajectory:
    """Compact in-memory view of one failing ``agent_trajectories`` row."""

    id: uuid.UUID
    session_id: uuid.UUID
    kind: str
    outcome: str
    created_at: datetime
    tool_name: str | None
    error_message: str | None
    tags: list[str]

    def to_llm_dict(self) -> dict[str, Any]:
        """Shape handed to the clustering LLM.

        We deliberately strip large payloads (full tool args, full LLM
        outputs) and only pass the dimensions that matter for grouping:
        kind, outcome, tool_name, a short error_message, and tags.
        """
        return {
            "id": str(self.id),
            "kind": self.kind,
            "outcome": self.outcome,
            "tool_name": self.tool_name,
            "error_message": (self.error_message or "")[:240],
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


async def cluster_failures(
    *,
    llm: Any | None = None,
    db_factory: Any | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    max_trajectories: int = DEFAULT_MAX_TRAJECTORIES,
    now: datetime | None = None,
) -> ReflectionResult:
    """Run the failure-clustering half of a reflection cycle.

    Args:
        llm: LangChain-style chat model (``.ainvoke(messages)``). If
            ``None``, :func:`_default_llm` is used.
        db_factory: ``async_session_factory``-compatible async context
            manager. If ``None``, the real one is used.
        window_hours: lookback window. Defaults to
            :data:`DEFAULT_WINDOW_HOURS` (24).
        max_trajectories: hard cap on trajectories sent to the LLM.
        now: reference time for the window. Tests can pin this to get
            deterministic SQL parameters.

    Returns:
        :class:`ReflectionResult`. Never raises for the "no data" or
        "bad LLM output" cases — those are surfaced via ``status`` +
        ``reason`` so the Celery wrapper can record a metric and move
        on without triggering retry.
    """
    factory = db_factory or _default_db_factory()
    reference = now or datetime.now(UTC)
    since = reference - timedelta(hours=int(window_hours))

    try:
        tool_failures = await _load_tool_call_failures(
            factory, since=since, limit=max_trajectories
        )
        repeated_sessions = await _load_repeated_failure_sessions(
            factory, since=since
        )
        repeated_failures: list[_FailingTrajectory] = []
        for sid in repeated_sessions:
            per_session = await _load_session_failures(
                factory,
                session_id=sid,
                since=since,
                limit=REPEATED_FAILURE_PER_SESSION_CAP,
            )
            repeated_failures.extend(per_session)
    except Exception:
        logger.exception("reflection: failed to pull failing trajectories")
        raise

    # Union + dedupe on id, preserving insertion order (tool failures
    # first so they carry the most weight in downstream rendering).
    seen: set[uuid.UUID] = set()
    pool: list[_FailingTrajectory] = []
    for traj in (*tool_failures, *repeated_failures):
        if traj.id in seen:
            continue
        seen.add(traj.id)
        pool.append(traj)
        if len(pool) >= max_trajectories:
            break

    if not pool:
        return ReflectionResult(status="empty", n_trajectories_considered=0)

    if len(pool) < MIN_TRAJECTORIES_FOR_CLUSTERING:
        return ReflectionResult(
            status="skipped",
            n_trajectories_considered=len(pool),
            reason=f"below_min_{MIN_TRAJECTORIES_FOR_CLUSTERING}",
        )

    model = llm if llm is not None else await _default_llm()
    raw = await _invoke_cluster_llm(model, pool)
    if raw is None:
        return ReflectionResult(
            status="error",
            n_trajectories_considered=len(pool),
            reason="llm_output_invalid",
        )

    clusters = _parse_clusters(raw, valid_ids={t.id for t in pool})
    return ReflectionResult(
        status="ok",
        n_trajectories_considered=len(pool),
        clusters=clusters,
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _default_db_factory() -> Any:
    from src.models.base import async_session_factory

    return async_session_factory


async def _default_llm() -> Any:
    from src.core.model_factory import get_default_model

    return await get_default_model()


async def _load_tool_call_failures(
    factory: Any, *, since: datetime, limit: int
) -> list[_FailingTrajectory]:
    """Pull ``kind='tool_call'`` rows with ``outcome in ('error','timeout')``.

    The raw SQL is kept thin so the FakeDB in tests can pattern-match
    on a stable prefix. We select only the columns the clustering step
    uses — the full ``data`` JSONB is left behind to avoid pulling
    multi-MB payloads into memory.
    """
    async with factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, session_id, kind, outcome, created_at, data, tags
                FROM agent_trajectories
                WHERE kind = 'tool_call'
                  AND outcome IN ('error', 'timeout')
                  AND created_at > :since
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"since": since, "limit": int(limit)},
        )
        items = rows.fetchall()
    return [_row_to_trajectory(r) for r in items]


async def _load_repeated_failure_sessions(
    factory: Any, *, since: datetime
) -> list[uuid.UUID]:
    """Return session ids with ``COUNT(outcome='error') >= 3`` in window.

    ``timeout`` is counted toward the threshold too — both outcomes are
    "bad user experience" for the reflection cycle.
    """
    async with factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT session_id, COUNT(*) AS n
                FROM agent_trajectories
                WHERE outcome IN ('error', 'timeout')
                  AND created_at > :since
                GROUP BY session_id
                HAVING COUNT(*) >= :threshold
                """
            ),
            {"since": since, "threshold": int(REPEATED_FAILURE_THRESHOLD)},
        )
        items = rows.fetchall()
    return [r.session_id for r in items if r.session_id is not None]


async def _load_session_failures(
    factory: Any,
    *,
    session_id: uuid.UUID,
    since: datetime,
    limit: int,
) -> list[_FailingTrajectory]:
    """Pull all failing rows for one session within the window.

    Captures non-tool-call failures (``turn`` / ``subagent`` / etc.) so
    we cluster every failure surface — not just tool errors.
    """
    async with factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, session_id, kind, outcome, created_at, data, tags
                FROM agent_trajectories
                WHERE session_id = :sid
                  AND outcome IN ('error', 'timeout')
                  AND created_at > :since
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {
                "sid": session_id,
                "since": since,
                "limit": int(limit),
            },
        )
        items = rows.fetchall()
    return [_row_to_trajectory(r) for r in items]


def _row_to_trajectory(row: Any) -> _FailingTrajectory:
    """Normalise a DB row into :class:`_FailingTrajectory`.

    ``data`` is JSONB in Postgres; asyncpg hands us a ``dict``.
    Tolerates the test FakeDB's looser typing (plain dicts / lists).
    """
    raw_data = getattr(row, "data", None) or {}
    if not isinstance(raw_data, dict):
        raw_data = {}
    raw_tags = getattr(row, "tags", None) or []
    if not isinstance(raw_tags, list):
        raw_tags = []

    tool_name = raw_data.get("tool_name") or raw_data.get("name")
    err = raw_data.get("error_message") or raw_data.get("error") or raw_data.get("reason")

    return _FailingTrajectory(
        id=row.id if isinstance(row.id, uuid.UUID) else uuid.UUID(str(row.id)),
        session_id=(
            row.session_id
            if isinstance(row.session_id, uuid.UUID)
            else uuid.UUID(str(row.session_id))
        ),
        kind=str(row.kind),
        outcome=str(row.outcome),
        created_at=row.created_at,
        tool_name=str(tool_name) if tool_name is not None else None,
        error_message=str(err) if err is not None else None,
        tags=[str(t) for t in raw_tags],
    )


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


async def _invoke_cluster_llm(
    llm: Any, pool: list[_FailingTrajectory]
) -> dict | None:
    """Ask the LLM to cluster *pool*; return parsed JSON dict or None."""
    user_block = _render_pool(pool)
    try:
        resp = await llm.ainvoke(
            [
                SystemMessage(content=CLUSTER_FAILURES_PROMPT),
                HumanMessage(content=user_block),
            ]
        )
    except Exception:
        logger.exception("reflection: clustering LLM call failed")
        raise

    raw = getattr(resp, "content", resp)
    if isinstance(raw, (list, tuple)):
        raw = "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in raw
        )
    raw = str(raw).strip()

    # Strip ``` fencing — several providers wrap JSON in a ``` block
    # even when asked not to.
    if raw.startswith("```"):
        _, _, rest = raw.partition("\n")
        if rest.rstrip().endswith("```"):
            raw = rest.rstrip()[: -3].rstrip()
        else:
            raw = rest

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "reflection: LLM returned invalid JSON (head=%r)", raw[:200]
        )
        return None

    if not isinstance(parsed, dict):
        logger.warning(
            "reflection: LLM returned non-object JSON (type=%s)",
            type(parsed).__name__,
        )
        return None
    return parsed


def _render_pool(pool: list[_FailingTrajectory]) -> str:
    """Compact JSON-lines style block for the LLM user message.

    Plain JSON of a 500-element list is easier for the LLM than a
    bullet tree. We cap each entry via :meth:`_FailingTrajectory.to_llm_dict`
    so total input stays under ~ 100k chars.
    """
    payload = [t.to_llm_dict() for t in pool]
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        f"## 输入 trajectories（共 {len(pool)} 条）\n"
        f"{body}\n\n"
        "请按 SYSTEM 指示输出 JSON。"
    )


# ---------------------------------------------------------------------------
# Cluster parsing / validation
# ---------------------------------------------------------------------------


_ALLOWED_FIX_TYPES = frozenset({"skill", "prompt_patch", "tool_config"})


def _parse_clusters(
    raw: dict, *, valid_ids: set[uuid.UUID]
) -> list[FailureCluster]:
    """Validate the LLM's JSON and project it into :class:`FailureCluster`.

    Contract:

    * ``clusters`` must be a list; any other type → empty result.
    * Each cluster must have non-empty ``name`` and ``description``.
    * ``example_trajectory_ids`` is filtered to ids in ``valid_ids`` —
      the LLM occasionally hallucinates uuids and we don't want those
      leaking into candidate generation.
    * Clusters with zero valid ids after filtering are dropped.
    * Unknown ``proposed_fix_type`` values default to ``"skill"`` (the
      broadest fix category) so a partially-bad LLM output still gives
      us something to act on.
    """
    items = raw.get("clusters")
    if not isinstance(items, list):
        return []

    out: list[FailureCluster] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not name or not description:
            continue

        ids_raw = item.get("example_trajectory_ids") or []
        if not isinstance(ids_raw, list):
            continue
        ids: list[uuid.UUID] = []
        for candidate in ids_raw:
            try:
                parsed_id = uuid.UUID(str(candidate))
            except (TypeError, ValueError):
                continue
            if parsed_id in valid_ids and parsed_id not in ids:
                ids.append(parsed_id)
        if not ids:
            continue

        fix_type = str(item.get("proposed_fix_type") or "skill").strip()
        if fix_type not in _ALLOWED_FIX_TYPES:
            fix_type = "skill"

        out.append(
            FailureCluster(
                name=name[:120],
                description=description[:1000],
                example_trajectory_ids=ids[:5],
                proposed_fix_type=fix_type,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Task 21.2 — Candidate generation (skill | prompt_patch | tool_config)
# ---------------------------------------------------------------------------
#
# Spec: .kiro/specs/agent-runtime-optimization-evolution, task 21.2.
# Requirements: R-3.1, R-3.2, R-3.3.
#
# Per cluster we invoke CANDIDATE_GEN_PROMPT which asks the LLM to
# propose one concrete fix of the given ``proposed_fix_type``. The
# result is:
#
#     {
#       "kind": "skill" | "prompt_patch" | "tool_config",
#       "name": "...",
#       "data": { kind-specific payload },
#       "expected_improvement": "..."
#     }
#
# We then:
#
#   1. Validate the schema with pydantic per-kind (see
#      ``_CandidateLLMOutput`` + the ``_*CandidateData`` models).
#      Bad LLM output is dropped, not raised — we don't want one
#      cluster taking down the whole cycle.
#   2. De-duplicate against already-live candidates. "Live" for
#      skill + tool_config = ``skill_candidates.status`` ∈
#      {active, shadow} matched by ``name``. "Live" for prompt_patch =
#      ``sub_agent_prompt_versions.status`` ∈ {active, shadow, ab}
#      matched by ``sub_agent_name`` (we never run two candidates
#      against the same sub-agent simultaneously). We also dedupe
#      intra-batch so two clusters can't both propose the same name /
#      target.
#   3. Optionally persist the validated candidates (R-3.3):
#        * skill       → INSERT ``skill_candidates`` + write
#                         ``data/skills/.candidate/<name>/SKILL.md``.
#                         The main ``data/skills/`` directory is
#                         NEVER touched here.
#        * prompt_patch → INSERT ``sub_agent_prompt_versions`` row with
#                         ``status='proposed'``. No skill_candidates
#                         row — prompt patches live in their own table.
#        * tool_config  → INSERT ``skill_candidates`` with kind + the
#                         patch serialised into ``tags``/``tool_names``
#                         JSONB columns; no FS artefact.
#      Persistence is opt-in (``persist=True``) so unit tests can
#      exercise the validation + dedup logic without touching a DB
#      or filesystem.
#
# This keeps the reflection module a pure data pipeline when
# persistence is off; the full ``SkillCandidateStore`` refactor
# (task 21.4) can then move the persist step behind a cleaner
# abstraction without changing the validation contract.


CANDIDATE_GEN_PROMPT = """你是运维智能体的候选生成器。我给你一个 cluster（一类失败）
以及挑好的样本 trajectories，请输出**一个**候选修复方案。

cluster 的 `proposed_fix_type` 决定了候选的 `kind`，三种类型严格对应：

- "skill"        → 提议新 skill / 或对现有 skill 的补丁。
- "prompt_patch" → 建议修改某子 agent 的 system prompt。
- "tool_config"  → 建议修改某工具的 config（timeout / retry / budget 等）。

严格输出 JSON，格式如下（字段全部必填）：

{
  "kind": "skill" | "prompt_patch" | "tool_config",
  "name": "...",                             // 短名；prompt_patch / tool_config
                                              // 的 name 必须以 "<target>_" 开头
  "data": { ... },                           // kind 决定的 schema，见下
  "expected_improvement": "..."              // 1-2 句说明修复预期带来的改善
}

## data schema（按 kind）

skill:
{
  "skill_prompt": "...",     // DeepAgents SKILL.md 正文（≥ 50 字）
  "description":  "...",     // 1 句话说明这个 skill 做什么
  "tags":         ["..."],   // 1-5 个
  "tool_names":   ["..."]    // skill 使用到的工具名
}

prompt_patch:
{
  "sub_agent_name": "...",   // knowledge / monitor / ops / analysis / ...
  "new_prompt":     "...",   // 完整替换的新 system prompt
  "rationale":      "..."    // 为什么要改
}

tool_config:
{
  "tool_name":  "...",
  "patch":      { ... },     // 将 merge 到 tools.config 的 JSON patch
  "rationale":  "..."
}

规则：
1. `kind` 必须等于 cluster 的 `proposed_fix_type`。
2. `name` 短而明确（≤ 120 字），避免和已有 skill 撞名。
3. skill 的 `skill_prompt` 必须是完整可用的 SKILL.md 正文。
4. prompt_patch 的 `new_prompt` 不得包含"ignore prior instructions" 等
   降级安全约束的片段。
5. tool_config 的 `patch` 只能是 plain JSON（dict），不要放对象引用。
6. 只输出 JSON，不要加解释文字、不要 ``` 包围。
"""


# Allowed values for CandidateProposal.kind.
_ALLOWED_CANDIDATE_KINDS = frozenset({"skill", "prompt_patch", "tool_config"})

# Maximum length of a skill prompt (Sanity cap — real promoter has its
# own size guards; we just keep the reflection output bounded).
_SKILL_PROMPT_MIN_LEN = 50

# Statuses that count as "already live" for dedup. A new candidate
# with the same name as any active or shadow candidate is dropped
# (we never replace something currently serving traffic — rely on the
# promoter's rollback chain for that).
LIVE_CANDIDATE_STATUSES = ("active", "shadow")

# Forbidden fragments inside prompt_patch.new_prompt. Matching any of
# these triggers a hard reject at generation time. Task 21.3 adds more
# fine-grained guards (including length delta) — this list is only
# the "never allow" subset so we never *propose* an unsafe prompt
# even if downstream guards were accidentally skipped.
_FORBIDDEN_PROMPT_FRAGMENTS = (
    "ignore prior instructions",
    "ignore previous instructions",
    "disregard all prior",
    "disregard previous instructions",
    "忽略之前的指令",
    "忽略上面所有指令",
)


# ---------------------------------------------------------------------------
# Pydantic schemas — per-kind ``data`` payloads
# ---------------------------------------------------------------------------


class _SkillCandidateData(BaseModel):
    """``data`` payload for ``kind="skill"`` candidates (R-3.2)."""

    model_config = ConfigDict(extra="ignore")

    skill_prompt: str
    description: str
    tags: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)

    @field_validator("skill_prompt", mode="before")
    @classmethod
    def _strip_skill_prompt(cls, v: Any) -> str:
        s = str(v or "").strip()
        if len(s) < _SKILL_PROMPT_MIN_LEN:
            raise ValueError(
                f"skill_prompt must be at least {_SKILL_PROMPT_MIN_LEN} chars"
            )
        return s

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("description must not be empty")
        return s[:500]

    @field_validator("tags", mode="before")
    @classmethod
    def _norm_tags(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("tags must be a list")
        out: list[str] = []
        for t in v:
            s = str(t).strip()
            if s:
                out.append(s)
        return out[:8]

    @field_validator("tool_names", mode="before")
    @classmethod
    def _norm_tool_names(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("tool_names must be a list")
        out: list[str] = []
        for t in v:
            s = str(t).strip()
            if s:
                out.append(s)
        return out[:32]


class _PromptPatchCandidateData(BaseModel):
    """``data`` payload for ``kind="prompt_patch"`` candidates (R-3.2)."""

    model_config = ConfigDict(extra="ignore")

    sub_agent_name: str
    new_prompt: str
    rationale: str = ""

    @field_validator("sub_agent_name", mode="before")
    @classmethod
    def _strip_sub_agent_name(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("sub_agent_name must not be empty")
        return s[:64]

    @field_validator("new_prompt", mode="before")
    @classmethod
    def _validate_new_prompt(cls, v: Any) -> str:
        s = str(v or "").strip()
        if len(s) < _SKILL_PROMPT_MIN_LEN:
            raise ValueError(
                f"new_prompt must be at least {_SKILL_PROMPT_MIN_LEN} chars"
            )
        lowered = s.lower()
        for frag in _FORBIDDEN_PROMPT_FRAGMENTS:
            if frag in lowered:
                raise ValueError(f"new_prompt contains forbidden fragment {frag!r}")
        return s

    @field_validator("rationale", mode="before")
    @classmethod
    def _strip_rationale(cls, v: Any) -> str:
        return str(v or "").strip()[:2000]


class _ToolConfigCandidateData(BaseModel):
    """``data`` payload for ``kind="tool_config"`` candidates (R-3.2)."""

    model_config = ConfigDict(extra="ignore")

    tool_name: str
    patch: dict[str, Any]
    rationale: str = ""

    @field_validator("tool_name", mode="before")
    @classmethod
    def _strip_tool_name(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("tool_name must not be empty")
        return s[:128]

    @field_validator("patch", mode="before")
    @classmethod
    def _validate_patch(cls, v: Any) -> dict[str, Any]:
        if not isinstance(v, dict) or not v:
            raise ValueError("patch must be a non-empty dict")
        # Round-trip through JSON to ensure the patch is serialisable —
        # catches object references or ``datetime`` payloads the LLM
        # can occasionally hallucinate.
        try:
            json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError) as err:
            raise ValueError(f"patch is not JSON-serialisable: {err}") from err
        return v

    @field_validator("rationale", mode="before")
    @classmethod
    def _strip_rationale(cls, v: Any) -> str:
        return str(v or "").strip()[:2000]


class _CandidateLLMOutput(BaseModel):
    """Top-level pydantic schema for CANDIDATE_GEN_PROMPT output.

    We post-process the ``data`` field with a kind-specific model in
    :func:`_build_proposal` rather than using a Union here — the
    discriminator pattern would require the LLM to label the data
    payload itself, which isn't in our prompt contract.
    """

    model_config = ConfigDict(extra="ignore")

    kind: Literal["skill", "prompt_patch", "tool_config"]
    name: str
    data: dict[str, Any]
    expected_improvement: str

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("name must not be empty")
        if len(s) > 128:
            raise ValueError("name must be <= 128 chars")
        return s

    @field_validator("expected_improvement", mode="before")
    @classmethod
    def _strip_expected(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("expected_improvement must not be empty")
        return s[:2000]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateProposal:
    """One validated, deduped candidate ready for the candidate store.

    Immutable value object handed to :func:`persist_candidate_proposal`
    (or directly surfaced in ``CandidateGenerationResult`` when
    ``persist=False``). Keeping the boundary here lets the reflector
    be replayed against historical clusters without touching
    production storage; task 21.4 will move the persistence step to a
    proper :class:`SkillCandidateStore` but the proposal contract
    stays the same.

    ``cluster_name`` and ``origin_trajectory_ids`` carry cluster
    context forward so the store can populate
    ``skill_candidates.origin_trajectory_ids`` without re-querying.
    """

    kind: str                              # skill | prompt_patch | tool_config
    name: str
    data: dict[str, Any]
    expected_improvement: str
    cluster_name: str
    origin_trajectory_ids: list[uuid.UUID]

    @property
    def target_ref(self) -> str | None:
        """Target the candidate patches (sub-agent or tool name).

        Read off ``data`` since the store persists it to
        ``skill_candidates.target_ref``. ``None`` for kind=skill
        (skill candidates don't patch anything — they create a new
        capability).
        """
        if self.kind == "prompt_patch":
            name = self.data.get("sub_agent_name")
            return str(name) if name else None
        if self.kind == "tool_config":
            name = self.data.get("tool_name")
            return str(name) if name else None
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "data": self.data,
            "expected_improvement": self.expected_improvement,
            "cluster_name": self.cluster_name,
            "origin_trajectory_ids": [str(i) for i in self.origin_trajectory_ids],
            "target_ref": self.target_ref,
        }


@dataclass(frozen=True, slots=True)
class PersistedCandidate:
    """Result of :func:`persist_candidate_proposal`.

    ``row_id`` is the PK of whichever table the candidate landed in —
    ``skill_candidates.id`` for skill / tool_config, or
    ``sub_agent_prompt_versions.id`` for prompt_patch. Distinguishing
    them lets downstream tasks (evaluator, promoter) route by
    ``kind`` + ``row_id`` without a second lookup.

    ``artifact_path`` is populated for ``kind="skill"`` only; it's
    the path to the freshly-written SKILL.md under
    ``data/skills/.candidate/<name>/``. ``None`` for the other kinds.
    """

    kind: str
    name: str
    row_id: uuid.UUID
    table: str
    artifact_path: Path | None


@dataclass
class CandidateGenerationResult:
    """Return value of :func:`generate_candidates`.

    Exposes ``proposals`` alongside summary counters so the caller
    can emit a single "reflection produced N candidates, dropped M
    invalid, M_dup deduped" log line + Prometheus counters. When
    :func:`generate_candidates` is called with ``persist=True`` the
    ``persisted`` list carries the post-write identifiers.
    """

    proposals: list[CandidateProposal] = field(default_factory=list)
    persisted: list[PersistedCandidate] = field(default_factory=list)
    n_clusters_input: int = 0
    n_llm_invoked: int = 0
    n_llm_failed: int = 0
    n_invalid_schema: int = 0
    n_deduped: int = 0          # dropped because name already live or duplicate in batch
    n_persist_failed: int = 0
    n_rejected_by_guard: int = 0  # prompt_patch rejected by guards (R-3.11/R-3.12)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposals": [p.to_dict() for p in self.proposals],
            "persisted": [
                {
                    "kind": p.kind,
                    "name": p.name,
                    "row_id": str(p.row_id),
                    "table": p.table,
                    "artifact_path": str(p.artifact_path) if p.artifact_path else None,
                }
                for p in self.persisted
            ],
            "n_clusters_input": self.n_clusters_input,
            "n_llm_invoked": self.n_llm_invoked,
            "n_llm_failed": self.n_llm_failed,
            "n_invalid_schema": self.n_invalid_schema,
            "n_deduped": self.n_deduped,
            "n_persist_failed": self.n_persist_failed,
            "n_rejected_by_guard": self.n_rejected_by_guard,
        }


# ---------------------------------------------------------------------------
# Main entry — generate_candidates
# ---------------------------------------------------------------------------


async def generate_candidates(
    clusters: list[FailureCluster],
    *,
    llm: Any | None = None,
    db_factory: Any | None = None,
    existing_skill_names: set[str] | None = None,
    existing_prompt_targets: set[str] | None = None,
    persist: bool = False,
    skills_root_dir: Path | None = None,
    proposal_source: str = "reflection_worker",
) -> CandidateGenerationResult:
    """Generate one candidate per cluster.

    Args:
        clusters: output of :func:`cluster_failures`. An empty list
            short-circuits to an empty result with no LLM calls.
        llm: LangChain-style chat model (``.ainvoke(messages)``). If
            ``None``, :func:`_default_llm` is used.
        db_factory: ``async_session_factory``-compatible async context
            manager. Used to fetch the dedup sets and, when
            ``persist=True``, to insert the rows. If both
            ``existing_skill_names`` AND ``existing_prompt_targets``
            are passed AND ``persist=False``, the DB factory is
            unused and unit tests can skip setting it up.
        existing_skill_names: pre-computed dedup set for
            skill / tool_config. Matched case-sensitively against
            :class:`CandidateProposal.name` (which maps to
            ``skill_candidates.name``).
        existing_prompt_targets: pre-computed dedup set of
            ``sub_agent_name`` values that already have a live
            (proposed/shadow/ab/active) row in
            ``sub_agent_prompt_versions`` (R-3.3). A prompt_patch
            candidate whose target is already live is dropped.
        persist: when True, write each validated candidate to the DB
            and (for ``kind="skill"``) its SKILL.md under
            ``<skills_root_dir>/.candidate/<name>/SKILL.md``. The
            main ``data/skills/`` directory is never written to
            (R-3.3). Persistence failures are counted but don't abort
            the batch.
        skills_root_dir: filesystem root for skill artefacts.
            Defaults to ``<server>/data/skills``. Tests typically
            inject a ``tmp_path``.
        proposal_source: label stored in ``skill_candidates.proposal_source``
            / ``sub_agent_prompt_versions.rationale``. Defaults to
            ``"reflection_worker"``; ``SkillReviewAgent`` (task 21.6)
            will pass ``"skill_review_agent"``.

    Returns:
        :class:`CandidateGenerationResult`. The function never raises
        for per-cluster problems (bad LLM output, schema violations,
        persistence errors) — those are surfaced via counters so the
        Celery wrapper can log + move on without triggering a retry
        storm. LLM transport exceptions bubble so Celery's retry
        machinery fires.

    Notes:
        * Dedup sets are matched **case-sensitively** — the DB columns
          are case-sensitive too.
        * A cluster can contribute at most one candidate. If the LLM
          returns something unusable we drop the cluster; we don't
          retry with a different prompt here (task 21.3 adds guards,
          not retries).
    """
    result = CandidateGenerationResult(n_clusters_input=len(clusters))
    if not clusters:
        return result

    # Load dedup sets if the caller didn't pre-compute them. We pull
    # both sets up-front rather than per-cluster so a batch of, say,
    # 10 clusters does one DB query per table instead of 10.
    need_skill_dedup = existing_skill_names is None
    need_prompt_dedup = existing_prompt_targets is None
    if (need_skill_dedup or need_prompt_dedup or persist):
        factory = db_factory or _default_db_factory()
    else:
        factory = None
    if need_skill_dedup:
        existing_skill_names = await _load_live_candidate_names(factory)
    if need_prompt_dedup:
        existing_prompt_targets = await _load_live_prompt_version_targets(factory)

    # Runtime-narrowed non-None sets (type checker helper).
    live_skill_names: set[str] = existing_skill_names or set()
    live_prompt_targets: set[str] = existing_prompt_targets or set()

    model = llm if llm is not None else await _default_llm()

    batch_skill_names: set[str] = set()
    batch_prompt_targets: set[str] = set()
    for cluster in clusters:
        result.n_llm_invoked += 1
        raw = await _invoke_candidate_llm(model, cluster)
        if raw is None:
            result.n_llm_failed += 1
            continue

        proposal = _build_proposal(raw, cluster)
        if proposal is None:
            result.n_invalid_schema += 1
            continue

        # Per-kind dedup. Skill + tool_config share the
        # ``skill_candidates.name`` namespace; prompt_patch uses
        # ``sub_agent_prompt_versions.sub_agent_name``.
        if proposal.kind == "prompt_patch":
            target = proposal.target_ref or ""
            if target in live_prompt_targets or target in batch_prompt_targets:
                result.n_deduped += 1
                logger.info(
                    "reflection: prompt_patch for %r dropped (dup vs %s)",
                    target,
                    "live" if target in live_prompt_targets else "batch",
                )
                continue
            # R-3.11 / R-3.12 — forbidden-fragment + length-delta guard.
            # Pydantic already drops obvious jailbreak fragments at
            # ``_build_proposal`` time (see ``_FORBIDDEN_PROMPT_FRAGMENTS``),
            # but the guard is the single pipeline that *always*
            # increments ``evolution_unsafe_prompt_total`` + enforces
            # the 50% length-delta bound against the current active
            # prompt (which pydantic can't do without a DB lookup).
            # Lazy import keeps this module free of a hard dependency
            # on the metrics client at import time — the metric is
            # optional for isolated unit tests.
            from src.services.evolution.prompt_patch_guards import (
                apply_prompt_patch_guards,
            )

            guard_result = await apply_prompt_patch_guards(
                sub_agent_name=target,
                new_prompt=str(proposal.data.get("new_prompt") or ""),
                db_factory=factory,
            )
            if not guard_result.passed:
                result.n_rejected_by_guard += 1
                logger.info(
                    "reflection: prompt_patch for %r rejected by guard: %s (%s)",
                    target,
                    guard_result.reason,
                    guard_result.detail,
                )
                continue
            batch_prompt_targets.add(target)
        else:
            if (
                proposal.name in live_skill_names
                or proposal.name in batch_skill_names
            ):
                result.n_deduped += 1
                logger.info(
                    "reflection: candidate %r dropped (dup vs %s)",
                    proposal.name,
                    "live" if proposal.name in live_skill_names else "batch",
                )
                continue
            batch_skill_names.add(proposal.name)

        result.proposals.append(proposal)

        if persist:
            try:
                persisted = await persist_candidate_proposal(
                    proposal,
                    db_factory=factory,
                    skills_root_dir=skills_root_dir,
                    proposal_source=proposal_source,
                )
            except Exception:
                logger.exception(
                    "reflection: persist failed for candidate %r", proposal.name
                )
                result.n_persist_failed += 1
                continue
            result.persisted.append(persisted)

    return result


# ---------------------------------------------------------------------------
# Helpers — LLM call
# ---------------------------------------------------------------------------


async def _invoke_candidate_llm(
    llm: Any, cluster: FailureCluster
) -> dict | None:
    """Ask the LLM to produce one candidate for *cluster*.

    Behaves like :func:`_invoke_cluster_llm`: logs on malformed output
    and returns ``None`` (never raises for parse errors). Any
    transport-level exception propagates so Celery can retry.
    """
    user_block = _render_cluster_for_candidate(cluster)
    try:
        resp = await llm.ainvoke(
            [
                SystemMessage(content=CANDIDATE_GEN_PROMPT),
                HumanMessage(content=user_block),
            ]
        )
    except Exception:
        logger.exception(
            "reflection: candidate LLM call failed for cluster=%r", cluster.name
        )
        raise

    raw = getattr(resp, "content", resp)
    if isinstance(raw, (list, tuple)):
        raw = "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in raw
        )
    raw = str(raw).strip()

    # Strip triple-backtick fencing.
    if raw.startswith("```"):
        _, _, rest = raw.partition("\n")
        if rest.rstrip().endswith("```"):
            raw = rest.rstrip()[: -3].rstrip()
        else:
            raw = rest

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "reflection: candidate LLM returned invalid JSON "
            "(cluster=%r, head=%r)",
            cluster.name,
            raw[:200],
        )
        return None

    if not isinstance(parsed, dict):
        logger.warning(
            "reflection: candidate LLM returned non-object JSON "
            "(cluster=%r, type=%s)",
            cluster.name,
            type(parsed).__name__,
        )
        return None
    return parsed


def _render_cluster_for_candidate(cluster: FailureCluster) -> str:
    """User-message block for CANDIDATE_GEN_PROMPT.

    Compact JSON so the LLM parses it unambiguously. Example ids are
    kept so the LLM can reference them in ``expected_improvement``.
    """
    payload = {
        "cluster": {
            "name": cluster.name,
            "description": cluster.description,
            "proposed_fix_type": cluster.proposed_fix_type,
            "example_trajectory_ids": [str(i) for i in cluster.example_trajectory_ids],
        }
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        f"## cluster 信息\n{body}\n\n"
        f"请按 SYSTEM 指示输出一个候选。cluster.proposed_fix_type "
        f"= {cluster.proposed_fix_type!r}，输出的 kind 必须与之一致。"
    )


# ---------------------------------------------------------------------------
# Helpers — schema validation / proposal construction
# ---------------------------------------------------------------------------


def _build_proposal(
    raw: dict, cluster: FailureCluster
) -> CandidateProposal | None:
    """Coerce LLM output into a :class:`CandidateProposal` or ``None``.

    All validation is local so bad output from one cluster doesn't
    affect the others. The top-level shape is validated by
    :class:`_CandidateLLMOutput`; the kind-specific ``data`` payload
    is then validated by one of ``_SkillCandidateData`` /
    ``_PromptPatchCandidateData`` / ``_ToolConfigCandidateData``.

    Failure modes (all return ``None``):

    * top-level fields missing or empty
    * ``kind`` doesn't match ``cluster.proposed_fix_type`` (LLM drifted)
    * per-kind ``data`` payload fails pydantic validation
    """
    try:
        top = _CandidateLLMOutput.model_validate(raw)
    except ValidationError as err:
        logger.warning(
            "reflection: candidate top-level validation failed (cluster=%r): %s",
            cluster.name,
            err.errors()[:3],
        )
        return None

    # R-3.2: enforce that kind matches the cluster's requested fix type.
    # The LLM is told this explicitly in the prompt; drifting means the
    # output is unreliable and should be dropped.
    if top.kind != cluster.proposed_fix_type:
        logger.warning(
            "reflection: candidate kind drift (cluster=%r wanted=%s got=%s)",
            cluster.name,
            cluster.proposed_fix_type,
            top.kind,
        )
        return None

    # Kind-specific ``data`` payload validation.
    try:
        if top.kind == "skill":
            typed = _SkillCandidateData.model_validate(top.data)
        elif top.kind == "prompt_patch":
            typed = _PromptPatchCandidateData.model_validate(top.data)
        else:  # tool_config
            typed = _ToolConfigCandidateData.model_validate(top.data)
    except ValidationError as err:
        logger.warning(
            "reflection: candidate data validation failed "
            "(cluster=%r kind=%s): %s",
            cluster.name,
            top.kind,
            err.errors()[:3],
        )
        return None

    return CandidateProposal(
        kind=top.kind,
        name=top.name,
        data=typed.model_dump(),
        expected_improvement=top.expected_improvement,
        cluster_name=cluster.name,
        origin_trajectory_ids=list(cluster.example_trajectory_ids),
    )


# ---------------------------------------------------------------------------
# Helpers — live-candidate dedup lookup
# ---------------------------------------------------------------------------


async def _load_live_candidate_names(factory: Any) -> set[str]:
    """Return the set of names with ``status`` in
    :data:`LIVE_CANDIDATE_STATUSES`.

    Pulled once per reflection cycle (not per cluster) — we assume
    the set is small relative to cluster count, and a single snapshot
    is fine for dedup purposes.
    """
    async with factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT DISTINCT name
                FROM skill_candidates
                WHERE status IN ('active', 'shadow')
                """
            )
        )
        items = rows.fetchall()
    return {str(r.name) for r in items if r.name is not None}


async def _load_live_prompt_version_targets(factory: Any) -> set[str]:
    """Return sub_agent_names with a live (non-terminal) prompt version.

    A prompt-patch candidate for a sub-agent that already has a live
    (proposed/shadow/ab/active) row in ``sub_agent_prompt_versions``
    is deduped — we never queue two candidates for the same sub-agent
    in parallel (R-3.3). ``retired`` and ``rejected`` rows don't
    block new candidates.
    """
    async with factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT DISTINCT sub_agent_name
                FROM sub_agent_prompt_versions
                WHERE status IN ('proposed', 'shadow', 'ab', 'active')
                """
            )
        )
        items = rows.fetchall()
    return {str(r.sub_agent_name) for r in items if r.sub_agent_name is not None}


# ---------------------------------------------------------------------------
# Helpers — persistence (R-3.3)
# ---------------------------------------------------------------------------


# Main skills directory and the quarantined ".candidate" sibling.
# Importing ``skill_sync.SKILLS_DIR`` lazily (inside the helper below)
# avoids pulling the whole skill_sync module into process memory at
# import time; reflection workers usually don't need its other
# side-effects (yaml dump, etc.) but the candidate writer needs the
# root path.


_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.\-]")


def _safe_candidate_dirname(name: str) -> str:
    """Return a filesystem-safe directory name for a candidate.

    Strips directory separators / control chars so a malicious LLM
    can't escape ``.candidate/`` via ``../etc/passwd``-style names.
    """
    cleaned = _NAME_SAFE_RE.sub("_", name.strip())
    cleaned = cleaned.strip(".") or "candidate"
    return cleaned[:128]


def _default_skills_root_dir() -> Path:
    """Resolve the main ``data/skills/`` directory.

    Matches :data:`src.services.skill_sync.SKILLS_DIR` but imports
    lazily so tests can construct this module without the whole
    skill_sync chain.
    """
    try:
        from src.services.skill_sync import SKILLS_DIR  # type: ignore

        return Path(SKILLS_DIR)
    except Exception:
        # Fallback: server/data/skills relative to this file.
        return Path(__file__).resolve().parents[3] / "data" / "skills"


def _candidate_skill_md(root: Path, name: str, proposal: CandidateProposal) -> str:
    """Render the SKILL.md body for a skill candidate.

    Minimal valid DeepAgents skill frontmatter + the LLM-generated
    prompt. We deliberately don't import ``yaml`` so this module
    stays light; the frontmatter is hand-written and escapes only
    what a skill name / description can legally contain.
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


async def persist_candidate_proposal(
    proposal: CandidateProposal,
    *,
    db_factory: Any | None = None,
    skills_root_dir: Path | None = None,
    proposal_source: str = "reflection_worker",
) -> PersistedCandidate:
    """Write one :class:`CandidateProposal` to its target table + FS.

    This is now a thin wrapper over
    :class:`~src.services.evolution.candidate_store.SkillCandidateStore`
    (task 21.4). Public contract preserved so existing callers
    continue to work unchanged:

    * ``kind="skill"``         → INSERT ``skill_candidates`` + write
                                 ``<skills_root>/.candidate/<name>/SKILL.md``
                                 (R-3.3, never the main ``data/skills/``).
    * ``kind="prompt_patch"``  → INSERT ``sub_agent_prompt_versions``
                                 with ``status='proposed'``. No FS
                                 artefact, no ``skill_candidates`` row.
    * ``kind="tool_config"``   → INSERT ``skill_candidates`` with the
                                 patch + a pre-patch snapshot of the
                                 target tool's ``config`` (R-3.13).
                                 No FS artefact.

    Raises:
        ValueError: for unknown ``proposal.kind``.
        sqlalchemy exceptions: on constraint / connectivity problems.
    """
    # Lazy import: candidate_store imports pydantic dataclasses from
    # this module, so deferring the import here keeps the dependency
    # graph acyclic at module load time.
    from src.services.evolution.candidate_store import SkillCandidateStore

    store = SkillCandidateStore(
        db_factory=db_factory,
        skills_root_dir=skills_root_dir,
    )
    return await store.propose(proposal, proposal_source=proposal_source)


async def _persist_skill_candidate(
    proposal: CandidateProposal,
    *,
    factory: Any,
    skills_root: Path,
    proposal_source: str,
) -> PersistedCandidate:
    """Deprecated shim — delegates to
    :class:`~src.services.evolution.candidate_store.SkillCandidateStore`.

    Preserved so modules that imported this helper directly keep
    working during task 21.4's refactor. New code should construct a
    store instance and call ``store.propose(proposal)``.
    """
    from src.services.evolution.candidate_store import SkillCandidateStore

    store = SkillCandidateStore(
        db_factory=factory, skills_root_dir=skills_root
    )
    return await store._persist_skill(
        proposal, proposal_source=proposal_source
    )


async def _persist_tool_config_candidate(
    proposal: CandidateProposal,
    *,
    factory: Any,
    proposal_source: str,
) -> PersistedCandidate:
    """Deprecated shim — delegates to
    :class:`~src.services.evolution.candidate_store.SkillCandidateStore`.

    Keeps the pre-refactor signature so existing call sites don't
    need a touch. Pre-patch snapshot (R-3.13) is captured by the
    store, not here.
    """
    from src.services.evolution.candidate_store import SkillCandidateStore

    store = SkillCandidateStore(db_factory=factory)
    return await store._persist_tool_config(
        proposal, proposal_source=proposal_source
    )


async def _persist_prompt_patch_candidate(
    proposal: CandidateProposal,
    *,
    factory: Any,
    proposal_source: str,
) -> PersistedCandidate:
    """Deprecated shim — delegates to
    :class:`~src.services.evolution.candidate_store.SkillCandidateStore`.
    """
    from src.services.evolution.candidate_store import SkillCandidateStore

    store = SkillCandidateStore(db_factory=factory)
    return await store._persist_prompt_patch(
        proposal, proposal_source=proposal_source
    )


# ---------------------------------------------------------------------------
# Orchestration — cluster + generate in one call
# ---------------------------------------------------------------------------


@dataclass
class ReflectionCycleResult:
    """Combined return of :func:`run_reflection_full_cycle`.

    Holds both the clustering result and the candidate-generation
    result so the Celery wrapper can emit a single Celery result
    payload. When clustering produces no clusters the ``candidates``
    field is still populated (with zero proposals) — makes it easy
    for the Celery task to report stable shape.
    """

    reflection: ReflectionResult
    candidates: CandidateGenerationResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "reflection": self.reflection.to_dict(),
            "candidates": self.candidates.to_dict(),
        }


async def run_reflection_full_cycle(
    *,
    llm: Any | None = None,
    db_factory: Any | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    max_trajectories: int = DEFAULT_MAX_TRAJECTORIES,
    persist: bool = True,
    skills_root_dir: Path | None = None,
    proposal_source: str = "reflection_worker",
    now: datetime | None = None,
) -> ReflectionCycleResult:
    """Cluster failures, then generate candidates from them, in one call.

    This is the orchestration point that the Celery worker
    (:func:`src.workers.tasks.reflection.run_reflection_cycle`) calls
    once 21.2 is wired in. Separating this from :func:`cluster_failures`
    keeps each step independently testable (21.1 tests drive only
    clustering; 21.2 tests drive only candidate generation); this
    wrapper only glues them together.

    Behaviour:

    * If clustering returns anything other than ``status="ok"``
      (empty / skipped / error), we return an empty
      :class:`CandidateGenerationResult` without invoking the
      candidate LLM.
    * LLM and DB factory are shared between the two steps so a
      single injected double can satisfy both.
    * ``persist`` defaults to ``True`` because the Celery worker is
      always running against the real DB + filesystem; tests pass
      ``persist=False`` to exercise pure validation.
    """
    reflection = await cluster_failures(
        llm=llm,
        db_factory=db_factory,
        window_hours=window_hours,
        max_trajectories=max_trajectories,
        now=now,
    )
    if reflection.status != "ok" or not reflection.clusters:
        return ReflectionCycleResult(
            reflection=reflection,
            candidates=CandidateGenerationResult(
                n_clusters_input=len(reflection.clusters)
            ),
        )

    candidates = await generate_candidates(
        reflection.clusters,
        llm=llm,
        db_factory=db_factory,
        persist=persist,
        skills_root_dir=skills_root_dir,
        proposal_source=proposal_source,
    )
    return ReflectionCycleResult(
        reflection=reflection, candidates=candidates
    )


__all__ = [
    "CANDIDATE_GEN_PROMPT",
    "CLUSTER_FAILURES_PROMPT",
    "CandidateGenerationResult",
    "CandidateProposal",
    "DEFAULT_MAX_TRAJECTORIES",
    "DEFAULT_WINDOW_HOURS",
    "FailureCluster",
    "LIVE_CANDIDATE_STATUSES",
    "MIN_TRAJECTORIES_FOR_CLUSTERING",
    "PersistedCandidate",
    "REPEATED_FAILURE_PER_SESSION_CAP",
    "REPEATED_FAILURE_THRESHOLD",
    "ReflectionCycleResult",
    "ReflectionResult",
    "cluster_failures",
    "generate_candidates",
    "persist_candidate_proposal",
    "run_reflection_full_cycle",
]
