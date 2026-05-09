# Release Notes — Agent Runtime Optimization & Evolution v1

**Spec:** `.kiro/specs/agent-runtime-optimization-evolution`
**Phases:** A–N (tasks 1 – 26)
**Related docs:**
- Admin runbook: `docs/admin-guide/evolution-runbook.md`
- RouterLLM rollout: `docs/admin-guide/router-llm-rollout.md`
- Spec: `.kiro/specs/agent-runtime-optimization-evolution/requirements.md`, `./design.md`

---

## Highlights

- `/chat/stream` now goes through `RuntimeGateway`; first-token p95 target ≤ 1 s (R-1.5).
- Memory consolidation moved off the request path into a Celery worker behind `consolidation_worker_enabled` (R-2.1 – R-2.3).
- Self-evolution pipeline: reflector → evaluator → shadow → A/B → promoter → rollback for `skill`, `prompt_patch`, and `tool_config` candidates (R-3.x).
- Kafka admin surface: topic CRUD, consumer group offsets, browser, DLQ, schema registry (R-5.x).
- OpenTelemetry tracing + Prometheus `/metrics` wired through both execution and control services (R-6.x).

Full acceptance-criterion-to-task traceability lives in
`.kiro/specs/agent-runtime-optimization-evolution/tasks.md`.

---

## New Control APIs

- `GET|POST|PUT|DELETE /api/control/kafka/topics`
- `GET|POST /api/control/kafka/consumer-groups` (+ `POST /reset-offset`)
- `GET /api/control/kafka/browser`
- `GET|POST /api/control/kafka/dlq`
- `GET|POST /api/control/kafka/schemas`
- `GET /api/control/sub-agents/{name}/prompt-versions` (+ `/diff`, `/activate`)
- `POST /api/control/sub-agents/{name}/rollback`
- `GET /api/control/candidates`, `POST /api/control/candidates/{id}/promote|reject`
- `CRUD /api/v1/runtime-flags/{key}` (seeded by `feature_flags_bootstrap.py`)

All mutating endpoints require `require_admin` and write to `audit_logs`.

---

## Migration steps

1. Apply Alembic migrations (`alembic upgrade head`):
   - `add_trajectory_and_evolution_tables`
   - `extend_agent_memories_and_sessions`
   - `add_wiki_compile_log`
2. Run data backfills:
   - `python -m scripts.backfill_memory_hash_and_embedding`
   - `python -m scripts.wiki_precompute_summaries`
3. Deploy the `worker` service from `deploy/docker-compose.dev.yml`.
4. Confirm `/metrics` returns 200 and Kafka topics listed in
   `design.md § "Kafka topics"` have been auto-ensured.
5. Default-on flags (set during Phase M, task 24.1) are seeded
   automatically. `router_llm_enabled` rolls out at 10 %; follow the
   RouterLLM rollout runbook to go to 100 %.

---

## Scenario baseline certification (R-11.1 ~ R-11.5)

Per-scenario weighted-mean targets for `scripts/eval_run.py --baseline`:

| Set | Target (× 10) | Recorded baseline | Status |
|-----|---------------|-------------------|--------|
| `knowledge_mgmt_v1` | ≥ 6.5 | _pending live-LLM run_ | **Known gap** |
| `fault_triage_v1`   | ≥ 6.0 | _pending live-LLM run_ | **Known gap** |
| `incident_coord_v1` | ≥ 6.0 | _pending live-LLM run_ | **Known gap** |
| `capacity_mgmt_v1`  | ≥ 5.5 | _pending live-LLM run_ | **Known gap** |
| `runbook_mgmt_v1`   | ≥ 6.0 | _pending live-LLM run_ | **Known gap** |

The harness plumbing is fully exercised in CI via
`server/tests/evaluation/test_eval_harness_smoke.py` (task 26.1) —
each of the five committed seed files is loaded, graded with a
deterministic stub grader, and aggregated end-to-end. That test
verifies the pipeline (loader, `_run_items`, `aggregate`, JSON report
round-trip) but does **not** verify the R-11.x score thresholds,
since those require a real LLM judge (task 22.2) and ~200 model
invocations, which are not appropriate for PR CI.

Operators fill in the **Recorded baseline** column using the steps in
`docs/admin-guide/evolution-runbook.md § 1. Scenario baseline
certification`; a run takes roughly 10 – 15 minutes per set. Per task
26.1, a missed target is **not** a merge blocker for this spec — the
investigation task is filed and the status is tracked here as a
**Known gap** until a follow-up verifies it.

---

## Known gaps

- **Scenario baselines pending.** See the table above. Real LLM
  baselines have not yet been recorded in CI; the numbers need to
  be filled in by an operator using the runbook. Investigation of
  any miss is tracked as a follow-up task, not a blocker for this
  merge.
- **Live LLM grader.** `scripts/eval_run.py --grader llm` is a
  Phase K deliverable (task 22.2); in this spec's CI only `stub`
  and `manual` graders are exercised.

---

## Rollback plan

For this release as a whole, rollback = flipping feature flags off:

- `router_llm_enabled` → `enabled=false` (main chat path falls back to legacy)
- `gateway_enabled` → `enabled=false` (legacy `/chat/stream` inline path retained one release)
- `consolidation_worker_enabled` → `enabled=false` + enable `memory_legacy_sync` (task 25.2 is still reversible)
- `wiki_compile_worker_enabled` → `enabled=false`
- `sleep_scheduler_v2_enabled` → `enabled=false`

None of the flags require a code deploy to flip; `FeatureFlagService`
converges within 15 s (R-7.3). Individual evolution rollbacks (skills
/ prompts / tool configs) are handled by the evolution runbook § 3.
