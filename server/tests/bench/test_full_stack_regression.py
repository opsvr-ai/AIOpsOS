"""Full-stack regression benchmark for Phase M DoD.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 24.3 /
R-1.5 / Phase M DoD.

**Validates: Requirements 1.5**

What this file actually asserts
-------------------------------

R-1.5 is the ceiling on end-to-end ``/chat(/stream)`` first-token
latency under a realistic mixed workload: **p50 ≤ 500 ms, p95 ≤
1000 ms, p99 ≤ 2000 ms** at 50 concurrent users × 100 turns. Phase M
DoD adds two memory-system health signals that the same bench must
sample at the end of the run:

* ``memory_recall_hit_ratio{tier="hot"} ≥ 0.6``
* ``embedding_cache_hit_ratio ≥ 0.6``

Exercising all three together requires a live Postgres / Redis / Kafka
stack plus a reachable LLM provider, which CI cannot guarantee. The
bench is therefore split into two tests:

* :func:`test_full_stack_regression` — drives the real FastAPI app at
  ``src.main.app`` via :class:`httpx.ASGITransport`, gated by
  ``RUN_BENCH=1`` so PR CI skips by default. Expected to run nightly
  against the dev stack.
* :func:`test_full_stack_smoke` — validates the bench harness itself
  without the live stack. It seeds the two hit-ratio gauges with
  deterministic values, runs a stubbed request handler through the
  same concurrency / percentile code path as the live test, and
  asserts the harness produces a numeric ``p50`` and reads the two
  gauges back correctly. This is the safety net that proves the
  instrumentation wiring still works even when a nightly broker is
  down.

Workload mix
------------

Per the task spec the live workload is 100 turns × 50 concurrent users
broken down by message shape:

* 50% greeting   — ``RouterLLM`` should return ``direct``; worst-case
  for latency since nothing else masks overhead.
* 30% ops query  — ``RouterLLM`` should return ``executor`` with a
  narrow tool subset; exercises warm_recall + dispatcher.
* 20% tool-triggered — messages that explicitly ask the agent to run
  one of the parallel-safe builtin tools; exercises the
  ``ToolDispatcher`` + approval path in the common case (all
  parallel-safe).

The three buckets are sampled via a seeded RNG so the workload is
deterministic per-user and reproducible run-to-run.
"""
from __future__ import annotations

import asyncio
import os
import random
import statistics
import time
from typing import Any, Awaitable, Callable

import pytest


# ---------------------------------------------------------------------------
# Shared workload / percentile plumbing
# ---------------------------------------------------------------------------

_N_USERS = 50
_N_TURNS_PER_USER = 100

# Budgets (milliseconds) — these are the R-1.5 end-to-end targets.
_P50_MS_BUDGET = 500.0
_P95_MS_BUDGET = 1000.0
_P99_MS_BUDGET = 2000.0

# Phase M DoD memory health thresholds.
_MEMORY_HOT_HIT_RATIO_FLOOR = 0.6
_EMBEDDING_CACHE_HIT_RATIO_FLOOR = 0.6


# The three buckets spec'd for the full-stack workload. ``weight`` sums
# to 1.0 — we sample a uniform random in [0,1) and pick the bucket
# whose cumulative weight just exceeds it.
_GREETING_PROMPTS = (
    "你好",
    "hi",
    "在吗",
    "hello there",
    "嗨",
)
_OPS_QUERY_PROMPTS = (
    "帮我查一下订单服务最近 10 分钟的报错率",
    "payment 服务的 p99 延迟现在是多少",
    "列一下还没闭环的 P1 告警",
    "最近一次数据库主备切换的时间",
    "库存服务的 CPU 使用率趋势",
)
_TOOL_TRIGGERED_PROMPTS = (
    "搜索 wiki 里关于 redis 主从切换的预案",
    "读取 /etc/nginx/conf.d/api.conf 当前内容",
    "查一下最近 1 小时 order-service 的 error 日志",
    "列出 k8s default 命名空间所有 pending 的 pod",
    "从 CMDB 里找一下 host-1234 的负责人",
)

_BUCKETS = (
    ("greeting", 0.50, _GREETING_PROMPTS),
    ("ops_query", 0.30, _OPS_QUERY_PROMPTS),
    ("tool_triggered", 0.20, _TOOL_TRIGGERED_PROMPTS),
)


def _sample_prompt(rng: random.Random) -> tuple[str, str]:
    """Return ``(bucket_name, prompt)`` drawn from :data:`_BUCKETS`."""
    roll = rng.random()
    cum = 0.0
    for name, weight, prompts in _BUCKETS:
        cum += weight
        if roll < cum:
            return name, rng.choice(prompts)
    # Defensive — floating-point drift; fall back to the last bucket.
    name, _, prompts = _BUCKETS[-1]
    return name, rng.choice(prompts)


def _percentile(values: list[float], p: float) -> float:
    """Plain-sorted percentile (no numpy dep) in same units as ``values``."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(len(s) * p / 100)))
    return s[idx]


async def _run_workload(
    request_fn: Callable[[int, int, str, str], Awaitable[float]],
    *,
    n_users: int = _N_USERS,
    n_turns: int = _N_TURNS_PER_USER,
) -> list[float]:
    """Drive ``request_fn`` across ``n_users`` × ``n_turns`` concurrent turns.

    ``request_fn(user_id, turn_id, bucket, prompt) -> duration_ms`` is
    responsible for whatever mechanism actually produces the latency —
    real HTTP call in the live test, stubbed asyncio.sleep in the
    smoke test. Returns the flat list of per-turn durations in ms.
    """

    async def _one_user(user_id: int) -> list[float]:
        # Seeded RNG so a given user's traffic shape is reproducible.
        rng = random.Random(user_id * 9973 + 7)
        out: list[float] = []
        for t in range(n_turns):
            bucket, prompt = _sample_prompt(rng)
            dur = await request_fn(user_id, t, bucket, prompt)
            out.append(dur)
        return out

    per_user = await asyncio.gather(*(_one_user(u) for u in range(n_users)))
    return [d for user in per_user for d in user]


def _format_percentile_report(
    label: str, durations_ms: list[float]
) -> tuple[float, float, float, str]:
    """Return ``(p50, p95, p99, human_readable_report)``."""
    p50 = _percentile(durations_ms, 50)
    p95 = _percentile(durations_ms, 95)
    p99 = _percentile(durations_ms, 99)
    mean = statistics.mean(durations_ms) if durations_ms else 0.0
    report = (
        f"\n[bench:{label}] {len(durations_ms)} turns "
        f"({_N_USERS} users × {_N_TURNS_PER_USER})\n"
        f"    mean = {mean:.1f} ms\n"
        f"    p50  = {p50:.1f} ms (budget {_P50_MS_BUDGET:.0f})\n"
        f"    p95  = {p95:.1f} ms (budget {_P95_MS_BUDGET:.0f})\n"
        f"    p99  = {p99:.1f} ms (budget {_P99_MS_BUDGET:.0f})"
    )
    return p50, p95, p99, report


def _read_hit_ratios() -> tuple[float, float]:
    """Sample the two Phase M DoD gauges. Returns ``(memory_hot, embedding)``.

    Exposed via the Prometheus client's ``_value`` protected accessor
    — the only supported way to read a gauge's current value in-process
    without scraping ``/metrics``. Missing label combinations (hot tier
    never touched) yield 0.0, which the live test treats as a failure
    signal (the memory subsystem should have served at least one hot
    turn during the workload).
    """
    from src.core.metrics import (
        embedding_cache_hit_ratio,
        memory_recall_hit_ratio,
    )

    try:
        hot = float(
            memory_recall_hit_ratio.labels(tier="hot")._value.get()
        )
    except Exception:
        hot = 0.0
    try:
        emb = float(embedding_cache_hit_ratio._value.get())
    except Exception:
        emb = 0.0
    return hot, emb


# ---------------------------------------------------------------------------
# Live-stack bench — gated by RUN_BENCH=1
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
@pytest.mark.skipif(
    not os.environ.get("RUN_BENCH"),
    reason=(
        "full-stack bench requires a live Postgres + Redis + Kafka + LLM "
        "stack; set RUN_BENCH=1 to enable"
    ),
)
@pytest.mark.asyncio
async def test_full_stack_regression() -> None:
    """End-to-end ``/chat`` regression vs R-1.5 + Phase M DoD targets.

    Drives ``src.main.app`` over :class:`httpx.ASGITransport` so the
    request lifecycle exactly matches what uvicorn would execute in
    production, including the gateway, router, executor pool,
    dispatcher, memory tier, and trajectory sink.

    The test registers a fresh user per-fixture-run and funnels every
    turn through the same authenticated session so warm_recall has a
    consistent hot memory block to hit. Budgets from R-1.5 and the
    Phase M DoD are asserted at the end; a single failure points
    directly at whichever target slipped.
    """
    # Point Kafka + tracing at test-safe defaults *before* importing the
    # app. Same pattern as tests/agent_runtime/test_gateway_e2e.py.
    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault(
        "KAFKA_BOOTSTRAP_SERVERS",
        os.environ.get("TEST_KAFKA_BOOTSTRAP_SERVERS", "localhost:9094"),
    )

    from httpx import ASGITransport, AsyncClient

    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://bench", timeout=30.0
    ) as client:
        # Register + login a single bench user. Every virtual user
        # shares the same session id so the memory subsystem can build
        # up a realistic hot block during the run.
        username = f"bench-{int(time.time())}-{os.getpid()}"
        await client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "email": f"{username}@bench.local",
                "password": "benchpass123",
            },
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "benchpass123"},
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        async def _request(
            user_id: int, turn_id: int, _bucket: str, prompt: str
        ) -> float:
            payload: dict[str, Any] = {
                "message": prompt,
                "session_id": f"bench-u{user_id}",
            }
            start = time.perf_counter()
            # Use the non-streaming /chat — first-token latency is a
            # subset of the full response time, so passing against
            # /chat budgets also passes against /chat/stream.
            resp = await client.post(
                "/chat", json=payload, headers=headers
            )
            duration_ms = (time.perf_counter() - start) * 1000.0
            # Don't assert on status inside the hot loop — a 5xx
            # should surface as an outlier in the percentile, which
            # the budget check below will catch. But we do want to
            # count it, so stuff the full elapsed time in either way.
            if resp.status_code >= 500:
                # Ensure failures count against the budget.
                return max(duration_ms, _P99_MS_BUDGET + 1.0)
            return duration_ms

        durations = await _run_workload(_request)

    p50, p95, p99, report = _format_percentile_report("full_stack", durations)
    hot_hit, emb_hit = _read_hit_ratios()
    print(
        report
        + f"\n    memory_recall_hit_ratio[hot] = {hot_hit:.3f} "
        f"(floor {_MEMORY_HOT_HIT_RATIO_FLOOR})\n"
        f"    embedding_cache_hit_ratio    = {emb_hit:.3f} "
        f"(floor {_EMBEDDING_CACHE_HIT_RATIO_FLOOR})"
    )

    # ---- R-1.5 latency budgets ---------------------------------------
    assert p50 <= _P50_MS_BUDGET, (
        f"R-1.5 p50 regression: {p50:.1f} ms > {_P50_MS_BUDGET:.0f} ms"
    )
    assert p95 <= _P95_MS_BUDGET, (
        f"R-1.5 p95 regression: {p95:.1f} ms > {_P95_MS_BUDGET:.0f} ms"
    )
    assert p99 <= _P99_MS_BUDGET, (
        f"R-1.5 p99 regression: {p99:.1f} ms > {_P99_MS_BUDGET:.0f} ms"
    )

    # ---- Phase M DoD hit ratios --------------------------------------
    assert hot_hit >= _MEMORY_HOT_HIT_RATIO_FLOOR, (
        f"memory_recall_hit_ratio[hot] {hot_hit:.3f} < "
        f"{_MEMORY_HOT_HIT_RATIO_FLOOR}"
    )
    assert emb_hit >= _EMBEDDING_CACHE_HIT_RATIO_FLOOR, (
        f"embedding_cache_hit_ratio {emb_hit:.3f} < "
        f"{_EMBEDDING_CACHE_HIT_RATIO_FLOOR}"
    )


# ---------------------------------------------------------------------------
# Smoke — always runs; validates the bench harness works end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_full_stack_smoke() -> None:
    """Harness validation: run the bench pipeline without the live stack.

    Why this exists
    ~~~~~~~~~~~~~~~

    The live bench only runs nightly with ``RUN_BENCH=1``. If the
    harness itself regresses (e.g. the workload mix loses a bucket,
    the percentile helper breaks, or the metric accessors drift), PR
    CI wouldn't notice until the nightly run flakes. This test runs
    in ordinary CI to keep the harness honest:

    * It drives :func:`_run_workload` against a stubbed async request
      callable that sleeps ~10ms, so the whole pipeline runs in
      well under a second.
    * It seeds the two Phase M DoD gauges with known values and reads
      them back through :func:`_read_hit_ratios`, which must return
      those same values bit-identical.
    * It asserts percentile math produces a numeric ``p50`` and that
      every bucket in the workload mix gets sampled at least once
      (otherwise the workload specification has drifted away from
      the R-1.5 mix the live test is supposed to be driving).

    It deliberately does NOT boot the real FastAPI app — the smoke
    test must pass even when Postgres / Redis / Kafka are unreachable.
    """
    from src.core.metrics import (
        embedding_cache_hit_ratio,
        memory_recall_hit_ratio,
    )

    # Seed deterministic gauge values we can read back. Use clearly
    # distinct numbers so an accidental label swap would show up.
    memory_recall_hit_ratio.labels(tier="hot").set(0.731)
    embedding_cache_hit_ratio.set(0.624)

    # Track which buckets the stub saw — guard against the workload
    # mix drifting to drop a bucket in a future refactor.
    seen_buckets: dict[str, int] = {"greeting": 0, "ops_query": 0, "tool_triggered": 0}
    seen_lock = asyncio.Lock()

    async def _stub_request(
        _user_id: int, _turn_id: int, bucket: str, _prompt: str
    ) -> float:
        start = time.perf_counter()
        # Cheap wait — the scheduler itself will jitter the real
        # wall-clock time so percentile math has something to sort.
        await asyncio.sleep(0.005)
        async with seen_lock:
            seen_buckets[bucket] = seen_buckets.get(bucket, 0) + 1
        return (time.perf_counter() - start) * 1000.0

    # Scaled-down so the smoke test stays sub-second in CI while still
    # exercising the full pipeline (percentile, concurrency, bucket
    # sampling, metric readback).
    n_users, n_turns = 8, 20
    durations = await _run_workload(
        _stub_request, n_users=n_users, n_turns=n_turns
    )

    # ---- Harness math: percentiles must be numeric + ordered --------
    assert len(durations) == n_users * n_turns
    p50 = _percentile(durations, 50)
    p95 = _percentile(durations, 95)
    p99 = _percentile(durations, 99)
    assert isinstance(p50, float) and p50 >= 0.0
    assert p95 >= p50
    assert p99 >= p95

    # ---- Workload mix: every bucket was exercised at least once -----
    for bucket, count in seen_buckets.items():
        assert count > 0, (
            f"workload mix regression: bucket {bucket!r} was never sampled "
            f"(seen={seen_buckets})"
        )

    # ---- Metric readback matches what we set (instrumentation wire) -
    hot_hit, emb_hit = _read_hit_ratios()
    assert hot_hit == pytest.approx(0.731, abs=1e-6)
    assert emb_hit == pytest.approx(0.624, abs=1e-6)

    # ---- Report helper: exercises the same code path the live test uses
    _, _, _, report = _format_percentile_report("smoke", durations)
    assert "smoke" in report and "p50" in report
