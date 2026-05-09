"""Phase M default-flip assertions for :mod:`feature_flags_bootstrap`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 24.1 /
Phase M DoD.

Task 24.1 flips the core rollout flags default-on:

* ``memory_embeddings_enabled``       — 100% (Phase D warm_recall)
* ``consolidation_worker_enabled``    — 100% (Phase E worker path)
* ``wiki_compile_worker_enabled``     — 100% (Phase F compiler worker)
* ``gateway_enabled``                 — 100% (already ON since task 14.2)
* ``router_llm_enabled``              —  10% (canary — task 24.2 bumps to 100)

Task 25.2 subsequently removed the ``memory_legacy_sync`` escape-hatch
flag entirely, so it no longer appears in :data:`DEFAULT_FLAGS`.

These assertions lock the Phase-M default contract at the dict-literal
level: no DB, no Redis, no LLM. If an operator legitimately needs to
re-stage the rollout they do it via the admin API; any code-level
regression away from the Phase-M defaults shows up as a red test here
rather than as a silent regression in prod.
"""
from __future__ import annotations

from src.services.feature_flags_bootstrap import DEFAULT_FLAGS


def test_memory_embeddings_enabled_defaults_on_100pct() -> None:
    """Task 24.1: ``memory_embeddings_enabled`` ships default-on at 100%."""
    spec = DEFAULT_FLAGS["memory_embeddings_enabled"]
    assert spec.enabled is True
    assert spec.rollout_percent == 100


def test_consolidation_worker_enabled_defaults_on_100pct() -> None:
    """Task 24.1: ``consolidation_worker_enabled`` ships default-on at 100%."""
    spec = DEFAULT_FLAGS["consolidation_worker_enabled"]
    assert spec.enabled is True
    assert spec.rollout_percent == 100


def test_wiki_compile_worker_enabled_defaults_on_100pct() -> None:
    """Task 24.1: ``wiki_compile_worker_enabled`` ships default-on at 100%."""
    spec = DEFAULT_FLAGS["wiki_compile_worker_enabled"]
    assert spec.enabled is True
    assert spec.rollout_percent == 100


def test_gateway_enabled_defaults_on_100pct() -> None:
    """Task 24.1 keeps ``gateway_enabled`` default-on at 100%.

    It was already flipped by task 14.2; this guard makes sure the
    Phase-M rollout doesn't accidentally re-stage it to a lower bucket.
    """
    spec = DEFAULT_FLAGS["gateway_enabled"]
    assert spec.enabled is True
    assert spec.rollout_percent == 100


def test_router_llm_enabled_defaults_on_10pct_canary() -> None:
    """Task 24.1 flips ``router_llm_enabled`` to a 10% canary rollout.

    Task 24.2 bumps this to 100% after 7d of no latency regression in
    ``tests/bench/test_chat_latency.py``. Until then, the flag is
    enabled but only a 10% bucketed slice of users hits the RouterLLM
    classifier — the rest fall through ``RuntimeGateway`` to the
    ``full_agent`` legacy graph.
    """
    spec = DEFAULT_FLAGS["router_llm_enabled"]
    assert spec.enabled is True, (
        "router_llm_enabled must default enabled=True per task 24.1 — "
        "the 10% canary bucket is enforced by rollout_percent, not by "
        "leaving enabled=False."
    )
    assert spec.rollout_percent == 10, (
        "router_llm_enabled must default rollout_percent=10 for the "
        "Phase-M canary window. Task 24.2 is what bumps this to 100."
    )


def test_memory_legacy_sync_flag_is_removed() -> None:
    """Task 25.2 removed ``memory_legacy_sync`` from the defaults.

    ``DatabaseMemoryProvider.sync_turn`` now unconditionally emits to
    the ConsolidationWorker — there is no legacy path to gate behind
    an escape-hatch flag. The DB row is dropped by migration
    ``202605041840_drop_memory_legacy_sync_flag``.
    """
    assert "memory_legacy_sync" not in DEFAULT_FLAGS, (
        "memory_legacy_sync was removed by task 25.2 and should not "
        "re-appear in DEFAULT_FLAGS."
    )
