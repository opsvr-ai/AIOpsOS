"""Default feature-flag rows — seeded on app boot.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 5.3 / R-7.4.

Each default flag exists in three places:

1. :data:`DEFAULT_FLAGS` — this file, the source of truth for defaults.
2. DB row in ``runtime_feature_flags`` — seeded once by
   :func:`seed_default_flags` on app startup; operators can then tune
   them via the admin UI without risking being overwritten on next boot.
3. Call sites in code that read the flag via
   :func:`src.services.feature_flags.get_feature_flags`.

``seed_default_flags(overwrite=True)`` is available for migrations /
tests that need to reset to the canonical defaults.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.models.base import async_session_factory
from src.models.runtime_flag import RuntimeFeatureFlag

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlagSpec:
    """Canonical default for one flag."""

    key: str
    enabled: bool = False
    rollout_percent: int = 0
    data: dict = field(default_factory=dict)


_D = lambda desc: {"description": desc}  # noqa: E731 - short-form builder for seed


DEFAULT_FLAGS: dict[str, FlagSpec] = {
    # --- Phase M (task 24.1): default-on rollout --------------------------
    # router_llm_enabled ships at 100% — the canary period is complete.
    # RouterLLM pre-classifies requests and narrows the tool set, reducing
    # prompt size and LLM decision latency significantly.
    "router_llm_enabled": FlagSpec(
        "router_llm_enabled",
        enabled=True,
        rollout_percent=100,
        data=_D("Enable RouterLLM pre-classification for /chat (Phase G)."),
    ),
    # Phase D embedding recall — default-on at 100% per task 24.1. The
    # warm_recall path has a graceful-degradation branch if pgvector
    # isn't available, so flipping this on doesn't break dev/test.
    "memory_embeddings_enabled": FlagSpec(
        "memory_embeddings_enabled",
        enabled=True,
        rollout_percent=100,
        data=_D("Enable pgvector-backed warm_recall (Phase D)."),
    ),
    # Phase E consolidation worker — default-on at 100% per task 24.1.
    # Task 25.2 removed the legacy in-request extraction path, so this
    # flag is now the only gate for the consolidation path — ``is_enabled``
    # false falls back to no-op (safe). See ``memory_provider.sync_turn``.
    "consolidation_worker_enabled": FlagSpec(
        "consolidation_worker_enabled",
        enabled=True,
        rollout_percent=100,
        data=_D("Enable Celery consolidation worker for async memory extraction (Phase E)."),
    ),
    # Phase F wiki compiler — default-on at 100% per task 24.1.
    "wiki_compile_worker_enabled": FlagSpec(
        "wiki_compile_worker_enabled",
        enabled=True,
        rollout_percent=100,
        data=_D("Use Celery wiki compiler instead of in-process kb_monitor (Phase F)."),
    ),
    "evolution_reflector_enabled": FlagSpec(
        "evolution_reflector_enabled",
        data=_D("Allow Reflector to create new candidates (Phase J)."),
    ),
    "evolution_shadow_enabled": FlagSpec(
        "evolution_shadow_enabled",
        data=_D("Run active candidates in shadow alongside production (Phase K)."),
    ),
    "evolution_ab_enabled": FlagSpec(
        "evolution_ab_enabled",
        data=_D("Allow Promoter to promote shadow→A/B→active (Phase K)."),
    ),
    # --- Phase G: RuntimeGateway front door ------------------------------
    # Default ON per task 14.2 — the gateway is now the canonical front
    # door for /chat(/stream). When the flag is flipped off (or the
    # service errors out) ``RuntimeGateway.handle`` returns
    # ``route="full_agent"`` with the legacy ``get_deep_agent()`` graph,
    # so the legacy path is retained for the one-release grace window
    # the task calls for without any conditional branches in the
    # handler. Operators can revert via the admin API if needed.
    "gateway_enabled": FlagSpec(
        "gateway_enabled",
        enabled=True,
        rollout_percent=100,
        data=_D("Route /chat through RuntimeGateway (Phase G)."),
    ),
    # --- Phase E: memory write-path flip ---------------------------------
    # Task 25.2 removed the ``memory_legacy_sync`` escape hatch entirely.
    # ``DatabaseMemoryProvider.sync_turn`` now unconditionally emits a
    # hint to the ConsolidationWorker (R-2.1 / R-9.3 / Phase M DoD) — no
    # in-request LLM extraction, no local turn buffer. The flag row is
    # dropped by migration ``202605041840_drop_memory_legacy_sync_flag``.
    # --- Phase H: ToolDispatcher wrapper --------------------------------
    # ON by default — routes executor tool calls through ToolDispatcher
    # for safety partitioning, result cache, and destructive-approval
    # enforcement. The result cache significantly reduces latency for
    # repeated tool calls (e.g., reading the same file multiple times).
    "tool_dispatcher_enabled": FlagSpec(
        "tool_dispatcher_enabled",
        enabled=True,
        rollout_percent=100,
        data=_D(
            "Route executor-agent tool calls through ToolDispatcher (safety "
            "partitioning + result cache + approval gate)."
        ),
    ),
    # --- Phase C: trajectory sink ----------------------------------------
    # Default ON — Phase C wires the sink behind this flag so ops can kill
    # it instantly without touching code if Kafka misbehaves.
    "trajectory_enabled": FlagSpec(
        "trajectory_enabled",
        enabled=True,
        rollout_percent=100,
        data=_D("Emit TrajectoryEvent for every chat turn (Phase C)."),
    ),
}


async def seed_default_flags(
    *,
    overwrite: bool = False,
    session_factory: Any | None = None,
) -> dict[str, str]:
    """Insert any missing default flag rows.

    Returns a small action-summary dict mapping ``key -> {"inserted", "skipped",
    "updated"}`` for operator observability.

    * ``overwrite=False`` (default): leave existing rows alone so hand-tuned
      rollout percentages survive a redeploy.
    * ``overwrite=True``: upsert every row to the canonical default —
      useful for tests or when an operator explicitly asks for a reset.
    """
    factory = session_factory or async_session_factory
    summary: dict[str, str] = {}

    async with factory() as session:
        for spec in DEFAULT_FLAGS.values():
            values = {
                "key": spec.key,
                "enabled": spec.enabled,
                "rollout_percent": spec.rollout_percent,
                "data": dict(spec.data),
            }
            stmt = pg_insert(RuntimeFeatureFlag).values(**values)
            if overwrite:
                stmt = stmt.on_conflict_do_update(
                    index_elements=[RuntimeFeatureFlag.key],
                    set_={
                        "enabled": spec.enabled,
                        "rollout_percent": spec.rollout_percent,
                        "data": dict(spec.data),
                    },
                )
                summary[spec.key] = "upserted"
            else:
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=[RuntimeFeatureFlag.key]
                )
                summary[spec.key] = "insert_or_skip"
            await session.execute(stmt)
        await session.commit()

    logger.info("feature_flags: seeded %d default flags (overwrite=%s)",
                len(DEFAULT_FLAGS), overwrite)
    return summary


__all__ = ["DEFAULT_FLAGS", "FlagSpec", "seed_default_flags"]
