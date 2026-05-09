# Evolution Operator Runbook

**Spec:** `.kiro/specs/agent-runtime-optimization-evolution` — 任务 26.1
**Requirements:** R-11.1 ~ R-11.5 (per-scenario baseline certification); R-3.x (evolution)
**Related code:**
- CLI `server/scripts/eval_run.py` (task 17.4)
- Scoring aggregator `server/src/services/evaluation/scoring.py` (task 17.5)
- Seed sets `data/eval_sets/v1/*.jsonl` (task 17.3)
- Smoke test `server/tests/evaluation/test_eval_harness_smoke.py` (task 26.1)
- CLI `server/scripts/evo_ctl.py` (task 23.5)

This runbook covers the operational tasks an administrator needs for
the self-evolution pipeline: certifying per-scenario baselines,
inspecting candidate state, rolling back a promotion, and tuning
worker concurrency.

---

## 1. Scenario baseline certification (R-11.1 ~ R-11.5)

The release gate for this spec requires weighted-mean baseline scores
on each of the five ops scenarios. Targets (on a 0..10 scale, the
aggregator reports in 0..1):

| Set | Target |
|-----|--------|
| `knowledge_mgmt_v1` | ≥ 6.5 / 10 |
| `fault_triage_v1`   | ≥ 6.0 / 10 |
| `incident_coord_v1` | ≥ 6.0 / 10 |
| `capacity_mgmt_v1`  | ≥ 5.5 / 10 |
| `runbook_mgmt_v1`   | ≥ 6.0 / 10 |

### 1.1 Prerequisites

- Production-equivalent `.env` (model provider, embedding key, DB / Redis / Kafka).
- Python venv at `server/.venv` with project deps installed.
- LLM judge (task 22.2) available — today the CLI exposes `--grader stub | manual | llm`; `llm` is Phase K (task 22.2) and the runbook below assumes that ships before certification.
- Enough token budget for ~200 judge invocations (40 items × 5 sets).

### 1.2 Running one set

From the repo root, for a single scenario:

```powershell
cd server
& ".\.venv\Scripts\python3.exe" -m scripts.eval_run `
    --set knowledge_mgmt_v1 `
    --grader llm `
    --out ..\artifacts\eval\baseline-knowledge_mgmt_v1.json
```

The output JSON carries `aggregate.weighted_mean` (0..1). Multiply by
10 to compare against the R-11.x target.

### 1.3 Running all five (shell loop)

```powershell
cd server
$sets = @(
    'knowledge_mgmt_v1',
    'fault_triage_v1',
    'incident_coord_v1',
    'capacity_mgmt_v1',
    'runbook_mgmt_v1'
)
foreach ($set in $sets) {
    & ".\.venv\Scripts\python3.exe" -m scripts.eval_run `
        --set $set `
        --grader llm `
        --out ..\artifacts\eval\baseline-$set.json
}
```

### 1.4 Recording results

1. Collect the five `baseline-<set>.json` artifacts.
2. Multiply each `aggregate.weighted_mean` by 10.
3. Paste the numbers into
   `docs/RELEASE_NOTES_agent_runtime_optimization_evolution_v1.md`
   under **"Scenario baseline certification"**, in the table.
4. If any target fails, open an investigation task (per task 26.1,
   a miss is **not** a merge blocker for this spec) and move the
   failing scenario from **"Verified"** to **"Known gap"** in the
   release notes.

The CI-gated smoke test
`server/tests/evaluation/test_eval_harness_smoke.py` exercises the
harness wiring on every set but does **not** verify the R-11.x
thresholds (no live LLM in CI).

---

## 2. Inspecting evolution state

### 2.1 List live prompt versions

```powershell
cd server
& ".\.venv\Scripts\python3.exe" -m scripts.evo_ctl list-versions --agent monitor
```

### 2.2 List candidates by status

```powershell
cd server
& ".\.venv\Scripts\python3.exe" -m scripts.evo_ctl list-candidates --status shadow
```

### 2.3 Diff two prompt versions

```powershell
cd server
& ".\.venv\Scripts\python3.exe" -m scripts.evo_ctl diff --agent monitor --from 7 --to 8
```

---

## 3. Rollback (R-3.9, R-3.19)

When a promoted skill or prompt turns out to regress production, roll
back. The operation is transactional + publishes the appropriate
Kafka event; all FastAPI instances converge within 5 s.

```powershell
cd server
& ".\.venv\Scripts\python3.exe" -m scripts.evo_ctl rollback --agent monitor
```

- For skills: the CLI calls `POST /api/control/skills/{name}/rollback`.
- For sub-agent prompts: `POST /api/control/sub-agents/{name}/rollback`.
- For tool configs: the pre-patch snapshot stored on the candidate is reapplied.

Every rollback is recorded in `audit_logs` (admin-only endpoint) and
emits `ops.agent.promotion` so all instances hot-reload.

---

## 4. Worker concurrency and degradation

### 4.1 Sleep queue depth

Grafana panel: `sleep_queue_depth` (emitted by `SleepScheduler`).

- **Green:** `< 200`.
- **Yellow:** `200 ≤ x < 500`.
- **Red (degraded mode):** `≥ 500` → `consolidation_degraded_total`
  starts climbing; the worker skips embedding for new jobs to shed
  load (R-2.11). Recovery is automatic once the queue drains.

### 4.2 Tuning concurrency

- Live: bump `max_concurrent_consolidations` in the
  `runtime_feature_flags` row (`data.max_concurrent_consolidations`).
  `FeatureFlagService` picks it up within 15 s (R-7.3).
- Worker-count: edit `deploy/docker-compose.dev.yml` → `worker`
  service `-c` flag (default 4) and restart just that service.

### 4.3 Skill candidate count

Grafana panel: `skill_candidate_count{status}`.

- Sustained `proposed > 50` suggests the evaluator is falling behind
  — check `evaluator` queue depth and Celery worker health.

---

## 5. Emergency: disable evolution entirely

Flip the following feature flags to `enabled=false`:

- `reflection_worker_enabled`
- `evaluator_worker_enabled`
- `promoter_enabled`

Running candidates hold at their current status; no new promotions.
Re-enable by flipping the flags back; no code deploy required.
