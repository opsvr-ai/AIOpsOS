# Eval Sets v1 — Ops scenarios

One JSONL file per operational scenario defined in R-11. Each file contains 16 human-authored items (8 positive + 8 negative) that cover the primary failure / success modes for that scenario. The cold-start script (`scripts/eval_cold_start.py`) tops each set up to 40 items by sampling from `agent_trajectories`.

## Files

| File | Scenario | R-11 target |
|------|----------|-------------|
| `knowledge_mgmt_v1.jsonl` | 知识管理 / wiki search & organization | ≥ 6.5/10 |
| `fault_triage_v1.jsonl` | 故障定界 / fault localization | ≥ 6.0/10 |
| `incident_coord_v1.jsonl` | 应急协同 / incident coordination | ≥ 6.0/10 |
| `capacity_mgmt_v1.jsonl` | 容量管理 / capacity analysis | ≥ 5.5/10 |
| `runbook_mgmt_v1.jsonl` | 预案管理 / runbook authoring & lookup | ≥ 6.0/10 |

## Schema

Every line is one JSON object with these fields:

```json
{
  "prompt": "User-facing question or task (required).",
  "expected_tools": ["grep_kb", "search_logs"],
  "expected_outcome": "answered | delegated | refused",
  "grading_rubric": "LLM-as-judge prompt: grading criteria as free text.",
  "weight": 1.0,
  "tags": ["scenario:fault_triage", "positive"]
}
```

Items with `"negative"` in tags are intended to test refusal / deflection paths (e.g. prompts that should be declined, or that must delegate to a sub-agent rather than answering directly).

## Ingestion

```
python -m scripts.eval_annotate --set knowledge_mgmt_v1 \
  --from-jsonl data/eval_sets/v1/knowledge_mgmt_v1.jsonl
```

Ingestion is idempotent — the script hashes each prompt and skips duplicates already present in `eval_set_items`.
