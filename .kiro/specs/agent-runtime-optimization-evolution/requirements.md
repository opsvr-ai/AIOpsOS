# Requirements Document: Agent Runtime Optimization & Evolution

**Feature:** agent-runtime-optimization-evolution
**Workflow:** design-first（需求由设计反推）
**Related:** `./design.md`
**Scope target users:** 运维平台终端用户（SRE / oncall / 值班经理 / 运维专家 / 管理员）以及平台自身维护者

## Introduction

本需求文档为已完成的 `design.md` 反推出可验收的功能需求。面向的三大痛点与一个能力需求（运维域自进化）共同构成本次交付范围：

- **NFR-1 对话链路延迟**：从用户发出消息到首 token 输出的 p95 在一秒内；主路径不再做同步记忆抽取。
- **NFR-2 llm-wiki 记忆系统性能**：记忆写入异步化、读路径三层分级、嵌入缓存与差分合并；睡眠管理不阻塞请求路径。
- **NFR-3 自进化与自学习**：面向运维场景的 skill / prompt / tool-config 候选闭环（提议 → 评估 → 影子 → A/B → 晋升 → 回滚）。
- **NFR-4 基础设施硬约束**：Kafka 为强依赖并提供完整管理能力；RouterLLM 默认 function calling。

所有需求编号 `R-x.y`，优先级 P0（必做）/ P1（应做）/ P2（可选）。验收条件采用 EARS 格式（WHEN / WHILE / IF / WHERE + SHALL）。

## 运维场景对齐

本次特性面向以下五个运维场景产出端到端能力：

| 场景编号 | 场景 | 本次交付相关能力 |
|----------|------|------------------|
| OS-1 | 知识管理 | llm-wiki 差分 compile、预编译摘要、`knowledge_mgmt_v1` 评估集 |
| OS-2 | 故障定界 | `monitor` 子 agent prompt 进化、`fault_triage_v1` 评估集、并行 parallel-safe 工具 |
| OS-3 | 应急协同 | `ops` 子 agent prompt 进化、`incident_coord_v1` 评估集、destructive 工具批准 |
| OS-4 | 容量管理 | `analysis` 子 agent prompt 进化、`capacity_mgmt_v1` 评估集 |
| OS-5 | 预案管理 | 预案技能候选进化、`runbook_mgmt_v1` 评估集 |

## Requirements

### R-1 主对话链路延迟（P0，对应 NFR-1）

**User Story (用户)**：作为 SRE，当我在聊天窗口问一个故障定界问题时，我希望在 1 秒内看到智能体开始回答，这样我在应急场景下不会因为等待而焦虑。

**User Story (平台维护者)**：作为平台维护者，当我调高 router 放量比例时，我希望指标面板上的 p95 首 token 不恶化。

#### Acceptance Criteria

- R-1.1 [P0] WHEN 用户通过 `POST /chat/stream` 发送一条消息 THE `RuntimeGateway` SHALL 并行发起 `MemoryTier.hot`、`history_cache.get`、`MemoryTier.warm_recall` 三路 prefetch，且等待时间不超过其中最慢一路。
- R-1.2 [P0] WHEN `router_llm_enabled` 为 true 且 cache 命中 THE `RuntimeGateway` SHALL 跳过 RouterLLM 调用。
- R-1.3 [P0] WHEN RouterLLM 被调用 THE 系统 SHALL 优先使用 function calling（`bind_tools` + `tool_choice`）路径；WHEN function calling 不可用或失败 THE 系统 SHALL 回退到 JSON mode；WHEN JSON mode 也失败或超时 500ms THE 系统 SHALL 回退到原有 DeepAgents 全量工具路径，并记录 `router_path` 与 `router_timeout` 指标。
- R-1.4 [P0] WHEN RouterDecision.route == "direct" THE `RuntimeGateway` SHALL 不构建 ExecutorAgent 且 SHALL NOT 调用 ToolDispatcher。
- R-1.5 [P0] WHILE 生产负载（50 并发、200 QPS 混合场景）运行 THE 首 token p95 延迟 SHALL 不超过 1000ms，p99 不超过 2000ms。
- R-1.6 [P0] WHEN ExecutorAgent 以 `(tools_subset, subagents_subset)` 装配 THE 系统 SHALL 使用 LRU（大小 32）缓存编译后的 graph 且命中率（24h 统计）SHALL ≥ 70%。
- R-1.7 [P0] WHEN ToolDispatcher 接收到一批工具调用 THE 系统 SHALL 按 safety 分类：`parallel-safe` 并行执行、`sequential` 串行执行、`destructive` 在未获取用户批准前 SHALL NOT 执行。
- R-1.8 [P1] WHEN `parallel-safe` 工具的 args canonical hash 在 Redis `tool:result:*` 缓存中命中 THE 系统 SHALL 直接返回缓存结果，TTL 默认 60 秒。
- R-1.9 [P0] IF RouterLLM 返回 `confidence < 0.4` THEN 系统 SHALL 将 route 提升为 `executor` 并装载全部工具（兜底）。
- R-1.10 [P0] WHEN `router_llm_enabled` 灰度放量时 THE 系统 SHALL 支持按 `user_id` 稳定 hash 的 `rollout_percent` 分流且命中率误差 ≤ 5%。

### R-2 llm-wiki 记忆系统性能（P0，对应 NFR-2）

#### Acceptance Criteria

- R-2.1 [P0] WHEN 用户完成一轮对话 THE 系统 SHALL 通过 `TrajectorySink.emit_turn` 发送事件到 Kafka `ops.agent.trajectory`，且主路径 SHALL NOT 执行任何同步 LLM 抽取。
- R-2.2 [P0] WHEN Trajectory 事件被 `ConsolidationWorker` 消费 THE 系统 SHALL 对同一 session 加 Redis 分布式锁 `lock:consolidate:{sid}` 确保并发互斥。
- R-2.3 [P0] WHEN ConsolidationWorker 运行 THE 系统 SHALL 基于现有 baseline 记忆进行差分合并（新增 / 归档 / 忽略三分类），被 `supersedes` 标记的记忆 SHALL 设置 `is_archived=TRUE` 与 `superseded_by` 指针。
- R-2.4 [P0] WHEN 新记忆被写入 THE `EmbeddingService` SHALL 以批量窗口（最多 30ms 或 16 条）聚合调用，且 SHALL 按 `sha256(content)` 在 Redis `emb:{model}:*` 缓存 7 天。
- R-2.5 [P0] IF `settings.embedding_api_key` 为空 THEN `warm_recall` SHALL 退化为 ILIKE 查询并返回合法结果，且 SHALL NOT 抛出异常。
- R-2.6 [P0] WHEN `MemoryTier.hot` 被调用 THE 系统 SHALL 优先读 Redis `session:{sid}:hot_mem`；未命中时 SHALL 从 DB 构建并写入缓存，TTL 10 分钟。
- R-2.7 [P0] WHEN ConsolidationWorker 完成一次合并 THE `sessions.hot_memory_version` SHALL 严格 +1 且 Redis hot_mem 版本 SHALL 与之一致。
- R-2.8 [P0] WHEN warm_recall 返回结果 THE 系统 SHALL 按 `0.5 * semantic_sim + 0.3 * recency + 0.2 * pinned` 的混合打分排序。
- R-2.9 [P0] WHILE `ConsolidationWorker` 并发运行 ≥ 4 个长任务（≥ 2s/任务） AND 同时有 `/chat` 请求 100 并发 THE `/chat` 的 p95 延迟 SHALL 不超过 baseline_p95 * 1.2。
- R-2.10 [P0] `SleepScheduler` 基于 Redis ZSET `sleep:queue`（score = 预期开始时间）调度，且 SHALL 受 `max_concurrent_consolidations`（默认 4）与每日每 space token 预算约束。
- R-2.11 [P1] WHEN `sleep:queue` 深度 > 500 THE 系统 SHALL 进入"summary-only 跳过嵌入"降级模式并记录 `consolidation_degraded_total` 指标。
- R-2.12 [P0] WHEN `WikiCompilerWorker` 处理同 raw 文件 THE 系统 SHALL 以 sha256 作为幂等键，sha 相同时 SHALL 直接跳过。
- R-2.13 [P1] WHEN wiki 编译完成 THE 页面 frontmatter SHALL 包含 `precomputed_summary`（≤ 300 字），`MemoryTier.cold_lookup` SHALL 直接返回该字段而不再触发 LLM。
- R-2.14 [P0] WHEN 任一 session 同时存在多条 ConsolidationWorker 任务 THE Redis 锁 SHALL 保证任一时刻只有 1 个在运行。

### R-3 自进化（P0，对应 NFR-3）

**User Story (运维专家)**：作为运维专家，当我多次纠正智能体在故障定界中的错误做法时，我希望系统能从这些纠正中形成新的技能或 prompt 修订，并在经过影子验证与 A/B 后自动生效；对比旧版没有实质改进时自动拒绝。

#### Acceptance Criteria

- R-3.1 [P0] WHEN `ReflectionWorker` 运行 THE 系统 SHALL 从 `agent_trajectories` 拉取 `outcome in ('error','timeout')` 与 `count ≥ 3 per session` 的失败聚类，并生成 candidate。
- R-3.2 [P0] Candidate 的 `kind` SHALL ∈ {`skill`, `prompt_patch`, `tool_config`}，三类 SHALL 走同一个 reflector → evaluator → promoter 状态机。
- R-3.3 [P0] WHEN candidate 被创建 THE status SHALL 为 `proposed`，且 `data/skills/` 主目录 SHALL NOT 被修改；`skill` 产物仅写 `data/skills/.candidate/<name>/`，`prompt_patch` 产物写 `sub_agent_prompt_versions` 表（status=proposed）。
- R-3.4 [P0] candidate 状态机 SHALL 仅允许以下转移：`proposed → shadow | rejected`；`shadow → ab | rejected | retired`；`ab → active | rejected | retired`；`active → retired`。任何逆向（`rejected → *`、`retired → active`）SHALL 被拒绝。
- R-3.5 [P0] WHEN `Evaluator.evaluate(candidate, eval_set)` 完成 THE 系统 SHALL 记录 `skill_evaluations` 行：`baseline_score`、`candidate_score`、`n_samples`、`passed`、`details`。
- R-3.6 [P0] WHEN `c.status` 从 `shadow → ab` 或 `ab → active` 转移 THE 最新 `skill_evaluations.candidate_score` SHALL ≥ `baseline_score - ε`（ε=0.02）。
- R-3.7 [P0] WHILE candidate 处于 `shadow` 状态 THE 用户可见的 response SHALL 等同于 baseline 路径的 response，candidate 的运行结果 SHALL 仅写入 shadow stats。
- R-3.8 [P0] WHEN candidate 晋升为 `active` THE 系统 SHALL 发布 `ops.agent.promotion` Kafka 事件并触发 `tool_manager.invalidate_cache()`，对 `prompt_patch` 同时 reload 子 agent 的 `system_prompt`。
- R-3.9 [P0] `Promoter.rollback(name)` SHALL 将当前 active 标为 `retired` 并恢复到 `skill_versions`（或 `sub_agent_prompt_versions`）表中上一个 active 条目。
- R-3.10 [P0] WHEN `SkillReviewAgent` 建议新 skill THE 系统 SHALL 写入 `skill_candidates(status=proposed, proposal_source='skill_review_agent')` 且 SHALL NOT 直接激活。
- R-3.11 [P0] `prompt_patch` candidate 的 `new_prompt` 长度相对 current 的变化率 > 50% THE 系统 SHALL 拒绝（防止大幅改写引入风险）。
- R-3.12 [P0] `prompt_patch` candidate 的 `new_prompt` 若包含禁止片段（如降低安全约束的"ignore prior instructions" 类表述）THE 系统 SHALL 拒绝并记录 `evolution_unsafe_prompt_total`。
- R-3.13 [P1] `tool_config` candidate 激活前 THE 系统 SHALL 存储 pre-patch snapshot；回滚时 SHALL 还原到该 snapshot。
- R-3.14 [P0] `.candidate/` 目录 SHALL 不会被 `tool_manager.skill_scan` 扫描到；SHALL 在 `.gitignore` 中被忽略。
- R-3.15 [P0] WHEN `prompt_patch` 晋升为 active THE 所有 FastAPI 进程 SHALL 在收到 `ops.agent.promotion` 事件后 5s 内返回新版本（`SubAgentPromptRegistry.get_active` 返回新 version_id）。
- R-3.16 [P0] WHILE prompt 正在被热切换 THE 任何已开始构造消息的请求 SHALL 仍用它启动时的版本完成，SHALL NOT 出现"一次请求内混合两版 system_prompt"。
- R-3.17 [P0] WHEN 多个实例订阅 `ops.agent.promotion` THE 每个实例 SHALL 有独立 consumer group（不共享 offset），以保证所有实例都收到事件。
- R-3.18 [P0] WHEN 同一 promotion 事件被 Kafka 重复投递 THE `SubAgentPromptRegistry.apply_promotion` SHALL 通过 `repo.get_by_id` 二次验证，终态 SHALL 等同于单次投递。
- R-3.19 [P0] `Promoter.rollback(name)` SHALL 在单次调用内完成 DB 状态变更 + Kafka 事件发布；调用返回即承诺新请求将走回滚后版本（最多 5s 收敛）。
- R-3.20 [P0] IF `SubAgentPromptRegistry` 启动时 DB 未定义某个 sub_agent THEN 该 sub_agent SHALL 回退到代码常量（`source=default`），且系统 SHALL 正常工作。
- R-3.21 [P0] `SubAgent` 在 DeepAgents LangGraph 中的 `system_prompt` SHALL 使用晚绑定（`CompiledSubAgent` + `DynamicSystemPromptMiddleware`），而不是编译期固化字符串。
- R-3.22 [P0] `DynamicSystemPromptMiddleware` SHALL 在 subagent 中间件栈的首位，SHALL 在每次 `wrap_model_call` / `awrap_model_call` 时用 `request.override(system_message=...)` 替换系统消息。
- R-3.23 [P0] Prompt 热切换 SHALL NOT 触发主 `_deep_agent` LangGraph 重建；只有工具集合变化或 `/api/control/evolution/force-reload` 才触发重建。
- R-3.24 [P0] 子 agent 的 LLM 调用 trajectory SHALL 记录 `sub_agent_name` / `prompt_version_id` / `prompt_version_no` / `prompt_source`，用于归因。
- R-3.25 [P0] WHEN 外层中间件（Skills / Summarization 等）在 sentinel prompt 之后追加了内容 X THE `DynamicSystemPromptMiddleware` 替换后最终 system_message.text SHALL 等于 `registry.prompt + X`（不得丢弃 X）。

### R-4 运维场景评估集（P0，对应 OS-1 ~ OS-5）

**User Story (运维管理员)**：作为运维管理员，当系统提议对子 agent 进行修改时，我希望能看到这次修改在 5 个运维场景上的分项打分，而不是一个含糊的总分。

#### Acceptance Criteria

- R-4.1 [P0] 系统 SHALL 具备 5 个冷启动评估集：`knowledge_mgmt_v1` / `fault_triage_v1` / `incident_coord_v1` / `capacity_mgmt_v1` / `runbook_mgmt_v1`，各 40 条样本。
- R-4.2 [P0] 每个评估集样本 SHALL 包含：`prompt` / `expected_tools` / `expected_outcome` / `grading_rubric` / `weight` / `tags`。
- R-4.3 [P0] 冷启动样本来源 SHALL 为 60% 从高质量 trajectory（`outcome=ok AND score≥0.8`）抽取 + 40% 人工标注。
- R-4.4 [P0] `scripts/eval_cold_start.py` SHALL 能按场景 tag 过滤并抽样种子样本，覆盖所有 5 个集合。
- R-4.5 [P0] `EvaluationRunner` CLI SHALL 能运行 `baseline | candidate` 双跑并输出 per-item + per-rubric + weighted-mean 三级打分。
- R-4.6 [P1] 评估集 SHALL 支持版本化（v1 / v2 / ...）；新版本发布后旧版本 SHALL 保留用于回归对比。
- R-4.7 [P1] 线上 trajectory 评分 ≥ 0.9 且覆盖缺口场景 THE 系统 SHALL 允许其进入"候选评估样本"队列（需人工批准入集）。
- R-4.8 [P0] 子 agent 与评估集的默认绑定 SHALL 按设计文档"Prompt Patch 的评估集匹配"表执行。

### R-5 Kafka 管理面（P0，对应 NFR-4）

**User Story (平台管理员)**：作为平台管理员，我需要在一个界面里完成 topic 的创建/调整、查看消费组 lag、浏览消息、重放死信与管理 schema，而不需要登录 broker 用命令行。

#### Acceptance Criteria

- R-5.1 [P0] 平台启动时 SHALL 自动 `ensure` 设计中列出的所有默认 topic（含 partitions / replication / retention / compaction / schema 绑定）。IF 任何默认 topic 无法创建或配置不一致 THEN `/readyz` SHALL 返回不通过。
- R-5.2 [P0] 提供 `POST/GET/PUT/DELETE /api/control/kafka/topics`，支持创建、描述、修改（partitions / retention / compression / cleanup.policy）、删除（带二次确认）。
- R-5.3 [P0] 提供 `GET /api/control/kafka/consumer-groups` 列出组、成员、assignments、per-partition lag；提供 `POST .../reset-offset` 支持 earliest / latest / timestamp / specific offset；重置 SHALL 要求二次确认。
- R-5.4 [P0] 提供 `GET /api/control/kafka/browser` 支持 topic + partition + offset 定位、正则搜索 key / value / header、分页读取。
- R-5.5 [P0] 提供 `GET/POST /api/control/kafka/dlq`：列出、按 tag / time 过滤、批量重放、批量丢弃、标记已处理；重放 SHALL 幂等（相同 id 重放产生相同业务效果或幂等跳过）。
- R-5.6 [P0] 提供 `GET/POST /api/control/kafka/schemas` 管理本地 JSON Schema 版本表；producer 发送前 SHALL 校验 schema，校验失败 SHALL 直接入 DLQ + 计 `kafka_schema_reject_total`。
- R-5.7 [P0] `KafkaMetricsCollector` SHALL 每 5 秒采集并暴露 lag、ISR、消费停滞时长、DLQ 增速到 Prometheus，并提供默认告警规则样板。
- R-5.8 [P0] Kafka 管理 UI（前端）SHALL 至少覆盖：topic 列表与详情、consumer group 面板、消息浏览器、DLQ 管理、schema 列表；操作二次确认 + 审计日志（写 `audit_logs` 表）。
- R-5.9 [P1] 平台 SHALL 支持 SASL/SCRAM 配置（生产）；dev 下允许 PLAINTEXT。
- R-5.10 [P0] 生产者端 TrajectorySink 的 `emit()` SHALL 非阻塞（fire-and-forget）；queue 溢出 SHALL 计数 `trajectory_emit_dropped` 而不阻塞请求。

### R-6 可观测性（P0，横切）

#### Acceptance Criteria

- R-6.1 [P0] 系统 SHALL 使用 OpenTelemetry 发射 spans：`gateway.handle`、`router_llm.classify`、`memory.hot/warm/cold`、`executor.ainvoke`、`tool.{name}`、`consolidation.run`、`reflection.run`、`evaluator.run`、`promoter.step`、`kafka.produce/consume`。
- R-6.2 [P0] Prometheus 指标 SHALL 包含：
  - `agent_turn_latency_ms{stage,route}` histogram
  - `agent_tokens_total{direction,model,stage}` counter
  - `memory_recall_hit_ratio{tier}` gauge
  - `embedding_cache_hit_ratio` gauge
  - `sleep_queue_depth` gauge
  - `consolidation_failed_total`、`consolidation_degraded_total` counter
  - `router_path_total{path}`、`router_timeout_total` counter
  - `skill_candidate_count{status}` gauge
  - `evolution_rejected_total`、`evolution_unsafe_prompt_total` counter
  - `trajectory_emit_dropped` counter
  - `kafka_lag{group,topic,partition}` gauge、`kafka_dlq_rate{topic}` gauge、`kafka_schema_reject_total` counter
- R-6.3 [P0] WHEN Trajectory 事件被 `emit()` THE 系统 SHALL 在 30s 内确保该事件 id 出现在 `agent_trajectories` 表中；若未出现 THEN `trajectory_emit_dropped` SHALL 被记账。
- R-6.4 [P0] `/metrics` 端点 SHALL 返回合法 Prometheus 文本格式。
- R-6.5 [P1] 控制面 SHALL 提供统一 dashboard（含 RED / USE 指标）。

### R-7 Feature Flag（P0，横切）

#### Acceptance Criteria

- R-7.1 [P0] 系统 SHALL 通过 `runtime_feature_flags` 表管理开关；`FeatureFlagService` SHALL 在 15s 内刷新最新值。
- R-7.2 [P0] 开关 SHALL 支持 `enabled`（布尔）+ `rollout_percent`（0..100）+ `data`（JSONB 扩展参数）。
- R-7.3 [P0] WHEN `rollout_percent=X` THE 命中率在 24h 尺度上 SHALL ≈ X% ± 5%（按 user_id 稳定 hash 分流）。
- R-7.4 [P0] 所有核心开关 SHALL 至少存在：`router_llm_enabled`、`memory_embeddings_enabled`、`consolidation_worker_enabled`、`wiki_compile_worker_enabled`、`evolution_reflector_enabled`、`evolution_shadow_enabled`、`evolution_ab_enabled`。

### R-8 安全与合规（P0，横切）

#### Acceptance Criteria

- R-8.1 [P0] `team` 记忆 consolidation 写入前 SHALL 经 `sanitize_pii()` 扫描（email / IPv4 / IPv6 / PAT token），命中 SHALL 降级为 `scope=personal`。
- R-8.2 [P0] `agent_trajectories.data` 写入前 SHALL 对 tool args 中的已知敏感字段（`api_key` / `password` / `token`）哈希占位。
- R-8.3 [P0] `destructive` 工具在 ToolDispatcher 层 SHALL 强制拦截并要求用户批准（`request_approval`）；IF 未 approve THEN SHALL 返回 `Rejected`。
- R-8.4 [P0] candidate 晋升 `ab → active` SHALL 有 `approved_by` 字段记录审批人（admin 或规则触发）。
- R-8.5 [P0] Kafka 生产环境 SHALL 默认启用 SASL/SCRAM；管理面 ACL 按 `space_id` 隔离。

### R-9 向后兼容 / 迁移（P0）

#### Acceptance Criteria

- R-9.1 [P0] 所有 Alembic 迁移 SHALL 可逆（up → down → up 等价）。
- R-9.2 [P0] 现有 `agent_memories` 行 `embedding=NULL` 在新系统下 SHALL 仍可被 ILIKE 检索；回填脚本 `scripts/backfill_memory_hash_and_embedding.py` SHALL 支持分批（500 条/批）+ checkpoint + 恢复。
- R-9.3 [P0] 旧 `DatabaseMemoryProvider.sync_turn` 行为 SHALL 由 `memory_legacy_sync` flag 控制；flag 关闭后 SHALL 无 in-request LLM 抽取。
- R-9.4 [P0] 部署时未开启任何 flag THE 系统行为 SHALL 与当前版本等价（零回归）。
- R-9.5 [P1] `wiki_precompute_summaries.py` SHALL 能一次性回填已有 wiki 页的 `precomputed_summary`。

### R-10 RouterLLM 详细行为（P0，对应 R-1）

#### Acceptance Criteria

- R-10.1 [P0] RouterDecision schema SHALL 为 `{route: "direct|executor|subagent", direct_answer?, subagent_name?, suggested_tools?, reason, confidence}`。
- R-10.2 [P0] RouterLLM 首选 function calling：`llm.bind_tools([RouterDecisionTool], tool_choice={"type":"tool","name":"decide"})`；SHALL 指标标记 `router_path=function_calling`。
- R-10.3 [P0] 生产稳定运行（上线 2 周后）`router_path=function_calling` 命中率 SHALL ≥ 95%。
- R-10.4 [P0] JSON mode 兜底 SHALL 为 `with_structured_output(RouterDecision, method='json_mode')`；失败再降级为 `route=executor + 全量工具`。
- R-10.5 [P0] Router 结果 SHALL 缓存在 Redis key `router:decision:{sha256(message+last_asst+user_id)}`，TTL 30s；命中则跳过 LLM 调用。
- R-10.6 [P0] 提升规则：IF `route==direct` 且消息包含 "执行/查询/分析/故障/告警" 等运维关键词 THEN 提升为 `executor`（避免对运维问题草率直答）。

### R-11 运维场景端到端（P1，对应 OS-1 ~ OS-5）

#### Acceptance Criteria

- R-11.1 [P1] OS-1 知识管理：在 `knowledge_mgmt_v1` 评估集上，`knowledge` 子 agent 或全量 agent 的加权平均分 SHALL ≥ 6.5/10（baseline）。
- R-11.2 [P1] OS-2 故障定界：在 `fault_triage_v1` 上加权平均分 SHALL ≥ 6.0/10。
- R-11.3 [P1] OS-3 应急协同：在 `incident_coord_v1` 上加权平均分 SHALL ≥ 6.0/10。
- R-11.4 [P1] OS-4 容量管理：在 `capacity_mgmt_v1` 上加权平均分 SHALL ≥ 5.5/10。
- R-11.5 [P1] OS-5 预案管理：在 `runbook_mgmt_v1` 上加权平均分 SHALL ≥ 6.0/10。
- R-11.6 [P1] 所有 candidate 晋升后 SHALL 不造成这 5 个集合上的 per-scenario 分数相对 baseline 下跌超过 0.2。

## 非功能性需求（汇总）

### 性能
- 首 token p95 ≤ 1s（R-1.5）；memory hot 路径 ≤ 5ms；warm_recall 整体 ≤ 80ms（含嵌入与 ANN）。
- ConsolidationWorker 单 session p95 ≤ 3s；吞吐 ≥ 30 sessions/min 单 worker。
- Kafka producer 批次发送 ≤ 10ms 平均延迟。

### 可用性
- FastAPI `/chat/stream` 可用性 SHALL ≥ 99.9%；worker 不可用 SHALL NOT 影响主路径（请求依然返回正常响应，记忆写变慢但不失败）。
- Kafka 不可用时 TrajectorySink SHALL 本地 queue 兜底（R-5.10），满时丢弃并计数。

### 可扩展性
- Celery worker SHALL 支持水平扩展（`-c N` 和多实例）。
- `agent_trajectories` 表 SHALL 支持按 `created_at` 分区（月分区）。

### 可维护性
- 所有 worker task SHALL 幂等（同 event id 重复消费产生相同效果）。
- 配置项统一走 `server/src/core/config.py`。

## 功能范围（In-Scope / Out-of-Scope）

### In-Scope
- Chat 主路径优化（gateway / router / executor / dispatcher）
- 记忆系统三层 + embedding + consolidation + sleep scheduler
- llm-wiki 差分 compile + precomputed_summary
- Kafka 管理面完整能力
- Reflector / Evaluator / Promoter 三类候选闭环
- Prompt patch 候选（子 agent prompt 进化）
- 运维场景 5 × 评估集冷启动
- Observability（OTel + Prometheus）
- Feature flag 表
- 向后兼容迁移脚本

### Out-of-Scope（下一期）
- 多租户 ACL 细化（仅保留 space_id 粒度）
- 跨集群 Kafka 复制（mirror maker 级）
- Schema Registry 与外部 Confluent 集成（本期只做内置）
- Candidate "crowd-sourced" 评分（用户投票）
- 完整的 Arize/Langsmith 等外部 LLM 观测平台集成（留接口）
- 多模型蒸馏 / fine-tuning 自动化

## 依赖与假设

- `deploy/docker-compose.dev.yml` 已提供 pgvector-pg15 / redis-7 / kafka-7.5（WSL 容器）。
- 生产环境 Kafka 集群 ≥ 3 broker。
- Embedding provider：默认复用 `model_provider` 表中 OpenAI / DeepSeek 兼容 endpoint；允许 `embedding_api_key=""` 时降级。
- LLM provider 至少 1 个支持 function calling（DeepSeek / OpenAI / Anthropic 任一）。
- 运维 SME 可用于评估集人工标注（预估 80 条样本，2 人天）。

## 验收与回归

- 每个 Phase 验收：对应 `design.md § 每个 Phase 的退出条件（DoD）`。
- 里程碑验收：M1 ~ M5（见 design.md）。
- 回归基线：`pytest tests/benchmarks` + `tests/property_tests`（对应 19 条 Correctness Properties）。
- 上线 gating：M5 Ready 后连续运行 7 天 RED 指标不恶化 + 所有默认 flag 开启。

## Correctness Properties 与 Requirements 映射

| Property | Requirements |
|----------|--------------|
| P-Router-1（幂等） | R-10.5 |
| P-Router-2（降级） | R-1.3 / R-10.4 |
| P-Router-3（direct 不调工具） | R-1.4 |
| P-Dispatcher-1（并行无序） | R-1.7 |
| P-Dispatcher-2（destructive 需批准） | R-1.7 / R-8.3 |
| P-Dispatcher-3（并发不串扰） | R-1.7 |
| P-Memory-1（无信息丢失） | R-2.3 |
| P-Memory-2（supersede 单调） | R-2.3 |
| P-Memory-3（HOT 一致性） | R-2.7 |
| P-Memory-4（embedding 幂等） | R-2.4 |
| P-Memory-5（降级无中断） | R-2.5 |
| P-Sleep-1（后台不阻塞） | R-2.9 |
| P-Sleep-2（token 配额） | R-2.10 |
| P-Sleep-3（session 互斥） | R-2.2 / R-2.14 |
| P-Evolve-1（状态机单调） | R-3.4 |
| P-Evolve-2（无分数回归） | R-3.6 |
| P-Evolve-3（回滚可行） | R-3.9 |
| P-Evolve-4（影子不影响用户） | R-3.7 |
| P-HotReload-1 ~ 5（子 agent prompt 热切换） | R-3.15 / R-3.16 / R-3.17 / R-3.18 / R-3.19 |
| P-HotReload-6（sentinel 被无条件替换） | R-3.22 |
| P-HotReload-7（suffix 保留） | R-3.25 |
| P-HotReload-8（metadata 标注） | R-3.24 |
| P-Observe-1（trajectory 零丢失或计数） | R-6.3 / R-5.10 |
| P-FF-1（15s 生效） | R-7.1 / R-7.3 |
