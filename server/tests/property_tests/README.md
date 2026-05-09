# Property-Based Test Registry

**Spec:** `.kiro/specs/agent-runtime-optimization-evolution` — task 28.1
(Phase N — cross-cutting / Correctness Property registry).

**Validates:** `design.md § Correctness Properties` and the
`requirements.md § "Correctness Properties 与 Requirements 映射"` table.

---

## Purpose

This package is the authoritative index of the **19 named correctness
properties** that the `agent-runtime-optimization-evolution` feature
promises to uphold. Each property has:

1. A spec identity — `P-*-N`, defined in `design.md § Correctness Properties`.
2. One or more **requirement IDs** it pins — see the requirements mapping table.
3. Exactly **one canonical test file** that runs Hypothesis strategies
   against it.

Property tests are not always physically stored under
`tests/property_tests/`. Tests that are tightly coupled to a feature
area (e.g. `P-Memory-*` lives next to the memory service unit tests)
live alongside their subject. This README maps every `P-*` id to the
file that actually runs the property so reviewers can trace a
requirement to its guarding test in one hop.

## Shared fixtures (`conftest.py`)

The `conftest.py` in this directory centralises the fakes every PBT
needs so individual suites stop carrying private copies. Fixtures:

| Fixture | Purpose |
|---------|---------|
| `fake_llm` | Fresh `_ScriptedLLM` — deterministic, canned-body LLM double. |
| `fake_kafka_producer` | Recording `send_and_wait` producer (plus `topics` / `bodies` helpers). |
| `asyncmock_kafka_producer` | `AsyncMock` variant for assertion-style producer tests. |
| `fake_kafka_consumer` | Queue-backed `aiokafka.ConsumerRecord` iterator. |
| `in_memory_prompt_registry` | Loaded `SubAgentPromptRegistry` on a `_SharedRepo` fake. |
| `hypothesis_default_settings` | Shared `max_examples=100`, `deadline=None`. |

Individual PBT modules that need tighter budgets (200 examples for
attribution coverage, 10 for 500-event bursts) override on a per-test
basis — the default only provides a sensible floor.

## Property → Test mapping (19 properties)

All files are rooted at `server/`. Source for each property spec is
`.kiro/specs/agent-runtime-optimization-evolution/design.md § Correctness Properties`.

| Property ID | Requirements | Description | Test file |
|-------------|--------------|-------------|-----------|
| **P-Router-1** | R-10.5 | Router is idempotent within the 30s cache window: same `(user, session, message)` → same `RouterDecision` (cache hit skips the LLM). | `tests/agent_runtime/test_router_llm.py` |
| **P-Router-2** | R-1.3, R-10.4 | RouterLLM degrades safely on any failure (timeout / parse error / exception) → always returns `route=executor` with full tool set. | `tests/agent_runtime/test_router_llm.py` |
| **P-Router-3** | R-1.4, R-10.6 | `route='direct'` implies no ExecutorAgent is built and no tool dispatch happens; ops-keyword direct decisions are promoted to `executor`. | `tests/agent_runtime/test_router_llm.py` |
| **P-Dispatcher-1** | R-1.7 | Parallel-safe tool calls are order-invariant: any input permutation yields the same `{call_id → output}` map. | `tests/agent_runtime/test_dispatcher_properties.py` |
| **P-Dispatcher-2** | R-1.7, R-8.3 | Destructive tools require approval: without `{approved: True}` the tool is never invoked; the call returns `REJECTED`. | `tests/agent_runtime/test_dispatcher_properties.py` |
| **P-Dispatcher-3** | R-1.7 | Concurrent calls to a stateless parallel-safe tool produce deterministic, non-cross-talking results (distinct args → distinct cache keys). | `tests/agent_runtime/test_dispatcher_properties.py` |
| **P-Memory-1** | R-2.3 | Consolidation preserves every sampled fact: each key fact from input turns ends up in the new memory set, the surviving baseline, or the explicit `ignored` list. | `tests/workers/test_consolidation_no_info_loss.py` |
| **P-Memory-2** | R-2.3 | Supersede is monotone: `is_archived=true ⇒ superseded_by ≠ null`, and the referenced row is itself still live. | `tests/workers/test_consolidation_supersede.py` |
| **P-Memory-3** | R-2.7 | HOT cache stays in lockstep with `sessions.hot_memory_version` after every consolidation. | `tests/workers/test_consolidation_hot_version.py` |
| **P-Memory-4** | R-2.4 | Embeddings are idempotent: `embed([t, t]) == [v, v]` and the second call for the same content-hash is served from cache. | `tests/memory/test_embedding_service.py` |
| **P-Memory-5** | R-2.5 | With an empty `embedding_api_key`, `warm_recall` degrades to ILIKE and never raises. | `tests/memory/test_embedding_service.py` (`enabled=False`) + `tests/memory/test_warm_recall_integration.py` |
| **P-Sleep-1** | R-2.9 | Background consolidation never blocks `/chat`: 100-concurrent chat p95 ≤ baseline × 1.2 while 4 consolidations run. | `tests/bench/test_sleep_non_blocking.py` |
| **P-Sleep-2** | R-2.10 | Daily token budget is a hard ceiling: over N consolidations total LLM token cost ≤ per-space budget. | `tests/services/test_sleep_scheduler.py` |
| **P-Sleep-3** | R-2.2, R-2.14 | Single-session consolidation is mutually exclusive: ≥ 10 concurrent tasks for the same session → exactly 1 runs, others skip via the Redis lock. | `tests/workers/test_consolidation_lock_concurrency.py` |
| **P-Evolve-1** | R-3.4 | Candidate state machine is monotone: only the allowed edges fire; any reverse / terminal-escape transition raises and the row is untouched. | `tests/evolution/test_state_machine.py` |
| **P-Evolve-2** | R-3.6 | `shadow → ab` and `ab → active` transitions require `candidate_score ≥ baseline_score − ε` (ε = 0.02). | `tests/evolution/test_no_score_regression.py` |
| **P-Evolve-3** | R-3.9 | Rollback restores the immediately-previous active version and retires the one that was active when `rollback()` was called. | `tests/evolution/test_rollback_pbt.py` |
| **P-Evolve-4** | R-3.7 | Shadow-mode runs never leak into user responses: user-visible output is byte-equal to the baseline response. | `tests/evolution/test_shadow_user_invisible.py` |
| **P-HotReload-1** | R-3.15, R-3.16 | Atomic prompt swap: while `apply_promotion` runs, every concurrent `get_active` read sees either `prev` or `new` — never a half-built record. | `tests/evolution/test_prompt_registry_hotreload.py` |
| **P-HotReload-2** | R-3.19 | Rollback duality: `promote(new)` followed by `rollback()` leaves `registry.get_active(s).id == previously_active.id` within 5 s. | `tests/evolution/test_rollback_duality.py` |
| **P-HotReload-3** | R-3.15, R-3.17 | Multi-instance convergence: three FastAPI replicas reading the shared promotion topic converge on the new `active` version id within 5 s. | `tests/evolution/test_multi_instance_convergence.py` |
| **P-HotReload-4** | R-3.18 | Promotion idempotency: re-delivering the same `PromotionEvent` K times leaves the registry in the same terminal state as a single delivery. | `tests/evolution/test_prompt_registry_hotreload.py` |
| **P-HotReload-5** | R-3.16 | No interruption under reload churn: 100 QPS `/chat/stream` for 60 s + 20 interleaved promote / rollback operations → zero 5xx, p95 ≤ baseline × 1.1. | `tests/evolution/test_hot_reload_no_interruption.py` (scaled) + `tests/bench/test_hot_reload_bench.py` (full 60 s profile) |
| **P-HotReload-6** | R-3.22, R-3.25 | Sentinel is always replaced: `request.system_message.text` never starts with `_SENTINEL_PROMPT` downstream of `DynamicSystemPromptMiddleware`. | `tests/property_tests/test_hotreload_6_sentinel_replaced.py` |
| **P-HotReload-7** | R-3.25 | Suffix preservation: if an outer middleware has appended `X` after the sentinel, the replaced text equals `registry.prompt + X` exactly. | `tests/property_tests/test_hotreload_7_suffix_preserved.py` |
| **P-HotReload-8** | R-3.24 | Metadata attribution: every sub-agent LLM call annotates `request.metadata` with `sub_agent_name / prompt_version_id / prompt_version_no / prompt_source`. | `tests/property_tests/test_hotreload_8_metadata.py` |
| **P-Observe-1** | R-5.10, R-6.3 | Zero-loss-or-counted: every emitted `TrajectoryEvent` either lands in `agent_trajectories` within 30 s or is counted in `trajectory_emit_dropped`. | `tests/agent_runtime/test_trajectory_zero_loss.py` |
| **P-FF-1** | R-7.1, R-7.3 | Feature-flag propagation: a DB mutation is reflected by `is_enabled` within the 15 s budget (test asserts ≤ 2 s). | `tests/feature_flags/test_flag_propagation.py` |

> 19 named properties. ``P-HotReload-*`` spans 1–8, so "19" in the
> requirements doc refers to: 3 P-Router + 3 P-Dispatcher + 5 P-Memory
> + 3 P-Sleep + 4 P-Evolve + 8 P-HotReload + 1 P-Observe + 1 P-FF =
> 28 test entries total (with 8 HotReload sub-properties, some
> requirements groups collapse in the requirements table).

## Running the PBT suite

All property-based tests are marked with `@pytest.mark.property`.
Run the suite:

```powershell
cd server
& ".\.venv\Scripts\python3.exe" -m pytest -m property -x --tb=short
```

Selected other gates (for reference — they are not part of the
standard PBT run):

* `-m benchmark` — sleep / hot-reload / dispatcher benchmarks gated
  by `RUN_BENCH=1`.
* `-m "property and not kafka"` — skips the live-broker tests
  (`P-HotReload-3`) when no broker is reachable at `localhost:9094`.
* `-m live_llm` — gated by `RUN_LIVE_LLM=1`; this feature has **no**
  PBTs that require a live LLM — all 28 entries above use either
  scripted fakes or deterministic stubs.

## Adding a new property

1. Name it `P-<Area>-<N>` and add it to
   `design.md § Correctness Properties` with the ∀-formula.
2. Map it to one or more requirement IDs in
   `requirements.md § "Correctness Properties 与 Requirements 映射"`.
3. Add a row to the table above pointing at the implementing test
   file. Keep the "one property per test file" rule unless the
   property is a close sibling of an existing one (see the
   three `P-Dispatcher-*` sharing `test_dispatcher_properties.py`).
4. The test itself should:
   * Use the shared fixtures from this `conftest.py` where applicable.
   * Include a header comment that names the property, its
     requirements, and links back to the spec.
   * Be marked `@pytest.mark.property`.
   * Compose `DEFAULT_PBT_SETTINGS` via the `hypothesis_default_settings`
     fixture unless the property needs a custom example budget.

## Why two locations?

Some properties live under `tests/property_tests/` (generic /
middleware-shaped); others live with the service they guard (memory,
evolution, workers). Two reasons:

* **Debugging ergonomics** — a failing `P-Memory-3` is easier to
  diagnose next to `test_consolidation.py` than in a sibling package.
* **Import locality** — property tests that only use a feature's
  private fakes don't benefit from being globally visible.

This README is the single place that bridges the two layouts; keep it
up to date whenever a property file moves.
