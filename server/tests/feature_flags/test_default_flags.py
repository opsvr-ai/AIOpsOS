"""Canonical-default assertions for :mod:`feature_flags_bootstrap`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 14.2 /
R-1.1 (the gateway is the new front door for /chat, default ON).

These are pure-Python assertions over :data:`DEFAULT_FLAGS` — no DB, no
Redis, no LLM. The point is to lock down the *defaults* contract so an
accidental flip away from the Phase-G rollout state shows up as a red
test on CI rather than as a silent production regression.

If a default legitimately changes, update both the assertion and the
release notes so operators see the behaviour change.
"""
from __future__ import annotations

from src.services.feature_flags_bootstrap import DEFAULT_FLAGS


def test_gateway_enabled_defaults_on() -> None:
    """Task 14.2: ``gateway_enabled`` ships default-on at 100% rollout.

    The gateway internally retains the legacy path via its
    ``full_agent`` fallback branch, so flipping this on does not drop
    the one-release grace window — it just makes the gateway the
    canonical dispatcher.
    """
    spec = DEFAULT_FLAGS["gateway_enabled"]
    assert spec.enabled is True, (
        "gateway_enabled must default enabled=True per task 14.2 — "
        "the legacy path stays reachable via RuntimeGateway's "
        "full_agent fallback, not via this flag."
    )
    assert spec.rollout_percent == 100, (
        "gateway_enabled must default to 100% rollout so the flag "
        "isn't accidentally gated on user_id bucketing."
    )


def test_trajectory_enabled_defaults_on() -> None:
    """Sanity-check that the already-default-on trajectory flag
    hasn't been accidentally flipped off by the same edit that enables
    the gateway. The two defaults share a file so they can move
    together if someone refactors; this guard keeps them honest."""
    spec = DEFAULT_FLAGS["trajectory_enabled"]
    assert spec.enabled is True
    assert spec.rollout_percent == 100


def test_router_llm_defaults_to_phase_m_canary() -> None:
    """Task 24.1 flips ``router_llm_enabled`` to a 10% canary rollout.

    Earlier Phase-G work left the flag OFF so the gateway could ship
    without a router dependency; task 24.1 now turns it on for a
    bucketed canary slice. Task 24.2 promotes it to 100% after the
    latency soak window. This guard keeps task 14.2 (gateway default-on)
    and task 24.x (router staging) from colliding via the shared
    defaults dict.

    Detailed Phase-M assertions live in ``test_defaults_flipped.py``;
    this is the quick-and-obvious sanity check alongside the other
    top-level default gates.
    """
    spec = DEFAULT_FLAGS["router_llm_enabled"]
    assert spec.enabled is True
    assert spec.rollout_percent == 10
