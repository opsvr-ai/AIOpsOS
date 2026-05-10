# Bugfix Requirements Document: chat-conversation-reliability-fixes

## Introduction

用户在 Web 端连续反馈了一组"新对话可靠性"问题：

- 新建对话的第一次请求失败、第二次才成功；
- 调用工具时"耗时很长"、偶尔整条 SSE 流突然中断；
- 浏览器 Console 多次出现 `500` 的 `/api/v1/notifications/unread-count`、`/api/v1/spaces`、`/api/v1/sessions`、`/api/v1/model-providers`、`/api/v1/sessions/recommendations`；
- 对话"没完成就结束了"。

对 `server/data/logs/server.log` 的排查结论：

- 上述 `500` 在后端 **access log 里都是 `200`**（见 `server.log` 启动完成行 `Application startup complete` 之后的日志）。真正的 `500` 来自 Vite dev server 在后端冷启动窗口（`10:42:46` → `10:43:17`，约 31s 不可用窗）对后端 TCP `ECONNREFUSED` 的映射。这是**配置/启动可观测性问题**，不是业务 bug，但会直接影响用户对"首轮失败"的感知。
- 用户真正持续体感到的"卡顿 / 突然结束 / 第一次失败"由三个确定的服务端 bug 触发，本 spec 以这三个为主要交付。

本文档将这些问题结构化为 4 个 bug：**B1/B2/B3 为核心运行时修复**，**B4 为前端/观测层降级改动**（本 spec 一并交付，执行顺序为 B1 → B2 → B3 → B4）。每一条都按 EARS 给出验收条件，并给出"Task 1 能否复现 bug"的 **bug-condition 判定**。

---

## Bug Analysis

### 概要

| 编号 | 概要 | 触发条件 | 主要症状 | 文件位置 | 日志证据（server.log 行号） |
|------|------|---------|---------|---------|----------------------------|
| B1 | `UnboundLocalError: UTC` 使 Phase C trajectory emit 在 Web 路径恒定失败 | 用户消息**未触发任何工具**（例如纯文本"你好"），`event_stream()` 走 `on_tool_start` 分支从未执行 | `datetime.now(UTC)` 第 1372 行抛 `UnboundLocalError`；exception 被 `logger.debug(..., exc_info=True)` 吞掉 | `server/src/api/execution/router.py` `event_stream()`（第 858 行附近定义，emit 在第 1372 行）；嵌套函数内第 1177 行 `from datetime import UTC` | 498–504 |
| B2 | 子 agent LLM 超时冒到 `astream_events`，把整条 SSE 流杀掉 | `task` 工具触发 `subagents.atask → ainvoke → _execute_model_async` 时上游（DeepSeek）TLS 连接超时 | `event_stream` 内 `async for event in _agent.astream_events(...)` 抛 `openai.APITimeoutError`；前端只看到 `error` 事件，没有 `done`，对话"突然结束" | `server/src/api/execution/router.py` 第 1062 行 `async for event in _agent.astream_events` 的循环体外层异常处理 | 289–500、913–1020（两次复现） |
| B3 | RouterLLM 对 DeepSeek 每轮浪费 2s 且把"你好"路由到 full_agent | 模型 provider 为 DeepSeek 时 `_classify_via_function_calling` 直接返回 None；`_classify_via_json_mode` 因长 system prompt + 网络波动每次必然吃满 2000ms 超时 | `router: skipping function_calling for DeepSeek model` → `json_mode timed out after 2000ms` → `fallback_executor reason=parse_error` → `route=executor confidence=0.00` → `using full_agent fallback`；首 token p95 ≥ 2s，违反 `agent-runtime-optimization-evolution` R-1.5 / R-10.3 | `server/src/services/agent_runtime/router.py` 第 362–365 行（硬跳过）；`_timeout_s = 2.0`（第 191 行） | 278–289、911–917（两次） |
| B4 | 后端冷启动窗口内前端所有请求被 Vite 代理映射为 `500 ECONNREFUSED`（前端/观测层改动） | 后端 `uvicorn` 启动 → `Application startup complete` 前约 30s 的时间窗 | 浏览器 Console 看到多条 `500`；"第一次失败、第二次才成功"的直接成因 | Vite dev proxy 配置 & 后端缺 `/readyz` | 1–190 的 startup 段 |

### Current Behavior (Defect)

**B1 — `UnboundLocalError: UTC` in `event_stream()` (纯文本对话路径)**

1.1 WHEN 一轮 `/api/v1/chat/stream` 请求的 agent 执行过程**未触发任何工具** THEN 系统 SHALL 在 `event_stream()` 中第 1372 行 `datetime.now(UTC)` 处抛出 `UnboundLocalError: cannot access local variable 'UTC' where it is not associated with a value`，根因是嵌套函数内第 1177 行的 `from datetime import UTC` 把 `UTC` 绑定为 `event_stream` 的 local，而当对话无工具调用时第 1177 行从未执行。
1.2 WHEN B1 发生 THEN 当前代码通过 `logger.debug("trajectory emit (chat_stream) failed", exc_info=True)` 把异常吞掉，因此 Phase C 的 `TrajectorySink.emit_turn` 在 web 路径下**每一轮纯文本对话都静默失败**，违反 `agent-runtime-optimization-evolution` 的 R-2.1。
1.3 WHEN 出现了 `upload_file` 式的"顶部 import UTC + 内层重复 import UTC"结构同样存在于同文件另一处（第 1596 行 `upload_session_file`）THEN 任何在其嵌套上下文里执行 `datetime.now(UTC)` 的分支都 SHALL 面临同样风险。

**B2 — 子 agent 超时把整条 SSE 流杀掉**

1.4 WHEN `task` 工具内部触发子 agent 的 LLM 调用（`subagents.atask → subagent.ainvoke → _execute_model_async → openai.resources.chat.completions.create`）并抛出 `openai.APITimeoutError`（底层为 `httpx.ConnectTimeout` / `httpcore.ConnectTimeout` / `httpx.ReadTimeout` / `asyncio.TimeoutError` 之一）THEN 该异常 SHALL 从 `_agent.astream_events` 冒泡到 `event_stream` 的最外层 `try/except Exception` 分支，被 `yield _sse_event("error", {...})` 承接后 `event_stream` 直接返回。
1.5 WHEN 1.4 发生 THEN 系统 SHALL NOT 发送 `done` 事件，SHALL NOT 发送 `sub_agent_error` / `tool_error` 事件，SHALL NOT 把错误作为 `ToolMessage` 回灌到主 agent 上下文，主 agent 也 SHALL NOT 获得基于"子任务失败"继续叙述或兜底的机会——前端观察到的现象就是"对话没完成就突然结束"。
1.6 WHEN 出现 1.4 且消息已经开始流式返回 token THEN 已落库的 assistant message 的 `delivery_status` SHALL 停留在 `pending`，`extra_metadata.execution_steps` 中对应的子 agent step 永远停在 `running`。

**B3 — RouterLLM 对 DeepSeek 的"你好"白白浪费 2s 且降级到 full_agent**

1.7 WHEN 模型 provider 为 DeepSeek（`_is_deepseek_model(llm)` 为 true）THEN `_classify_via_function_calling` SHALL 直接返回 `None`（`router.py` 第 362–365 行），无条件跳过 Tier 1。
1.8 WHEN Tier 1 被跳过 THEN 流程 SHALL 进入 `_classify_via_json_mode`；由于系统 prompt 过长（≥ 1k tokens）+ DeepSeek 网络波动 + 硬编码 `_timeout_s = 2.0s`（`DEFAULT_TIMEOUT_MS`），该调用 SHALL 几乎必然 `asyncio.TimeoutError`，被记为 `router: json_mode timed out after 2000ms`。
1.9 WHEN 1.8 发生 THEN `RouterDecision.fallback_executor("parse_error")` 被返回，`confidence=0.0`；gateway 日志立刻出现 `executor_pool returned None → falling back to full_agent`，并装载全部工具继续执行主 agent，消息"你好"即使符合 direct 意图也必然走 full_agent 全量重流程。
1.10 WHEN 任意短句输入（如"你好"、"嗯"、"ok"）被 B3 命中 THEN 首 token p95 SHALL ≥ router_timeout（2.0s）+ 主 agent 首字延迟，违反 `agent-runtime-optimization-evolution` R-1.5（p95 ≤ 1s）和 R-10.3（function_calling ≥ 95% 命中率）。

**B4 — 后端冷启动窗口前端 500**

1.11 WHILE 后端处于启动中（`uvicorn` 进程已接受 TCP 但 FastAPI 路由尚未注册完成，即 `server.log` 第 1–190 行的时间窗）AND 前端已加载 THE 浏览器请求 `/api/v1/notifications/unread-count` 等 GET 端点 THEN Vite dev proxy SHALL 把 TCP `ECONNREFUSED` 映射为 HTTP `500` 返回给浏览器，呈现为"首页一进去就红了一片"。
1.12 WHEN 1.11 发生 AND 用户不重试 THEN 用户将主观感知"第一次失败"，尽管后端实际上还没启动完成（`Application startup complete` 尚未出现）。

### Expected Behavior (Correct)

**B1 — `UnboundLocalError: UTC` 修复后**

2.1 WHEN 一轮纯文本对话（无工具调用）走到 trajectory emit 代码块 THEN 系统 SHALL 使用 `event_stream` 外层已 import 的 `UTC`（`chat_stream` 第 758 行）正确解析 `datetime.now(UTC)`，SHALL NOT 抛 `UnboundLocalError`。
2.2 WHEN 2.1 成立 AND `trajectory_enabled` feature flag 为 true THEN `TrajectorySink.emit(TrajectoryEvent(kind="turn", ...))` SHALL 被成功调用，事件 SHALL 最终落到 `agent_trajectories` 表中（或在 sink 繁忙时计入 `trajectory_emit_dropped`）。
2.3 WHEN `event_stream` 在 `on_tool_start` 分支执行时 THEN 系统 SHALL 复用 `chat_stream` 顶部已 import 的 `UTC`，SHALL NOT 在嵌套函数内再次 `from datetime import UTC`。
2.4 WHEN `upload_session_file`（第 1595 行附近）被调用 THEN 系统 SHALL 采用与 2.3 相同的规范（顶部单次 import，内部不 shadow）。

**B2 — 子 agent 超时不再杀流**

2.5 WHEN `_agent.astream_events(...)` 循环中抛出 `openai.APITimeoutError` / `httpx.ConnectTimeout` / `httpx.ReadTimeout` / `asyncio.TimeoutError` 之一 AND 该异常来源是**子 agent 的 LLM 调用（run_id 可以归因到 `task` tool 的当前调用）** THEN `event_stream` SHALL 捕获该异常，产出一个 `sub_agent_error` SSE 事件和一个对应的 `tool_error` SSE 事件，二者 payload 至少包含 `{session_id, step, name="task", error_kind, error_message_preview}`。
2.6 WHEN 2.5 成立 THEN 系统 SHALL 把错误作为 `ToolMessage(tool_call_id=..., content="sub-agent timed out: ...")` 回灌到主 agent 的消息上下文，让 `_agent.astream_events` 可以从中继续执行（主 agent SHALL 获得"子任务失败"这一事实，决定是否重试 / 向用户解释）。
2.7 WHEN 子 agent 白名单超时（2.5）且被软失败处理（2.6）后主 agent 自然结束 OR 所有 fallback 都已走完 THEN `event_stream` SHALL 走到常规的 `yield _sse_event("done", {...})` 分支闭流。WHEN 主 agent 自身发生 fatal（非白名单异常、非子 agent 归因的错误）THEN `event_stream` SHALL 先 `yield _sse_event("error", {...})`，再 `yield _sse_event("done", {"session_id": session_id, "reply": final_answer or "对话异常结束"})` 兜底闭流，SHALL NOT 以孤立的 `error` 事件直接终止流（保证前端任何情况下都能看到 `done`）。
2.8 WHEN 2.5 成立 THEN 对应 `collected_steps` 条目的 `status` SHALL 由 `running` 置为 `error`，`output` SHALL 包含截断后的错误摘要，assistant message 的 `extra_metadata.execution_steps` SHALL 持久化这一状态。

**B3 — RouterLLM 在 DeepSeek 上快速降级**

2.9 WHEN `_is_deepseek_model(llm)` 为 true AND 环境变量 `OPS_ROUTER_SKIP_FOR_DEEPSEEK` 未被显式设为 `"1"` THEN 系统 SHALL 允许 `_classify_via_function_calling` 尝试基于受限 `tool_choice`（例如 `"auto"` 配 `bind_tools([RouterDecisionTool])`）返回结构化结果；即使 DeepSeek 不支持 `{"type":"tool","name":"decide"}`，也 SHALL 以 function-calling auto 模式作为首选，失败再回落 json_mode（显式开关可回到旧行为）。
2.10 WHEN router 被调用 AND 用户消息满足 `len(message.strip()) ≤ OPS_ROUTER_HEURISTIC_MAX_LEN（默认 6） AND 不包含运维动词关键词集合` THEN 系统 SHALL 短路为 `RouterDecision(route="direct", direct_answer="...可为空触发 LLM direct...", confidence≥0.7)`，SHALL NOT 触发 LLM 调用（"你好"、"嗯"、"ok"、"hi" 等典型闲聊一律直通）；阈值通过环境变量 `OPS_ROUTER_HEURISTIC_MAX_LEN` 可覆盖（设为 `0` 即完全关闭 heuristic，回到修复前路径）。
2.11 WHEN router LLM 调用发生超时 THEN 有效超时窗口 SHALL 为 800ms（可配 `OPS_ROUTER_TIMEOUT_MS`），且超时时 SHALL 走 "heuristic direct/executor" 分支（例如：包含 R-10.6 列出的运维关键词 → executor 但保持 `suggested_tools=[]` 不强制 full_agent；否则 direct 并回落 LLM 单轮直接回答），SHALL NOT 默认回落到 `full_agent` 全量工具装配。
2.12 WHEN 2.11 中 router 被判定为 direct AND 无 `direct_answer` THEN gateway SHALL 请出**一次轻量 LLM 直接回答**（沿用现有 `gw_result.direct_answer` 分支），SHALL NOT 走 full_agent。
2.13 WHEN 环境中 provider 为 DeepSeek AND B3 修复生效 THEN "你好" 这样的消息首 token p95 SHALL ≤ 1500ms（承认网络 RTT，但优于当前 > 2000ms 的 router 超时 + full_agent 加载）。

**B4 — 后端冷启动窗口前端不再 500**

2.14 WHEN 后端启动尚未完成 AND 前端请求到达 THE 系统 SHALL 采取以下 A 或 B 其一：
- A) 后端暴露 `GET /readyz`，在所有关键子服务（DB、Redis、agent 预热）就绪前返回 503；Vite dev proxy 在探活失败时回退到静态占位 JSON 而不是 500。
- B) 前端对 5xx on startup 的业务端点采用指数退避重试（3 次，间隔 250ms/500ms/1s）+ 降级占位渲染（例如 unread-count 显示"—"）。
2.15 WHEN 2.14 的 A/B 任一落地 THEN 首次进入页面 AND 后端尚未启动完成 THE 浏览器 Console SHALL NOT 出现未处理的 5xx 红条，用户感知 SHALL 为"正在加载"。

### Unchanged Behavior (Regression Prevention)

**保持不变（B1）**

3.1 WHEN 对话**触发了至少一个工具调用** THEN 系统 SHALL CONTINUE TO 按现有顺序产出 `tool_start` / `tool_end` / `retrieve_start` / `retrieve_end` / `sub_agent_start` / `sub_agent_end` / `done` 事件序列，`collected_steps` 内容与修复前等价。
3.2 WHEN `upload_session_file` 被调用并保存文件 THEN 系统 SHALL CONTINUE TO 将 `Session.last_active_at` 设置为当前 UTC 时间，写入 `SessionFile`，并返回原有 `SessionFileOut` 结构。
3.3 WHEN `trajectory_enabled` feature flag 为 false THEN 主路径 SHALL CONTINUE TO 跳过 trajectory emit，行为与修复前一致。

**保持不变（B2）**

3.4 WHEN 子 agent 的 LLM 调用**正常成功** THEN 系统 SHALL CONTINUE TO 产出 `sub_agent_start` → `sub_agent_end` → `tool_end` 序列，`collected_steps` 中对应 step 的 `status` SHALL CONTINUE TO 被置为 `done`。
3.5 WHEN 主 agent（非子 agent）本身的 LLM 调用抛出异常 THEN 系统 SHALL CONTINUE TO 在 `event_stream` 外层 `except Exception` 分支 emit `error` 事件（B2 的修复范围仅限"归因到子 agent 的超时类异常"），但序列尾 SHALL **强制**追加一条 `done` 事件作为闭流兜底（对应 assistant message 的 `delivery_status` 置为 `failed` 以区分正常 `delivered`）；这是新增的最小闭流保证，不改变 `error` 事件本身的语义。
3.6 WHEN `request_approval` / `request_input` 等 interrupt 工具的 pending 流程触发 THEN 系统 SHALL CONTINUE TO 按原有逻辑发 `interrupt` + `done(interrupt_pending=true)` 事件。
3.7 WHEN 出现非超时类异常（`ValueError`、`KeyError`、业务自定义异常等）从子 agent 冒出 THEN 系统 SHALL CONTINUE TO 走原有 fatal error 处理（即 B2 的 catch 白名单严格限定 `APITimeoutError` / `ConnectTimeout` / `ReadTimeout` / `asyncio.TimeoutError`）。

**保持不变（B3）**

3.8 WHEN 模型 provider 为**非 DeepSeek**（OpenAI / Anthropic / vLLM OpenAI-compat 等）THEN RouterLLM SHALL CONTINUE TO 走 Tier 1 function_calling (`tool_choice={"type":"tool","name":"decide"}`) → Tier 2 json_mode → Tier 3 fallback_executor 的原有三层路径。
3.9 WHEN 用户消息包含任一运维关键词（"执行/查询/分析/故障/告警/部署/排查/重启" 或 R-10.6 列表）THEN RouterLLM SHALL CONTINUE TO 倾向于 `route=executor`，并受 `promote_if_ops_keyword` 提升规则约束；修复 SHALL NOT 让运维请求意外降级为 `direct`。
3.10 WHEN router cache 命中（`router:decision:{sha256(...)}`）THEN 系统 SHALL CONTINUE TO 直接复用缓存决策，SHALL NOT 重新走 LLM / heuristic。
3.11 WHEN `OPS_ROUTER_SKIP_FOR_DEEPSEEK=1` 显式被设置 THEN 系统 SHALL CONTINUE TO 保持当前"DeepSeek 跳过 function_calling"的行为（向后兼容出口）。

**保持不变（B4）**

3.12 WHEN 后端已完成启动（`Application startup complete` 之后）THEN 所有业务端点 SHALL CONTINUE TO 正常返回 2xx，前端 SHALL CONTINUE TO 渲染实际数据而非占位。

---

## 与 `agent-runtime-optimization-evolution` 的关系

- B1 直接影响 R-2.1 / R-6.3（trajectory 零丢失或计数），因为 UnboundLocalError 虽然被 `logger.debug` 吞掉不计入 `trajectory_emit_dropped`，属于静默丢失。
- B2 违反 R-1.5（首 token p95 ≤ 1s 在现象维度）& 用户信任（"对话无预警结束"）。
- B3 违反 R-1.2 / R-1.3 / R-1.5 / R-10.3。
- B4 是上述优化落地前的"启动可用性"前置条件，单独建议另立一个小 spec 或纳入本次作为 P2 交付。

所有需求验收同时满足 `agent-runtime-optimization-evolution/requirements.md` 中以下现有条款不回退：R-1.5、R-1.9、R-2.1、R-6.2（`router_path_total`、`router_timeout_total`、`trajectory_emit_dropped` 计数口径）、R-10.4（JSON mode 兜底）、R-10.6（ops 关键词提升）。
