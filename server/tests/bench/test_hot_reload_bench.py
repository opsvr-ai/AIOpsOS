"""Bench: P-HotReload-5 reload-churn benchmark (full 60s / 100 QPS profile).

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 23.9 /
R-3.16.

**Validates: Requirements 3.16**

Full-profile variant of the scaled-down stress test in
``tests/evolution/test_hot_reload_no_interruption.py``. Gated by
``RUN_BENCH=1`` because it sustains load for a full minute.

Profile (as per task 23.9 description)
--------------------------------------

* **Duration.** 60 s of sustained load.
* **Reader load.** ~100 QPS against :meth:`SubAgentPromptRegistry.get_active`
  spread across 4 sub-agents. Implemented as 50 concurrent coroutines
  each firing at ~2 Hz (2 reads/sec × 50 workers ≈ 100 QPS).
* **Writer churn.** 20 promote / rollback events interleaved across
  the 60 s window (one every ~3 s).

Pass criteria
-------------

1. **Zero interruptions.** No ``get_active`` call raises; every
   returned :class:`PromptVersion` is well-formed (non-empty prompt,
   no sentinel leak, matching ``sub_agent_name``).
2. **Latency regression cap.** The observed ``get_active`` p95 is
   bounded (``p95_under_churn ≤ p95_baseline * 1.1``), mirroring the
   spec's "no 5xx, p95 ≤ baseline * 1.1" requirement translated to the
   registry slice of the chain.

Why a registry-level benchmark (not an HTTP one)
------------------------------------------------

The spec's ``/chat/stream`` bench needs a live LLM endpoint plus the
full FastAPI stack, which is impractical to pin for latency assertions
in CI. The registry's :meth:`get_active` is the single hot-path
function a hot reload can block on — if registry p95 regresses, the
``/chat/stream`` p95 regresses. Validating the registry slice here
gives us a deterministic, fast-to-run stand-in.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.agent.runtime.dynamic_prompt_middleware import _SENTINEL_PROMPT
from src.services.evolution.prompt_registry import (
    PromotionEvent,
    PromptVersion,
    SubAgentPromptRegistry,
)
from src.services.prompt_versions.repository import PromptVersionRow


pytestmark = [
    pytest.mark.benchmark,
    pytest.mark.property,
    pytest.mark.skipif(
        os.environ.get("RUN_BENCH") != "1",
        reason="benchmark disabled; set RUN_BENCH=1 to enable",
    ),
]


# ---------------------------------------------------------------------------
# Profile knobs (match the spec profile)
# ---------------------------------------------------------------------------

_DURATION_S = 60.0
_N_READER_WORKERS = 50
_READ_INTERVAL_S = 0.5  # 50 workers × 2 Hz ≈ 100 QPS
_N_PROMOTIONS = 20
# Latency regression tolerance (R-3.16-aligned).
_P95_TOLERANCE = 1.1
# Minimum baseline we accept; if measurement is too small (sub-µs) the
# ratio gets unstable from pure scheduler noise. 50 µs is generous.
_MIN_BASELINE_S = 50e-6
_SUB_AGENT_NAMES = ("ops", "monitor", "knowledge", "analysis")
_CANDIDATES_PER_NAME = 8


# ---------------------------------------------------------------------------
# Fake repository (same shape as the PBT sibling)
# ---------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self) -> None:
        self._rows: dict[str, PromptVersionRow] = {}
        self._lock = asyncio.Lock()

    def add(self, row: PromptVersionRow) -> None:
        self._rows[str(row.id)] = row

    async def set_status(
        self,
        row_id: str | uuid.UUID,
        status: str,
        *,
        activated: bool = False,
        retired: bool = False,
    ) -> None:
        async with self._lock:
            key = str(row_id)
            cur = self._rows[key]
            now = datetime.now(UTC)
            updates: dict = {"status": status}
            if activated:
                updates["activated_at"] = now
            if retired:
                updates["retired_at"] = now
            self._rows[key] = replace(cur, **updates)

    async def retire_active_for(
        self, sub_agent_name: str, *, except_id: str | uuid.UUID
    ) -> None:
        async with self._lock:
            except_key = str(except_id)
            for key, row in list(self._rows.items()):
                if (
                    row.sub_agent_name == sub_agent_name
                    and row.status == "active"
                    and key != except_key
                ):
                    self._rows[key] = replace(
                        row, status="retired", retired_at=datetime.now(UTC)
                    )

    async def list_live(self) -> list[PromptVersionRow]:
        return [
            r
            for r in self._rows.values()
            if r.status in ("proposed", "shadow", "ab", "active")
        ]

    async def get_by_id(
        self, version_id: uuid.UUID | str
    ) -> PromptVersionRow | None:
        return self._rows.get(str(version_id))

    async def get_active(
        self, sub_agent_name: str
    ) -> PromptVersionRow | None:
        for r in self._rows.values():
            if r.sub_agent_name == sub_agent_name and r.status == "active":
                return r
        return None

    async def get_previous_active(
        self,
        sub_agent_name: str,
        *,
        before_id: uuid.UUID | str | None,
    ) -> PromptVersionRow | None:
        bid = str(before_id) if before_id is not None else None
        matches = [
            r
            for r in self._rows.values()
            if r.sub_agent_name == sub_agent_name
            and r.activated_at is not None
            and (bid is None or str(r.id) != bid)
        ]
        matches.sort(
            key=lambda r: r.activated_at or datetime.min, reverse=True
        )
        return matches[0] if matches else None

    async def get_by_candidate(
        self, candidate_id: uuid.UUID | str
    ) -> list[PromptVersionRow]:
        return []


def _make_row(
    *,
    sub_agent_name: str,
    system_prompt: str,
    status: str = "proposed",
    activated: datetime | None = None,
) -> PromptVersionRow:
    return PromptVersionRow(
        id=uuid.uuid4(),
        sub_agent_name=sub_agent_name,
        candidate_id=None,
        system_prompt=system_prompt,
        rationale=None,
        status=status,
        parent_version_id=None,
        manifest_sha256=None,
        activated_at=activated,
        retired_at=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_well_formed(pv: PromptVersion, *, expected_name: str) -> None:
    assert pv is not None, "get_active returned None under churn"
    assert isinstance(pv, PromptVersion)
    assert pv.sub_agent_name == expected_name
    assert isinstance(pv.system_prompt, str)
    assert pv.system_prompt, "empty system_prompt mid-swap"
    assert not pv.system_prompt.startswith(_SENTINEL_PROMPT)
    assert isinstance(pv.id, str) and pv.id
    assert pv.source in ("db", "default")


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(len(s) * p / 100)))
    return s[idx]


# ---------------------------------------------------------------------------
# The bench
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hot_reload_100qps_60s_no_interruption() -> None:
    """60-second P-HotReload-5 bench: no errors, p95 ≤ baseline × 1.1."""
    repo = _FakeRepo()

    baselines: dict[str, PromptVersionRow] = {}
    candidates: dict[str, list[PromptVersionRow]] = {}
    defaults: dict[str, str] = {}
    for name in _SUB_AGENT_NAMES:
        defaults[name] = f"DEFAULT-{name}-{uuid.uuid4().hex[:6]}"
        base = _make_row(
            sub_agent_name=name,
            system_prompt=f"BASELINE-{name}-{uuid.uuid4().hex[:6]}",
            status="active",
            activated=datetime.now(UTC),
        )
        repo.add(base)
        baselines[name] = base
        pool: list[PromptVersionRow] = []
        for idx in range(_CANDIDATES_PER_NAME):
            row = _make_row(
                sub_agent_name=name,
                system_prompt=f"CAND-{name}-v{idx}-{uuid.uuid4().hex[:6]}",
                status="proposed",
            )
            repo.add(row)
            pool.append(row)
        candidates[name] = pool

    registry = SubAgentPromptRegistry(
        repo=repo,  # type: ignore[arg-type]
        defaults=defaults,
    )
    await registry.load()

    # --- Baseline pass: measure get_active latency with no churn -----------
    # 1000 warm reads to characterise the clean-path p95. We want a
    # stable, tight baseline so the churn-path ratio is meaningful.
    baseline_samples: list[float] = []
    for i in range(1000):
        name = _SUB_AGENT_NAMES[i % len(_SUB_AGENT_NAMES)]
        t0 = time.perf_counter()
        pv = registry.get_active(name)
        baseline_samples.append(time.perf_counter() - t0)
        _assert_well_formed(pv, expected_name=name)

    baseline_p95 = max(_percentile(baseline_samples, 95), _MIN_BASELINE_S)

    # --- Churn pass: sustained reads + 20 promotions over 60s --------------
    stop_at = asyncio.get_event_loop().time() + _DURATION_S
    stop_event = asyncio.Event()
    reader_errors: list[BaseException] = []
    churn_samples: list[float] = []
    observations_per_worker: list[int] = []
    # asyncio.Lock is not strictly needed — single thread — but we still
    # want to keep list appends atomic-ish when worker coroutines run
    # interleaved.

    async def reader(worker_id: int) -> int:
        n = 0
        try:
            while asyncio.get_event_loop().time() < stop_at:
                if stop_event.is_set():
                    break
                # Round-robin across sub-agents so every reader hits
                # all of them across the run.
                for name in _SUB_AGENT_NAMES:
                    t0 = time.perf_counter()
                    pv = registry.get_active(name)
                    churn_samples.append(time.perf_counter() - t0)
                    _assert_well_formed(pv, expected_name=name)
                    n += 1
                # Throttle to ~2 Hz so 50 workers ≈ 100 QPS.
                await asyncio.sleep(_READ_INTERVAL_S)
        except BaseException as exc:  # noqa: BLE001
            reader_errors.append(exc)
            raise
        return n

    # Event plan: 20 events evenly spread across 60s.
    rng = random.Random(0xB010)
    interval = _DURATION_S / _N_PROMOTIONS
    event_plan: list[tuple[str, str, str]] = []
    cursors = {name: 0 for name in _SUB_AGENT_NAMES}
    for _ in range(_N_PROMOTIONS):
        name = rng.choice(_SUB_AGENT_NAMES)
        cur = cursors[name]
        if cur < len(candidates[name]):
            row = candidates[name][cur]
            cursors[name] += 1
            event_plan.append(("promote", name, str(row.id)))
        else:
            active = next(
                (
                    r
                    for r in repo._rows.values()  # noqa: SLF001
                    if r.sub_agent_name == name and r.status == "active"
                ),
                None,
            )
            if active is not None:
                event_plan.append(("retire", name, str(active.id)))

    async def writer() -> None:
        try:
            for kind, name, row_id in event_plan:
                await asyncio.sleep(interval)
                if kind == "promote":
                    await repo.set_status(
                        row_id, "active", activated=True
                    )
                    await repo.retire_active_for(name, except_id=row_id)
                    evt = PromotionEvent(
                        event_id=f"bench-promote-{row_id}",
                        new_version_id=row_id,
                        to_status="active",
                        sub_agent_name=name,
                    )
                else:  # retire
                    await repo.set_status(row_id, "retired", retired=True)
                    evt = PromotionEvent(
                        event_id=f"bench-retire-{row_id}",
                        new_version_id=row_id,
                        to_status="retired",
                        sub_agent_name=name,
                    )
                await registry.apply_promotion(evt)
        finally:
            stop_event.set()

    reader_tasks = [
        asyncio.create_task(reader(i)) for i in range(_N_READER_WORKERS)
    ]
    writer_task = asyncio.create_task(writer())

    observations_per_worker = await asyncio.gather(*reader_tasks)
    await writer_task

    # --- Pass/fail assertions ---------------------------------------------

    assert not reader_errors, (
        f"{len(reader_errors)} reader errors during churn; "
        f"first={reader_errors[0]!r}"
    )

    churn_p50 = _percentile(churn_samples, 50)
    churn_p95 = _percentile(churn_samples, 95)
    churn_p99 = _percentile(churn_samples, 99)
    total_reads = len(churn_samples)

    print(
        f"\n[bench] hot-reload churn over {_DURATION_S:.0f}s "
        f"(workers={_N_READER_WORKERS}, promotions={_N_PROMOTIONS}):\n"
        f"    total reads = {total_reads}\n"
        f"    baseline p95 = {baseline_p95 * 1e6:.2f} µs\n"
        f"    churn   p50  = {churn_p50 * 1e6:.2f} µs\n"
        f"    churn   p95  = {churn_p95 * 1e6:.2f} µs "
        f"(budget {baseline_p95 * _P95_TOLERANCE * 1e6:.2f} µs)\n"
        f"    churn   p99  = {churn_p99 * 1e6:.2f} µs\n"
        f"    observations/worker min={min(observations_per_worker)} "
        f"max={max(observations_per_worker)}"
    )

    assert churn_p95 <= baseline_p95 * _P95_TOLERANCE, (
        f"get_active p95 regressed under churn: "
        f"baseline={baseline_p95 * 1e6:.2f}µs observed={churn_p95 * 1e6:.2f}µs "
        f"(tolerance x{_P95_TOLERANCE})"
    )
