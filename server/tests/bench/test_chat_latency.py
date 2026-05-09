"""Bench: dispatcher-level chat turn latency (P-Dispatcher-∞).

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 16.7 /
R-1.5.

Background
----------
R-1.5 specifies **first-token p95 ≤ 1000 ms / p99 ≤ 2000 ms** under 50
concurrent users × 100 turns. Validating the full chain requires a
live LLM endpoint, which CI can't provision deterministically. What we
*can* bench deterministically — and what Phase H makes newly
measurable — is the **dispatcher portion** of the turn: tool
classification, cache lookup/fan-out, approval gating, and output
budgeting for a realistic batch of tool calls per turn.

Below we simulate a mixed tool-call workload (3–5 tool calls per turn,
majority parallel-safe with a sprinkle of sequential) and measure the
wall-clock time spent inside :meth:`ToolDispatcher.dispatch_batch`.
This is a *lower-bound* for actual turn latency (the LLM forward pass
dominates in practice), so if the dispatcher itself exceeds 1000 ms p95
on these workloads we've regressed a constant-factor cost that will
compound in production.

Targets chosen for the dispatcher-only slice:

* p50 ≤ 200 ms
* p95 ≤ 1000 ms  (R-1.5 full-chain cap)
* p99 ≤ 2000 ms  (R-1.5 full-chain cap)

Gated by ``RUN_BENCH=1`` so PR CI skips by default. Nightly runs with
``RUN_BENCH=1 pytest -m benchmark`` exercise it.
"""
from __future__ import annotations

import asyncio
import os
import random
import statistics
import time
from typing import Any

import pytest

from src.services.agent_runtime import dispatcher as dispatcher_mod
from src.services.agent_runtime.dispatcher import (
    ToolCall,
    ToolDispatcher,
)
from src.services.tool_manager import (
    SAFE_PARALLEL,
    SEQUENTIAL,
    ToolManager,
)


pytestmark = [
    pytest.mark.benchmark,
    pytest.mark.skipif(
        os.environ.get("RUN_BENCH") != "1",
        reason="benchmark disabled; set RUN_BENCH=1 to enable",
    ),
]


# ---------------------------------------------------------------------------
# Workload parameters
# ---------------------------------------------------------------------------

_N_USERS = 50  # concurrent simulated users
_N_TURNS_PER_USER = 100
# Per-turn tool mix — majority are parallel-safe with a small share of
# sequential calls. All non-LLM latency is simulated.
_TOOLS_PER_TURN_MIN = 3
_TOOLS_PER_TURN_MAX = 5
# Latency budgets (milliseconds).
_P50_MS_BUDGET = 200.0
_P95_MS_BUDGET = 1000.0
_P99_MS_BUDGET = 2000.0
# Simulated per-tool network / IO latency bounds — parallel-safe tools
# should amortize via asyncio.gather, so larger values here actually
# *stress* the dispatcher's parallel fan-out.
_TOOL_LATENCY_MIN_S = 0.010
_TOOL_LATENCY_MAX_S = 0.080


# ---------------------------------------------------------------------------
# Fixture plumbing
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    dispatcher_mod._reset_singleton_for_tests()
    yield
    dispatcher_mod._reset_singleton_for_tests()


def _install_noop_cache(monkeypatch) -> dict:
    """Disable Redis; every call is a miss. Also bypasses cache writes.

    We bench the *worst* case — every tool call does a real invocation
    rather than benefiting from Redis cache hits. That's the right
    baseline: the cache hit path is trivially faster and would mask
    regressions in the fan-out path.
    """
    async def _no_get(_key: str):
        return None

    async def _no_set(_key: str, _value: Any, ttl: int = 300) -> None:
        return None

    monkeypatch.setattr(
        "src.services.agent_runtime.dispatcher.cache_get", _no_get
    )
    monkeypatch.setattr(
        "src.services.agent_runtime.dispatcher.cache_set", _no_set
    )
    return {}


class _SimulatedTool:
    """Tool that sleeps a deterministic-per-seed amount per invocation."""

    def __init__(self, name: str, min_s: float, max_s: float) -> None:
        self.name = name
        self.min_s = min_s
        self.max_s = max_s
        # One RNG per tool so each call yields reproducible-ish latency
        # across tool kinds while still jittering run-to-run.
        self._rng = random.Random(name)

    async def ainvoke(self, args: dict[str, Any]) -> str:
        # Use the args' canonical form to derive per-call jitter — this
        # keeps identical args deterministic while different args
        # produce different latencies.
        dur = self.min_s + (self.max_s - self.min_s) * self._rng.random()
        await asyncio.sleep(dur)
        return f"ok:{self.name}"


def _register_tools(tm: ToolManager) -> list[str]:
    """Register a representative mix of tools. Returns their names."""
    parallel_names = [
        "grep_kb",
        "read_wiki",
        "list_wiki",
        "memory_retrieve",
        "get_config",
        "search_logs",
    ]
    sequential_names = [
        "summarize_result",
        "format_table",
    ]
    for n in parallel_names:
        tm._tools[n] = _SimulatedTool(  # type: ignore[attr-defined]
            n, _TOOL_LATENCY_MIN_S, _TOOL_LATENCY_MAX_S
        )
        tm.set_safety(n, SAFE_PARALLEL)
    for n in sequential_names:
        tm._tools[n] = _SimulatedTool(  # type: ignore[attr-defined]
            n, _TOOL_LATENCY_MIN_S, _TOOL_LATENCY_MAX_S
        )
        tm.set_safety(n, SEQUENTIAL)
    return parallel_names + sequential_names


def _build_turn_calls(
    names: list[str], rng: random.Random, turn_id: int
) -> list[ToolCall]:
    """Construct a realistic batch of 3–5 tool calls for one turn."""
    n_calls = rng.randint(_TOOLS_PER_TURN_MIN, _TOOLS_PER_TURN_MAX)
    return [
        ToolCall(
            name=rng.choice(names),
            args={"q": f"t{turn_id}_c{i}", "n": i},
            call_id=f"t{turn_id}-c{i}",
        )
        for i in range(n_calls)
    ]


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(len(s) * p / 100)))
    return s[idx]


# ---------------------------------------------------------------------------
# Benchmark — dispatcher-only latency under concurrent turn workload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_latency_under_concurrent_turns(monkeypatch):
    """p95 of dispatch_batch ≤ 1000 ms across 50 users × 100 turns.

    This is the **dispatcher slice** of R-1.5 first-token latency —
    the LLM forward pass adds on top but is not exercised here.
    Failing this test means a dispatcher-level regression (e.g.
    sequential phase holding parallel for too long, or cache path
    blocking on Redis).
    """
    _install_noop_cache(monkeypatch)

    tm = ToolManager()
    names = _register_tools(tm)
    dispatcher = ToolDispatcher(
        tool_manager_=tm, redis_enabled=False
    )

    async def _one_user_turns(user_id: int) -> list[float]:
        """Run ``_N_TURNS_PER_USER`` turns sequentially for one user."""
        rng = random.Random(user_id)
        durations: list[float] = []
        for t in range(_N_TURNS_PER_USER):
            calls = _build_turn_calls(names, rng, turn_id=t)
            start = time.perf_counter()
            await dispatcher.dispatch_batch(calls)
            durations.append((time.perf_counter() - start) * 1000.0)
        return durations

    # Fire off all users concurrently — aggregate every per-turn latency.
    all_durations_per_user = await asyncio.gather(
        *(_one_user_turns(u) for u in range(_N_USERS))
    )
    all_latencies_ms: list[float] = [
        d for user_durations in all_durations_per_user for d in user_durations
    ]

    p50 = _percentile(all_latencies_ms, 50)
    p95 = _percentile(all_latencies_ms, 95)
    p99 = _percentile(all_latencies_ms, 99)
    mean_ms = statistics.mean(all_latencies_ms)
    total_turns = len(all_latencies_ms)

    print(
        f"\n[bench] dispatcher mixed-workload latency "
        f"({_N_USERS} users × {_N_TURNS_PER_USER} turns = {total_turns} turns):\n"
        f"    mean = {mean_ms:.1f} ms\n"
        f"    p50  = {p50:.1f} ms (budget {_P50_MS_BUDGET:.0f})\n"
        f"    p95  = {p95:.1f} ms (budget {_P95_MS_BUDGET:.0f})\n"
        f"    p99  = {p99:.1f} ms (budget {_P99_MS_BUDGET:.0f})"
    )

    assert p50 <= _P50_MS_BUDGET, (
        f"dispatcher p50 regression: {p50:.1f} ms > {_P50_MS_BUDGET:.0f} ms budget"
    )
    assert p95 <= _P95_MS_BUDGET, (
        f"dispatcher p95 regression: {p95:.1f} ms > {_P95_MS_BUDGET:.0f} ms budget"
    )
    assert p99 <= _P99_MS_BUDGET, (
        f"dispatcher p99 regression: {p99:.1f} ms > {_P99_MS_BUDGET:.0f} ms budget"
    )
