# Implementation Plan: Agent Runtime Optimization & Evolution

**Related:** `./design.md`, `./requirements.md`
**Workflow:** design-first
**Status:** Ready to execute

## Conventions

- Leaf tasks use `- [ ]` (required) or `- [ ]*` (optional). Only leaves have status.
- Each leaf task maps to requirement ids `(R-x.y)` and correctness properties `(P-*)`.
- Tests authored alongside the code; each task's DoD includes tests green.
- `PBT` = property-based test using `hypothesis`; `IT` = integration test (real PG + Redis + Kafka from `deploy/docker-compose.dev.yml`).
- Secrets / embedding keys are read from existing `server/src/core/config.py`; no new secret channels.
- All new modules land under `server/src/...` paths enumerated in design.md.
- Where we touch routers/handlers, keep `main_execution.py` and `main_control.py` separation.
- Every task description begins with a concrete action verb; no open-ended "design X" leaves.

## Phase A — Observability + Data Model (3d)

- [x] 1. Data model migrations
  - [x] 1.1 Create Alembic revision `add_trajectory_and_evolution_tables`
    - New tables: `agent_trajectories`, `skill_candidates`, `skill_evaluations`, `eval_set_items`, `runtime_feature_flags`, `skill_versions`, `sub_agent_prompt_versions`, `kafka_topic_schemas`.
    - Exact DDL from design.md § Data Models; add `ON DELETE` behavior as specified.
    - File: `server/alembic/versions/YYYYMMDDHHMM_add_trajectory_and_evolution_tables.py`.
    - Requirements: R-9.1; Phase A DoD.
  - [x] 1.2 Create Alembic revision `extend_agent_memories_and_sessions`
    - ALTER `agent_memories`: `content_hash`, `is_archived`, `superseded_by`, `pinned`, `last_used_at` + indexes (`agent_memories_active_idx`, `agent_memories_embed_idx` with HNSW).
    - ALTER `sessions`: `last_consolidation_at`, `consolidation_count`, `hot_memory_version`.
    - File: `server/alembic/versions/YYYYMMDDHHMM_extend_memories_and_sessions.py`.
    - Requirements: R-9.1.
  - [x] 1.3 Author SQLAlchemy models for new tables
    - Files:
      - `server/src/models/trajectory.py` (`AgentTrajectory`)
      - `server/src/models/evolution.py` (`SkillCandidate`, `SkillEvaluation`, `EvalSetItem`, `SkillVersion`, `SubAgentPromptVersion`)
      - `server/src/models/runtime_flag.py` (`RuntimeFeatureFlag`)
      - `server/src/models/kafka_schema.py` (`KafkaTopicSchema`)
    - Wire into `server/src/models/__init__.py`.
    - Requirements: R-3.x model-backing.
  - [x] 1.4 Extend `agent_memories` and `sessions` models
    - Update `server/src/models/memory.py` and `server/src/models/session.py` with new columns.
    - Requirements: R-2.3, R-2.7.
  - [x] 1.5 Write migration round-trip test
    - `tests/db/test_migrations_roundtrip.py` — applies up → down → up, asserts table existence and column types via `inspector`.
    - Requirements: R-9.1. PBT not needed (single-shot).

- [x] 2. OpenTelemetry + Prometheus bootstrap
  - [x] 2.1 Add dependencies
    - `server/pyproject.toml`: `opentelemetry-api ^1.27`, `opentelemetry-sdk ^1.27`, `opentelemetry-instrumentation-fastapi ^0.48b0`, `opentelemetry-instrumentation-sqlalchemy ^0.48b0`, `prometheus-client ^0.21`, dev: `hypothesis ^6.100`, `pytest-benchmark ^4.0`.
    - Requirements: design.md § Dependencies.
  - [x] 2.2 Create tracing module
    - `server/src/core/tracing.py` exposing `init_tracing(app)` + `tracer` singleton.
    - Accepts `OTEL_EXPORTER_OTLP_ENDPOINT` env var; default = stdout console exporter.
    - Instrument FastAPI + SQLAlchemy (sync + async).
    - Requirements: R-6.1.
  - [x] 2.3 Create Prometheus metrics module
    - `server/src/core/metrics.py` defining all metrics listed in R-6.2.
    - Expose `/metrics` endpoint in both `main_execution.py` and `main_control.py`.
    - Requirements: R-6.2, R-6.4.
  - [x] 2.4 Wire tracing init into app startup
    - Call `init_tracing(app)` from `server/src/main.py` after `FastAPI` construction.
    - Skip in test mode via `settings.testing`.
    - Requirements: R-6.1.
  - [x] 2.5 Add `/metrics` smoke test
    - `tests/observability/test_metrics_endpoint.py` — GET returns 200 + `Content-Type: text/plain; version=0.0.4`.
    - Requirements: R-6.4.

## Phase B — Celery Worker + Kafka Management Surface (5d)

- [x] 3. Celery worker app
  - [x] 3.1 Build Celery app skeleton
    - `server/src/workers/app.py` — `celery = Celery('aiopsos')` with Redis broker + Redis result backend, 3 queues: `memory`, `evolution`, `wiki`.
    - Task auto-discovery: `celery.autodiscover_tasks(['src.workers.tasks'])`.
    - Requirements: Phase B DoD.
  - [x] 3.2 Create empty task modules (stubs)
    - `server/src/workers/tasks/__init__.py`, `memory_consolidation.py`, `wiki_compile.py`, `reflection.py`, `evaluator.py` (each exporting `@celery.task`-decorated no-op for now).
    - Requirements: Phase B bootstrap.
  - [x] 3.3 Add worker service to docker-compose.dev.yml
    - `deploy/docker-compose.dev.yml`: `worker` service, same image as server, command `celery -A src.workers.app worker -Q memory,evolution,wiki -c 4 -l info`, depends on db/redis/kafka.
    - Healthcheck via `celery inspect ping`.
    - Requirements: Phase B DoD.
  - [x] 3.4 Allinone in-process worker
    - When `settings.service_type == "allinone"`, spawn an in-process Celery worker thread on startup via `app.control.pool.start()` or `worker_main(['worker', '--pool=solo'])`.
    - File: `server/src/workers/embedded.py`.
    - Requirements: design.md § Infra.

- [x] 4. Kafka admin service
  - [x] 4.1 Add Kafka client deps
    - Already have `kafka-python`. Add `aiokafka ^0.12` (async producer/consumer + admin).
    - Requirements: R-5.x.
  - [x] 4.2 `KafkaAdminService`
    - `server/src/services/kafka/admin.py` — `list_topics()`, `describe_topic(name)`, `create_topic(...)`, `alter_topic(...)`, `delete_topic(name, confirm=True)`, `list_consumer_groups()`, `describe_group(name)`, `reset_offset(group, topic, partition, target)`.
    - Uses `AIOKafkaAdminClient`.
    - Unit tests with mocked admin client.
    - Requirements: R-5.2, R-5.3.
  - [x] 4.3 `KafkaMetricsCollector`
    - `server/src/services/kafka/metrics.py` — 5s loop, pull lag per group/topic/partition, ISR status, DLQ growth rate; emit to Prometheus gauges.
    - Started as asyncio task from `main_control.py` startup.
    - Requirements: R-5.7, R-6.2.
  - [x] 4.4 `KafkaBrowser`
    - `server/src/services/kafka/browser.py` — seek to offset/timestamp, read N messages, regex-match key/value/header server-side.
    - Requirements: R-5.4.
  - [x] 4.5 `KafkaDLQManager`
    - `server/src/services/kafka/dlq.py` — list by tag/time, batch replay (to original or overridden topic), batch discard, mark-handled (writes to Redis set).
    - Replay is idempotent: each DLQ record has `original_message_id`; replay sets a Redis key `dlq:replayed:{id}` to dedupe.
    - Requirements: R-5.5; P-Kafka-DLQ-replay-idempotent.
  - [x] 4.6 `KafkaSchemaRegistry`
    - `server/src/services/kafka/schema.py` — CRUD on `kafka_topic_schemas` table; `validate(topic, payload, version)` returns `(ok, errors)`; producer wrapper raises → push-to-DLQ on invalid.
    - Use `jsonschema` (add dep `jsonschema ^4.23`).
    - Requirements: R-5.6.
  - [x] 4.7 Topic ensure on startup
    - `server/src/services/kafka/ensure.py` — read list from design.md § "Kafka topics" + "平台默认注册的 topics"; for each missing topic create with spec'd partitions/replication/retention/compaction.
    - Called from `main_control.py` startup; if fails, app remains up but `/readyz` returns unhealthy.
    - `readyz` endpoint added to `main_control.py`.
    - Requirements: R-5.1.
  - [x] 4.8 Control API
    - `server/src/api/control/kafka.py` — routes: `/api/control/kafka/topics` (GET/POST/PUT/DELETE), `/consumer-groups` (GET + reset-offset POST), `/browser` (GET), `/dlq` (GET/POST batch actions), `/schemas` (GET/POST).
    - All mutating endpoints require `require_admin` + emit to `audit_logs` (existing table).
    - Requirements: R-5.2 – R-5.6, R-5.8.
  - [x] 4.9 PBT: DLQ replay idempotency
    - `tests/kafka/test_dlq_idempotency.py` — hypothesis generates N random DLQ entries, replay same batch twice, assert target topic message count == first replay count.
    - Requirements: R-5.5. Property: same-id replay is a no-op.
  - [x] 4.10 IT: topic auto-ensure
    - `tests/kafka/test_topic_ensure_integration.py` — start against dev kafka, drop default topic, restart, assert topic recreated with spec.
    - Requirements: R-5.1.
  - [x] 4.11* Kafka management UI scaffold (optional-can-defer-to-v1.1)
    - `web/src/pages/admin/kafka/` — React pages for topics/groups/browser/dlq/schemas; calls control API.
    - Requirements: R-5.8. **Optional in this feature; server-side REST is primary.**

## Phase C — Trajectory Sink + Feature Flag Service (2d)

- [x] 5. Feature flag service
  - [x] 5.1 `FeatureFlagService`
    - `server/src/services/feature_flags.py` — reads `runtime_feature_flags`; 10s background refresh; method `is_enabled(key, user_id=None) -> bool` using stable hash `xxh3(user_id + key) % 100 < rollout_percent`.
    - Thread-safe (asyncio.Lock on refresh, dict swap on read).
    - Unit tests: refresh correctness, rollout_percent bucketing fair.
    - Requirements: R-7.1, R-7.2, R-7.3.
  - [x] 5.2 Admin API for flags
    - `server/src/api/control/runtime_flags.py` — CRUD on flags.
    - Requirements: R-7.1.
  - [x] 5.3 Seed default flags on app boot
    - `server/src/services/feature_flags_bootstrap.py` — insert row if absent for each key listed in R-7.4; initial `enabled=false`.
    - Requirements: R-7.4.
  - [x] 5.4 PBT: flag effective within 15s
    - `tests/feature_flags/test_flag_propagation.py` — hypothesis generates rollout percent; update DB, measure time until `is_enabled` reflects new value; assert ≤ 15s.
    - Property: P-FF-1.
    - Requirements: R-7.1, R-7.3.

- [x] 6. TrajectorySink
  - [x] 6.1 Define `TrajectoryEvent` schema
    - `server/src/schemas/trajectory.py` — pydantic BaseModel matching design.md § TrajectorySink.
    - Register `TrajectoryEvent.v1` schema in `kafka_topic_schemas` via seed script.
    - Requirements: R-5.6, R-6.2.
  - [x] 6.2 `TrajectorySink` implementation
    - `server/src/services/agent_runtime/trajectory.py` — async class with internal `asyncio.Queue(maxsize=10000)` + background flusher task that batches to PG (`INSERT ... RETURNING`) and produces to Kafka (`ops.agent.trajectory`).
    - `emit()` is sync-like fire-and-forget (puts on queue; if full, drops and increments `trajectory_emit_dropped`).
    - PII sanitizer: `sanitize_pii(data)` scrubs known sensitive fields before write.
    - Requirements: R-2.1, R-5.10, R-6.3, R-8.2.
  - [x] 6.3 Wire TrajectorySink into `/chat` and `/chat/stream`
    - `server/src/api/execution/router.py` — after each turn, call `trajectory_sink.emit_turn(...)`, `emit_tool_call(...)`, `emit_router_decision(...)` as appropriate.
    - Behind flag `trajectory_enabled` (default true once merged).
    - Requirements: R-2.1.
  - [x] 6.4 PBT: P-Observe-1 zero-loss-or-counted
    - `tests/agent_runtime/test_trajectory_zero_loss.py` — emit N events; within 30s either all N present in `agent_trajectories` or `trajectory_emit_dropped` counter >= (N - present). Use hypothesis to vary burst shapes.
    - Requirements: R-6.3, R-5.10.

## Phase D — Embedding Service + Memory Tier Read Path (4d)

- [x] 7. EmbeddingService
  - [x] 7.1 Implement `EmbeddingService`
    - `server/src/services/memory/embedding.py` — class with `embed(list[str])`, `embed_one(str)`.
    - Batching: accumulate up to 16 items or 30ms window.
    - Content-hash cache: Redis `emb:{model}:{sha256(text)[:16]}` TTL 7d.
    - Fallback: if `settings.embedding_api_key == ""`, `embed` returns zero-length list and flag `self.enabled = False`.
    - Uses `langchain_openai.OpenAIEmbeddings` (DeepSeek-compatible endpoint via `model_providers` table).
    - Requirements: R-2.4, R-2.5.
  - [x] 7.2 PBT: P-Memory-4 embedding idempotency
    - `tests/memory/test_embedding_idempotent.py` — `embed([t, t])` returns `[v, v]`; cache hit for same `content_hash(t)`.
    - Requirements: R-2.4.
  - [x] 7.3 Fallback behavior test
    - `tests/memory/test_embedding_fallback.py` — with empty key, `warm_recall` still returns results via ILIKE, no exception.
    - Requirements: R-2.5, P-Memory-5.

- [x] 8. MemoryTier read path
  - [x] 8.1 `MemoryTier.hot`
    - `server/src/services/memory/tier.py` — `hot(ctx) -> HotBlock`. Reads Redis `session:{sid}:hot_mem`; on miss, builds from DB (`agent_memories` last-N personal + pinned team + user profile), caches with TTL 10min.
    - `HotBlock` dataclass with `user_profile / space_ctx / last_k_summary / top_recent_personal / top_pinned_team`, plus `version` int.
    - Requirements: R-2.6.
  - [x] 8.2 `MemoryTier.warm_recall`
    - Same module — `warm_recall(ctx, query, k=8)`.
    - If embeddings enabled: pgvector HNSW ANN (top 3k), then hybrid scoring `0.5*sim + 0.3*recency + 0.2*pinned`, take top k.
    - Else: ILIKE fallback.
    - Requirements: R-2.5, R-2.8.
  - [x] 8.3 `MemoryTier.cold_lookup`
    - `cold_lookup(slug) -> str | None` — reads wiki page frontmatter `precomputed_summary`; if missing returns None.
    - Requirements: R-2.13.
  - [x] 8.4 Hit ratio instrumentation
    - Emit `memory_recall_hit_ratio{tier}` gauge and `embedding_cache_hit_ratio`.
    - Requirements: R-6.2.
  - [x] 8.5 PBT: hybrid scoring monotonicity
    - `tests/memory/test_hybrid_scoring.py` — hypothesis generates memory rows with varying sim/recency/pinned; assert: when sim increases and others fixed, score is monotone non-decreasing; when pinned flips false→true, score increases by 0.2 ± ε.
    - Requirements: R-2.8.
  - [x] 8.6 IT: warm_recall end-to-end
    - `tests/memory/test_warm_recall_integration.py` — real PG + Redis; insert 50 memories with embeddings, query, assert top-k order matches expected.
    - Requirements: R-2.8.

- [x] 9. Memory data backfill
  - [x] 9.1 `scripts/backfill_memory_hash_and_embedding.py`
    - Batch 500 rows; checkpoint to `agent_memories_active_idx` via last_id cursor in Redis `backfill:memory:cursor`.
    - Resumable: re-run picks up from cursor.
    - Dry-run mode `--dry-run`.
    - Requirements: R-9.2.
  - [x] 9.2 Backfill smoke test
    - `tests/scripts/test_backfill_memory.py` — insert 10 rows without hash/embedding, run script, assert both filled.
    - Requirements: R-9.2.

## Phase E — Consolidation Worker + Sleep Scheduler (5d)

- [x] 10. ConsolidationWorker
  - [x] 10.1 Implement worker task
    - `server/src/workers/tasks/memory_consolidation.py` — `@celery.task` `run_consolidation(session_id)`.
    - Acquire `RedisLock("lock:consolidate:{sid}", ttl=5min)`; skip if already held.
    - Load pending turns since `last_consolidation_at`; load baseline memories (limit 50).
    - Call DIFF_EXTRACTION_PROMPT (see design § prompt); validate JSON schema.
    - Dedupe by content_hash; batch embed via `EmbeddingService.embed`; insert with `ON CONFLICT (content_hash) DO NOTHING`.
    - Archive superseded rows (set `is_archived=true`, `superseded_by=<new_id>`).
    - Rebuild HOT cache; bump `sessions.hot_memory_version`.
    - Requirements: R-2.2, R-2.3, R-2.7, R-2.14.
  - [x] 10.2 PII sanitizer
    - `server/src/services/memory/pii.py` — regex scan (email, IPv4/IPv6, PAT-like token); team-scope rows with hits get downgraded to personal.
    - Requirements: R-8.1.
  - [x] 10.3 Flip sync_turn to async emit
    - `server/src/services/memory_provider.py` — `DatabaseMemoryProvider.sync_turn` checks `consolidation_worker_enabled` flag; if true, only emits TrajectoryEvent to sink and bumps Redis pending counter; does NOT run LLM extraction.
    - Legacy path retained behind `memory_legacy_sync` flag.
    - Requirements: R-2.1, R-9.3.
  - [x] 10.4 PBT: P-Memory-1 no information loss
    - `tests/workers/test_consolidation_no_info_loss.py` — hypothesis generates turn batches; run consolidation with stubbed LLM (rule-based fake); for each sampled fact, assert it appears in new memories, baseline, or `ignored` list.
    - Requirements: R-2.3.
  - [x] 10.5 PBT: P-Memory-2 supersede monotonicity
    - `tests/workers/test_consolidation_supersede.py` — assert `is_archived=true ⟹ superseded_by is not null`; `superseded_by.is_archived=false`.
    - Requirements: R-2.3, design.md P-Memory-2.
  - [x] 10.6 PBT: P-Memory-3 HOT version consistency
    - After consolidation completes, assert `Redis["session:{sid}:hot_mem"].version == DB.sessions.hot_memory_version`.
    - Requirements: R-2.7.
  - [x] 10.7 PBT: P-Sleep-3 single-session concurrency
    - Fire 10 concurrent consolidation tasks for same session; assert exactly 1 runs (lock), others return `Skipped`.
    - Requirements: R-2.14.

- [x] 11. SleepScheduler
  - [x] 11.1 Implement scheduler
    - `server/src/services/sleep_scheduler.py` — single asyncio loop, 5s tick, `ZRANGEBYSCORE sleep:queue 0 now`; for each session, `run_consolidation.delay(sid)`.
    - Token bucket: max 4 concurrent consolidations globally.
    - Daily token budget per space: read from `runtime_feature_flags.data`; skip non-critical work if exceeded.
    - Requirements: R-2.10.
  - [x] 11.2 Backpressure degradation
    - On `ZCARD sleep:queue > 500`: switch to summary-only mode (skip embedding step in ConsolidationWorker); emit `consolidation_degraded_total`.
    - Requirements: R-2.11.
  - [x] 11.3 Replace sleep_detector
    - `server/src/services/sleep_detector.py` — gate behind flag `sleep_scheduler_v2_enabled`; when true, `start_sleep_detector()` becomes no-op and SleepScheduler runs instead.
    - Requirements: R-2.10.
  - [x] 11.4 PBT: P-Sleep-1 non-blocking
    - `tests/bench/test_sleep_non_blocking.py` — burst 100 `/chat` requests while 4 consolidations running; assert p95 latency ≤ baseline * 1.2.
    - Marked as `@pytest.mark.benchmark` + `@pytest.mark.property`.
    - Requirements: R-2.9.
  - [x] 11.5 PBT: P-Sleep-2 daily token budget
    - Inject fake LLM with known token cost; run N consolidations; assert total token consumption ≤ budget.
    - Requirements: R-2.10.

## Phase F — Wiki Compiler Worker (3d)

- [x] 12. WikiCompilerWorker
  - [x] 12.1 Implement worker
    - `server/src/workers/tasks/wiki_compile.py` — `@celery.task` `compile_wiki(raw_path)`.
    - Idempotent: check `raw_file_sha256` against `wiki_compile_log`; skip if unchanged.
    - Diff mode: if wiki page exists, call LLM with (old_wiki_text, diff_of_raw) instead of full raw.
    - Add `precomputed_summary` to frontmatter (<= 300 chars).
    - Requirements: R-2.12, R-2.13.
  - [x] 12.2 Create `wiki_compile_log` lightweight table
    - `raw_path TEXT PK, raw_sha256 VARCHAR(64), last_compiled_at TIMESTAMPTZ, wiki_path TEXT`.
    - Alembic revision `add_wiki_compile_log`.
    - Requirements: R-2.12.
  - [x] 12.3 Replace kb_monitor invocation
    - `server/src/services/kb_monitor.py` — on file change, `compile_wiki.delay(path)` instead of in-process call.
    - Behind flag `wiki_compile_worker_enabled`.
    - Requirements: R-2.12.
  - [x] 12.4 Precompute script for existing wiki pages
    - `scripts/wiki_precompute_summaries.py` — iterate `data/knowledge/wiki/**/*.md`, LLM-generate summary, write frontmatter.
    - Idempotent: skip pages that already have `precomputed_summary`.
    - Requirements: R-9.5.
  - [x] 12.5 PBT: compile idempotency
    - Same sha twice → second call is no-op; LLM invocation count = 1.
    - Requirements: R-2.12.

## Phase G — RuntimeGateway + RouterLLM + ExecutorAgent (5d)

- [x] 13. RouterLLM
  - [x] 13.1 Define `RouterDecision` + `RouterDecisionTool`
    - `server/src/services/agent_runtime/router_schema.py` — pydantic `RouterDecision(route, direct_answer?, subagent_name?, suggested_tools, reason, confidence)`.
    - `RouterDecisionTool` as `StructuredTool` for function calling.
    - Requirements: R-10.1.
  - [x] 13.2 Implement `RouterLLM.classify`
    - `server/src/services/agent_runtime/router.py` — implements 3-tier path: function calling → JSON mode → fallback executor (per design.md algorithm).
    - Timeout 500ms, metrics `router_path_total{path}` and `router_timeout_total`.
    - Cache via Redis `router:decision:{hash}` TTL 30s.
    - Confidence promotion rule for ops keywords (执行/查询/分析/故障/告警).
    - Requirements: R-1.3, R-1.9, R-10.2, R-10.4, R-10.5, R-10.6.
  - [x] 13.3 PBT: P-Router-1 idempotency
    - Same message within 30s → same decision.route + suggested_tools (cache hit).
    - Requirements: R-10.5.
  - [x] 13.4 PBT: P-Router-2 degradation safety
    - On simulated timeout / parse error / exception → always `executor` with full tool set.
    - Requirements: R-1.3, R-10.4.
  - [x] 13.5 PBT: P-Router-3 direct route no tools
    - When `route=='direct'`, assert no ExecutorAgent construction + no ToolDispatcher call.
    - Requirements: R-1.4.

- [x] 14. RuntimeGateway
  - [x] 14.1 `RuntimeGateway.handle`
    - `server/src/services/agent_runtime/gateway.py` — orchestrates: prefetch (parallel 3 paths), router, branch (direct/subagent/executor), SSE relay, post-turn tasks.
    - Pseudocode from design.md § HandleChatStream.
    - Requirements: R-1.1, R-1.2, R-1.4, R-1.5.
  - [x] 14.2 Port `/chat/stream` to Gateway
    - `server/src/api/execution/router.py` — replace inline orchestration with `await runtime_gateway.handle(ctx, message)`.
    - Behind flag `gateway_enabled` (default true for new path, legacy path retained for 1 release).
    - Keep `/chat` (non-streaming) using same gateway, different SSE→JSON adapter.
    - Requirements: R-1.1.
  - [x] 14.3 IT: end-to-end chat gateway
    - `tests/agent_runtime/test_gateway_e2e.py` — real PG/Redis/Kafka; send user messages covering greeting / question / ops query; assert expected routes and SSE events.
    - Requirements: R-1.1 – R-1.5.

- [x] 15. ExecutorAgentPool
  - [x] 15.1 LRU build cache
    - `server/src/services/agent_runtime/executor_pool.py` — `build_for(tools_subset, subagents_subset)` with `functools.lru_cache`-backed graph cache (size 32, key = frozenset pair).
    - Essential tools always included: `read_file, write_file, execute`.
    - Dynamic system prompt via `_infer_caps` template render.
    - Requirements: R-1.6.
  - [x] 15.2 Fall back to legacy full agent
    - If `tools_subset is None`, route to existing `get_deep_agent()`.
    - Requirements: R-1.9.
  - [x] 15.3 PBT: cache hit semantics
    - Permutation of same subset → same graph ref; order-invariant.
    - Requirements: R-1.6.

## Phase H — Tool Dispatcher (4d)

- [x] 16. ToolDispatcher
  - [x] 16.1 Safety field on Tool
    - Add `safety VARCHAR(16) NOT NULL DEFAULT 'sequential'` to `tools` table via Alembic.
    - Values: `parallel-safe | sequential | destructive`.
    - Seed existing builtins per design.md § ToolDispatcher table.
    - Requirements: R-1.7.
  - [x] 16.2 Implement dispatcher
    - `server/src/services/agent_runtime/dispatcher.py` — `dispatch_batch(calls) -> list[Result]` per design pseudocode.
    - Result cache: Redis `tool:result:{name}:{sha256(args)}` TTL 60s; only for parallel-safe calls.
    - Destructive: invoke `interrupt_manager.request_approval`; on reject → `Rejected` result.
    - Emit `tool.{name}` OTel span, `agent_turn_latency_ms{stage="tool"}` histogram.
    - Requirements: R-1.7, R-1.8, R-8.3.
  - [x] 16.3 Wire dispatcher into ExecutorAgent path
    - ExecutorAgent tool calls route through dispatcher (override LangChain `ToolNode` behavior via wrapper).
    - Requirements: R-1.7.
  - [x] 16.4 PBT: P-Dispatcher-1 parallel order-invariant
    - Shuffle input of N parallel-safe calls; result set equal.
    - Requirements: R-1.7.
  - [x] 16.5 PBT: P-Dispatcher-2 destructive-needs-approval
    - Without approval, destructive tool not invoked; returns Rejected.
    - Requirements: R-1.7, R-8.3.
  - [x] 16.6 PBT: P-Dispatcher-3 no-state-cross-talk
    - Concurrent calls to stateless parallel-safe tool produce deterministic results.
    - Requirements: R-1.7.
  - [x] 16.7 Benchmark: p95 ≤ 1s
    - `tests/bench/test_chat_latency.py` — 50 concurrent users × 100 turns mixed workload; assert p95 first token ≤ 1000ms, p99 ≤ 2000ms; verbose mode prints breakdown.
    - Requirements: R-1.5.

## Phase I — Ops Eval Set v1 (3d)

- [x] 17. Eval set infrastructure
  - [x] 17.1 Cold-start seed extraction
    - `scripts/eval_cold_start.py` — per-scenario tag filter on `agent_trajectories` with `outcome=ok AND score>=0.8`, sample 24 per scenario; dedupe; write to `eval_set_items` (set_name + version='v1').
    - Requirements: R-4.3, R-4.4.
  - [x] 17.2 SME annotation CLI
    - `scripts/eval_annotate.py` — prompt-driven CLI to add items: input prompt text, expected_tools, expected_outcome, grading_rubric, weight, tags.
    - Target 16 items per scenario (5 scenarios × 16 = 80 items).
    - Requirements: R-4.2, R-4.3.
  - [x] 17.3 Eval set seed data (per scenario)
    - Each a JSONL file under `data/eval_sets/v1/{set_name}.jsonl` with 40 items total.
    - Commit 5 scenario files: `knowledge_mgmt_v1`, `fault_triage_v1`, `incident_coord_v1`, `capacity_mgmt_v1`, `runbook_mgmt_v1`.
    - Initial 40% human-annotated templates (8 positive + 8 negative per scenario); remaining 60% fillable via cold-start script post-deployment.
    - Requirements: R-4.1, R-4.2, R-4.3, R-4.8.
  - [x] 17.4 `EvaluationRunner` CLI
    - `scripts/eval_run.py` — args: `--set NAME --baseline | --candidate <id>`; outputs per-item + per-rubric + weighted-mean scores as JSON + table.
    - Requirements: R-4.5.
  - [x] 17.5 Unit tests for scoring aggregator
    - `tests/evaluation/test_scoring.py` — weighted mean, per-rubric breakdown, edge cases (empty set, single-item set).
    - Requirements: R-4.5.

## Phase J — Reflector + Candidate Store (6d)

- [x] 18. `SubAgentPromptRegistry` + `PromptVersionRepository`
  - [x] 18.1 Repository
    - `server/src/services/prompt_versions/repository.py` — CRUD on `sub_agent_prompt_versions`: `list_live()`, `get_by_id()`, `get_active(name)`, `get_previous_active(name, before_id)`, `get_by_candidate(candidate_id)`.
    - Requirements: R-3.15 backing, R-3.20.
  - [x] 18.2 Registry
    - `server/src/services/evolution/prompt_registry.py` — class + singleton accessor `get_prompt_registry()` as per design.
    - Methods: `load()`, `get_active(name)`, `get_shadow(name)`, `get_ab(name)`, `get_by_id(id)`, `apply_promotion(event)`, `refresh()`.
    - Reads defaults from `_DEFAULT_SUBAGENT_PROMPTS` in `deep_agent.py`.
    - Requirements: R-3.15, R-3.16, R-3.20.
  - [x] 18.3 PBT: P-HotReload-1 atomic swap
    - Fire promote events while 100 concurrent `get_active` reads; assert every read returns either `prev` or `new`, never mid-state garbage.
    - Requirements: R-3.15, R-3.16.
  - [x] 18.4 PBT: P-HotReload-4 idempotency
    - Repeated delivery of same promotion event → same final state as single delivery.
    - Requirements: R-3.18.

- [x] 19. `DynamicSystemPromptMiddleware` + CompiledSubAgent shim
  - [x] 19.1 Implement middleware
    - `server/src/agent/runtime/dynamic_prompt_middleware.py` — as per design.md; `wrap_model_call` + `awrap_model_call`; sentinel handling; suffix preservation; metadata tagging.
    - Requirements: R-3.21, R-3.22, R-3.24, R-3.25.
  - [x] 19.2 `build_dynamic_subagent` factory
    - `server/src/agent/runtime/compiled_subagent_factory.py` — mirrors DeepAgents middleware stack with DynamicSystemPromptMiddleware at position 0.
    - Uses `create_agent` with sentinel string.
    - Requirements: R-3.21.
  - [x] 19.3 Refactor `SUBAGENTS` in `deep_agent.py`
    - Replace static `SubAgent` dict list with dynamic builder: `await _build_subagents(model, backend, registry, tools_map)`.
    - `_DEFAULT_SUBAGENT_PROMPTS` dict kept as registry cold-start defaults.
    - Requirements: R-3.21, R-3.20.
  - [x] 19.4 PBT: P-HotReload-6 sentinel always replaced
    - Capture every LLM call via test hook; assert `request.system_message.text` never starts with `_SENTINEL_PROMPT`.
    - Requirements: R-3.22, R-3.25.
  - [x] 19.5 PBT: P-HotReload-7 suffix preserved
    - Insert probe middleware after DynamicSystemPromptMiddleware that appends `"::SUFFIX"` to system_message; assert final text = `registry.prompt + "::SUFFIX"` exactly.
    - Requirements: R-3.25.
  - [x] 19.6 PBT: P-HotReload-8 metadata annotation
    - Every subagent LLM call annotates `request.metadata` with `sub_agent_name / prompt_version_id`; captured in trajectory.
    - Requirements: R-3.24.

- [x] 20. `PromptReloader`
  - [x] 20.1 Implement Kafka consumer
    - `server/src/services/evolution/prompt_reloader.py` — per design; per-instance consumer group `prompt-reloader-{instance_id}`, `auto_offset_reset=latest`, handles only `kind=prompt_patch` events.
    - Requirements: R-3.15, R-3.17.
  - [x] 20.2 Instance id generation
    - `server/src/core/instance.py` — `instance_id = uuid7()` on startup; stored in memory only.
    - TTL cleanup of empty consumer groups: background task every 1h, deletes groups with no members and no offset.
    - Requirements: R-3.17.
  - [x] 20.3 Start/stop lifecycle
    - Wire start/stop into FastAPI `startup` / `shutdown` hooks in `main_execution.py`.
    - Requirements: R-3.17.
  - [x] 20.4 IT: multi-instance convergence (P-HotReload-3)
    - `tests/evolution/test_multi_instance_convergence.py` — spawn 3 uvicorn subprocesses; promote prompt; assert all 3 `get_active` return new version_id within 5s.
    - Uses `tests/conftest.py` fixture `multi_instance_cluster`.
    - Requirements: R-3.15, R-3.17.

- [x] 21. ReflectionWorker
  - [x] 21.1 Failure clustering
    - `server/src/workers/tasks/reflection.py` — `@celery.task` `run_reflection_cycle()`.
    - Pull `outcome in ('error','timeout')` + sessions with `count>=3` failures in 24h.
    - LLM cluster → named cluster groups with example trajectory ids.
    - Requirements: R-3.1.
  - [x] 21.2 Candidate generation (3 kinds)
    - Per cluster, call CANDIDATE_GEN_PROMPT; parse into `{kind: skill|prompt_patch|tool_config, name, data, expected_improvement}`.
    - Validate schema; dedupe vs existing active+shadow candidates.
    - Requirements: R-3.1, R-3.2, R-3.3.
  - [x] 21.3 Guards on prompt_patch
    - Length delta > 50% → reject with reason.
    - Regex scan for forbidden fragments ("ignore prior instructions", etc.) → reject, increment `evolution_unsafe_prompt_total`.
    - Requirements: R-3.11, R-3.12.
  - [x] 21.4 `SkillCandidateStore`
    - `server/src/services/evolution/candidate_store.py` — writes row to `skill_candidates` (status=proposed) + materialized content:
      - skill → `data/skills/.candidate/<name>/SKILL.md`
      - prompt_patch → `sub_agent_prompt_versions` row (status=proposed)
      - tool_config → `skill_candidates.data.tool_config_patch` JSON
    - Tool_config stores pre-patch snapshot for rollback.
    - Requirements: R-3.2, R-3.3, R-3.13.
  - [x] 21.5 `.candidate/` exclusion
    - Update `tool_manager.skill_scan` to skip `.candidate/` subdirectories.
    - Add `data/skills/.candidate/` to `.gitignore`.
    - Requirements: R-3.14.
  - [x] 21.6 Refactor `SkillReviewAgent` to propose-only
    - `server/src/agent/sub_agents/skill_review_agent.py` — writes to `skill_candidates(status=proposed, proposal_source='skill_review_agent')` instead of directly calling `create_skill`.
    - Requirements: R-3.10.
  - [x] 21.7 IT: dry-run reflection
    - Inject 10 error trajectories; trigger `run_reflection_cycle`; assert ≥1 candidate created with `status=proposed` and files exist at expected paths.
    - Requirements: R-3.1, R-3.3.

## Phase K — Evaluator (4d)

- [x] 22. Evaluator worker
  - [x] 22.1 Implement `evaluate(candidate_id, eval_set_name)`
    - `server/src/workers/tasks/evaluator.py` — `@celery.task evaluate(candidate_id, eval_set_name)`.
    - Load candidate + items; parallel baseline vs candidate runs (deterministic: `temperature=0`, fixed seed).
    - LLM-as-judge per item with fixed grading prompt (attached to `grading_rubric`).
    - Weighted mean scoring; check regressions (per-item delta > 0.05 → log); safety check (PII scan on outputs).
    - Insert `skill_evaluations` row; update candidate status (`shadow` if passed, `rejected` else).
    - Requirements: R-3.5, R-3.6, R-4.5.
  - [x] 22.2 Grading prompt harness
    - `server/src/services/evolution/grading.py` — `grade(run, item) -> (score, per_rubric)`; temperature=0, seed=42.
    - Cache grading results by `(run_sha, item_id, active_version)`; TTL 24h.
    - Requirements: design.md § Evaluator; R-3.6.
  - [x] 22.3 Baseline run fixtures
    - Cache baseline runs per item for 24h to avoid re-burning tokens: Redis `eval:baseline:{set_name}:{item_id}:{active_version_hash}`.
    - Requirements: cost control; design.md § Risks.
  - [x] 22.4 PBT: P-Evolve-1 state machine monotonicity
    - `tests/evolution/test_state_machine.py` — exhaustively test all allowed transitions + reject reverse moves.
    - Requirements: R-3.4.
  - [x] 22.5 PBT: P-Evolve-2 no score regression on promotion
    - `tests/evolution/test_no_score_regression.py` — given shadow→ab or ab→active transition, latest `candidate_score >= baseline_score - 0.02`.
    - Requirements: R-3.6.

## Phase L — Promoter + Rollback (5d)

- [x] 23. Promoter
  - [x] 23.1 `Promoter.step(candidate_id)`
    - `server/src/services/evolution/promoter.py` — handles `shadow` and `ab` phase transitions per design pseudocode.
    - Shadow: collect 500 samples or 24h; promote to `ab` if metrics pass.
    - AB: 10% rollout_percent (stable hash by user_id); 2000 samples or 7d; promote to `active` if win rate ≥ 0.55 and error_rate_delta ≤ 0.005.
    - Activation for skill: move `.candidate/<name>/` → `data/skills/<name>/`; call `tool_manager.invalidate_cache()`.
    - Activation for prompt_patch: transactional DB update + emit `ops.agent.promotion`.
    - Activation for tool_config: DB JSON merge on `tools.config` + `tool_manager.invalidate_cache()`.
    - Requirements: R-3.2, R-3.7, R-3.8.
  - [x] 23.2 Shadow runner
    - `server/src/services/evolution/shadow_runner.py` — async worker that replays shadow candidates against production traffic; writes comparison stats to `shadow_stats` table (new lightweight table, no strict DDL needed — JSONB column on `skill_evaluations`).
    - Does NOT affect user-visible responses (assertion-tested).
    - Requirements: R-3.7, P-Evolve-4.
  - [x] 23.3 `Promoter.rollback(name)`
    - `rollback(skill_name)` and `rollback_prompt(sub_agent_name)` implementations; single transaction + Kafka event.
    - Restores previous `active` version; sets current to `retired`.
    - Requirements: R-3.9, R-3.19.
  - [x] 23.4 Admin API for evolution
    - `server/src/api/control/evolution.py` — endpoints:
      - `GET /api/control/sub-agents/{name}/prompt-versions`
      - `POST /api/control/sub-agents/{name}/rollback`
      - `POST /api/control/sub-agents/{name}/prompt-versions/{id}/activate`
      - `GET /api/control/sub-agents/{name}/prompt-versions/{id}/diff`
      - `GET /api/control/candidates` (list by status)
      - `POST /api/control/candidates/{id}/promote` (admin override)
      - `POST /api/control/candidates/{id}/reject`
    - All require `require_admin` + audit log.
    - Requirements: R-3.8, R-3.9, R-8.4.
  - [x] 23.5 CLI tool
    - `scripts/evo_ctl.py` — `list-versions`, `rollback`, `diff`, `activate`, `force-reload`.
    - Thin wrapper over control API.
    - Requirements: design.md § API / CLI.
  - [x] 23.6 PBT: P-Evolve-3 rollback restores prev
    - `tests/evolution/test_rollback.py` — after rollback, active version_id == previous active; retired_at set on old version.
    - Requirements: R-3.9.
  - [x] 23.7 PBT: P-Evolve-4 shadow doesn't affect users
    - `tests/evolution/test_shadow_user_invisible.py` — shadow run executes; user response byte-equal to baseline response.
    - Requirements: R-3.7.
  - [x] 23.8 PBT: P-HotReload-2 rollback duality
    - Promote then rollback; assert `registry.get_active(s).id == previously_active.id` within 5s.
    - Requirements: R-3.19.
  - [x] 23.9 PBT: P-HotReload-5 no interruption under reload churn
    - Benchmark: 100 QPS `/chat/stream` for 60s + 20 promote/rollback operations interleaved; assert no 5xx, p95 ≤ baseline * 1.1.
    - Requirements: R-3.16.

## Phase M — Default-on + Cleanup (3d)

- [x] 24. Default-on rollout
  - [x] 24.1 Flip default flags
    - Set `memory_embeddings_enabled`, `consolidation_worker_enabled`, `wiki_compile_worker_enabled`, `gateway_enabled`, `router_llm_enabled` (10% rollout) as default-on in `feature_flags_bootstrap.py`.
    - Requirements: Phase M DoD.
  - [x] 24.2 Increase router_llm rollout to 100%
    - After 7d of no regression in `tests/bench/test_chat_latency.py`, bump rollout to 100%.
    - Requirements: Phase M DoD, R-7.3.
  - [x] 24.3 Benchmark full-stack
    - `tests/bench/test_full_stack_regression.py` — baseline vs current; assert p50/p95/p99 targets met (R-1.5); memory recall hit ratio ≥ 60%; embedding cache hit ratio ≥ 60%.
    - Requirements: R-1.5, Phase M DoD.

- [x] 25. Cleanup legacy paths
  - [x] 25.1 Remove `sleep_detector.py`
    - Delete `server/src/services/sleep_detector.py` + references in `main.py` startup.
    - Requirements: Phase M DoD.
  - [x] 25.2 Remove in-request LLM extraction in `DatabaseMemoryProvider.sync_turn`
    - Delete the legacy path; remove `memory_legacy_sync` flag row.
    - Requirements: R-9.3, Phase M DoD.
  - [x] 25.3 Retire `chat:msgs:*` cache key
    - Remove writes; delete key scan + cleanup script.
    - Requirements: Phase M DoD.
  - [x] 25.4 Update operator runbook
    - `docs/admin-guide/evolution-runbook.md` — how to inspect lag, rollback skill, extend worker concurrency, interpret `sleep_queue_depth`, `skill_candidate_count`.
    - Requirements: Phase M DoD.
  - [x] 25.5 Release notes
    - `docs/RELEASE_NOTES_agent_runtime_optimization_evolution_v1.md` — user-facing changes, new control APIs, migration steps, rollback plan.
    - Requirements: Phase M DoD.

## Phase N — Scenario Acceptance Gates (2d)

- [x] 26. Per-scenario baseline certification
  - [x] 26.1 Run full eval harness
    - Execute `scripts/eval_run.py --set * --baseline` for all 5 sets; capture scores.
    - Assert baseline meets targets from R-11.1 ~ R-11.5:
      - knowledge_mgmt_v1 ≥ 6.5/10
      - fault_triage_v1 ≥ 6.0/10
      - incident_coord_v1 ≥ 6.0/10
      - capacity_mgmt_v1 ≥ 5.5/10
      - runbook_mgmt_v1 ≥ 6.0/10
    - If any fails: file an investigation task (not a blocker to merge this spec; document gap in release notes).
    - Requirements: R-11.1 – R-11.5.
  - [x] 26.2* Regression guard for future candidates
    - CI job `bench-ops-scenarios.yml` — runs nightly; fails build if per-scenario score drops > 0.2 vs recorded baseline (R-11.6).
    - Requirements: R-11.6.

---

## Cross-cutting / Always-On Tasks

- [x] 27. Secrets & PII
  - [x] 27.1 PII sanitizer tests
    - `tests/security/test_pii_sanitizer.py` — email, IPv4/IPv6, PAT-like tokens detection; downgrade team→personal.
    - Requirements: R-8.1.
  - [x] 27.2 Trajectory redaction tests
    - `tests/security/test_trajectory_redaction.py` — `api_key` / `password` / `token` fields in tool args hashed before storage.
    - Requirements: R-8.2.

- [x] 28. Correctness property registry
  - [x] 28.1 Central PBT registry
    - `tests/property_tests/conftest.py` — shared fixtures: fake LLM, fake Kafka producer/consumer, in-memory registry.
    - `tests/property_tests/README.md` — maps each P-* id to test file for traceability.
    - Requirements: design.md § Correctness Properties.

- [x] 29. Dependencies & Docker
  - [x] 29.1 Update `server/pyproject.toml`
    - Bump deps listed in tasks 2.1, 4.1, 4.6.
    - Requirements: design.md § Dependencies.
  - [x] 29.2 Update `deploy/Dockerfile.server`
    - Ensure worker command path works; celery binary on PATH.
    - Requirements: Phase B DoD.

---

## Task Execution Order

Strict dependency order (one can parallelize within a phase):
`1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17 → 18 → 19 → 20 → 21 → 22 → 23 → 24 → 25 → 26`

Parallelization opportunities (see design.md § "并行化建议"):
- Phase D (tasks 7-9) and Phase F (task 12) can run alongside once Phase C is in.
- Phase G (tasks 13-15) and Phase H (task 16) can overlap with Phase D/E after Phase C.
- Phase I (task 17) can start once Phase A is in (eval set infra is schema-complete); SME labeling runs in parallel with Phase B–F.

## Milestone Gates

| Milestone | Completed Tasks | Gate Condition |
|-----------|-----------------|----------------|
| M1 | 1 – 6 | `/metrics` live; Kafka admin CRUD ops; flag 15s convergence tests green |
| M2 | 7 – 12 | Main path 0 sync LLM extraction; warm recall hit ratio ≥ 60% (bench) |
| M3 | 13 – 16 | p95 first token ≤ 1s (bench); router function_calling ≥ 95% (metrics) |
| M4 | 17 – 23 | Candidate → evaluator → shadow → ab → active flow end-to-end IT green; rollback works |
| M5 | 24 – 26 | All default flags on; 7d no regression; scenario gates documented |

## Notes

- Where a task depends on a feature flag, the code lands disabled; flag-flip happens in the task's own DoD or during Phase M rollout.
- PBT tests targeting LLM output use `FakeLLM` fixtures (rule-based deterministic) unless explicitly marked `@pytest.mark.live_llm`.
- Benchmarks gated behind `RUN_BENCH=1` env; CI runs them nightly, PR CI skips by default.
- All worker tasks idempotent: same `event_id` → same effect; retries safe.
- `invokeSubAgent(name="spec-task-execution")` is the correct executor for each leaf task during "Run All Tasks" mode.
