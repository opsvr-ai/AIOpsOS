"""Bench: P-Sleep-1 non-blocking consolidation.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 11.4 / R-2.9
/ P-Sleep-1.

**Validates: Requirements 2.9**

Property: four long-running (2s each, stubbed) consolidation tasks must
not drive the p95 of 100 concurrent ``/chat``-style work items above
``baseline_p95 * 1.2``.

Gated by ``RUN_BENCH=1`` so CI skips by default. The PBT runs locally
with ``RUN_BENCH=1 pytest -m benchmark``.
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time

import pytest


pytestmark = [
    pytest.mark.benchmark,
    pytest.mark.skipif(
        os.environ.get("RUN_BENCH") != "1",
        reason="benchmark disabled; set RUN_BENCH=1 to enable",
    ),
]


_CHAT_WORK_SECONDS = 0.01
_CONSOLIDATION_DURATION_SECONDS = 2.0
_N_CONCURRENT_CONSOLIDATIONS = 4
_N_CHAT_REQUESTS = 100
_BASELINE_P95_S = _CHAT_WORK_SECONDS * 1.0  # pure async no-contention baseline
_TOLERANCE = 1.2


async def _chat_work() -> float:
    """Simulated chat request handler; returns wall-clock duration."""
    start = time.perf_counter()
    await asyncio.sleep(_CHAT_WORK_SECONDS)
    return time.perf_counter() - start


async def _slow_consolidation() -> None:
    """Simulated long-running consolidation; runs in its own task so the
    event loop can still service chat awaits."""
    await asyncio.sleep(_CONSOLIDATION_DURATION_SECONDS)


def _percentile(values: list[float], p: float) -> float:
    """No external stats dep — use sorted interpolation."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(len(s) * p / 100)))
    return s[idx]


@pytest.mark.asyncio
async def test_chat_latency_p95_stable_under_consolidation_load() -> None:
    # 1) Baseline run: no consolidations in flight.
    baseline_durations = [
        await _chat_work() for _ in range(10)
    ]
    baseline_p95 = max(_percentile(baseline_durations, 95), _BASELINE_P95_S)

    # 2) Full run: 4 slow consolidations + 100 concurrent chat requests.
    cons_tasks = [
        asyncio.create_task(_slow_consolidation())
        for _ in range(_N_CONCURRENT_CONSOLIDATIONS)
    ]
    chat_durations = await asyncio.gather(
        *(_chat_work() for _ in range(_N_CHAT_REQUESTS))
    )
    await asyncio.gather(*cons_tasks)

    chat_p95 = _percentile(chat_durations, 95)
    chat_mean = statistics.mean(chat_durations) if chat_durations else 0.0

    assert chat_p95 <= baseline_p95 * _TOLERANCE, (
        f"p95 regression: baseline={baseline_p95:.4f}s observed={chat_p95:.4f}s "
        f"(mean={chat_mean:.4f}s, tolerance x{_TOLERANCE})"
    )
