# Implementation Plan: chat-conversation-reliability-fixes

本 spec 覆盖 4 个 bug（B1 / B2 / B3 / B4），均纳入本次交付。B1/B2/B3 为后端运行时修复，B4 为前端/观测层降级改动。

**核心约束**：每个 bug 都必须先写一条"能在 UNFIXED 代码上失败"的 property/回归测试（Task 1-style），测试失败即 Task 1 完成；随后才能写修复；最后重新跑同一条测试，变绿即 Task N 完成。

**不要动的文件**（本 spec 范围内只产 spec，不改代码）：
- `server/src/*`
- `web/src/*`
- 其他 spec `agent-runtime-optimization-evolution` 下的文件

> Tasks 条目按执行顺序列出：**B1 → B2 → B3 → B4**。每个 bug 的第 1 条任务都是 "写一条必然在 UNFIXED 代码上失败的 exploration 测试"。

---

## B1: UnboundLocalError: UTC 在 event_stream trajectory emit 点

- [x] 1. 为 B1 写 bug condition 探索性测试（在 UNFIXED 代码上必然失败）
  - **Property 1: Bug Condition** - 纯文本对话在 trajectory emit 点触发 UnboundLocalError
  - **CRITICAL**: 此测试 MUST 在 UNFIXED 代码上失败（失败即证明 bug 真实存在）；**不要试图修代码或改测试让它通过**
  - **NOTE**: 测试内容即 Property 1 的期望行为——修复后它会验证 bug 不再出现
  - **GOAL**: 产出能被自动化重现的 counterexample
  - **Scoped PBT Approach**: 对"deterministic bug"做范围化 PBT，输入域固定为 `ChatInvocation(will_trigger_tool=false, trajectory_enabled=true, provider=任意)`；message 从 `["你好", "嗯", "ok", "hi", "早", "谢谢"]` 这 6 条短文本采样（都属于短闲聊，主 agent 必定直接回答不调工具）
  - 实现位置建议：`server/tests/property_tests/test_chat_stream_trajectory_unbound_utc.py`
  - 用 FastAPI `AsyncClient` + `TestClient` 打 `POST /api/v1/chat/stream`，把 `get_deep_agent()` patch 为一个"只产 `on_chat_model_stream` 事件、从不产 `on_tool_start`"的 stub agent，并 patch `get_trajectory_sink()` 返回一个能被断言 `emit.called` 的 fake sink
  - 断言 1：HTTP 200 + SSE 序列以 `done` 结束
  - 断言 2：fake sink.emit 被调用至少 1 次（UNFIXED 代码上不会被调用，因为 UnboundLocalError 在 emit call 参数 `ts=datetime.now(UTC)` 构造阶段就抛了）
  - 断言 3（白盒可选）：通过 caplog 抓到的 ERROR 日志中**不**出现 `UnboundLocalError: cannot access local variable 'UTC'`
  - 同时补一条显式白盒单测：构造一个 UnboundLocalError 触发场景，断言 pure Python（不依赖 HTTP 栈）——这会在 UNFIXED 代码可靠复现
  - **EXPECTED OUTCOME**: 测试 FAIL（fake sink.emit.called == False + caplog 含 `UnboundLocalError`）
  - 把观察到的 counterexample 写入 `server/tests/property_tests/counterexamples/b1_utc_unbound.md`（记录失败输入与堆栈行号对比 server.log 498–504）
  - Mark task complete when: 测试已写 + 已跑 + 失败现象已记录
  - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. 为 B1 写 preservation 属性测试（BEFORE 实现修复）
  - **Property 2: Preservation** - 有工具调用的对话路径保持原有 SSE 序列不变
  - **IMPORTANT**: 遵循"先观察 unfixed 行为，再把观察结果编码为 property"方法论
  - 实现位置建议：`server/tests/property_tests/test_chat_stream_with_tools_golden.py`
  - Observation step：对 UNFIXED 代码跑 3 条 scripted agent streams
    - 样本 A：单工具调用（`grep_kb`），成功
    - 样本 B：多工具调用（`list_wiki` 后 `read_wiki`），成功
    - 样本 C：一个 `task` 子 agent 成功返回
  - 把每条样本的 SSE 事件序列（`event:` / `data:` 对）录制为 golden 文件（`counterexamples/b1_golden_with_tools_*.jsonl`）
  - 写 property-based test：对上述 3 条输入在 UNFIXED 代码上重放，断言事件序列与 golden 等价；同时在"trajectory_enabled=false"情况下断言 trajectory sink 未被调用
  - 断言：对每条 golden，SSE 事件序列严格一致；`collected_steps` JSON 等价；`Session.last_active_at` 被更新（非 None）
  - **EXPECTED OUTCOME**: 测试 PASS on UNFIXED（确认 baseline）；修复后仍必须 PASS
  - Mark task complete when: 测试已写、已跑、在 UNFIXED 代码上通过
  - _Requirements: 3.1, 3.2, 3.3_

- [x] 3. Fix for B1 — event_stream UnboundLocalError

  - [x] 3.1 移除 event_stream 内重复 import，复用顶部已 import 的 UTC
    - 编辑 `server/src/api/execution/router.py`
    - 删除第 1177 行 `from datetime import UTC` 与第 1179 行 `from datetime import datetime as _dt`
    - 将第 1181 行 `_dt.now(UTC).timestamp()` 改为 `datetime.now(UTC).timestamp()`（复用 chat_stream 第 758 行顶部 import）
    - 在该位置补一行注释：`# UTC/datetime 已在 chat_stream 顶部 import；不要在嵌套闭包内重复 import，否则会触发 Python local 绑定陷阱 (PEP 227)`
    - 检查 `upload_session_file`（第 1595 行附近）的 `from datetime import UTC` 是否为嵌套闭包：它是顶层函数、非闭包，保留现状即可；但**加注释**约束未来不要在内部条件分支里二次 import UTC/datetime
    - _Bug_Condition: `isBugCondition_B1(inv) = (will_trigger_tool=false AND trajectory_enabled=true)` from design_
    - _Expected_Behavior: `datetime.now(UTC)` 在 emit 前正确解析；`TrajectorySink.emit` 成功调用 from design § Correctness Properties § Property 1_
    - _Preservation: 有工具调用的 SSE 序列与 collected_steps 不变 from design § Preservation Requirements_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3_

  - [x] 3.2 重新运行 Task 1 的探索性测试，验证已由 FAIL 变为 PASS
    - **Property 1: Expected Behavior** - 纯文本对话 trajectory emit 成功
    - **IMPORTANT**: 重跑 Task 1 的同一条测试 — 不要新写测试
    - Task 1 的测试本身就是 Property 1 的可执行版本；它变绿即证 bug 被修复
    - 运行 `pytest server/tests/property_tests/test_chat_stream_trajectory_unbound_utc.py -v`
    - **EXPECTED OUTCOME**: PASS（fake sink.emit.called == True 且 caplog 不含 UnboundLocalError）
    - _Requirements: Expected Behavior Properties 1 from design_

  - [x] 3.3 重新运行 Task 2 的 preservation 测试，确认无回归
    - **Property 2: Preservation** - 有工具调用路径逐字节等价
    - 运行 `pytest server/tests/property_tests/test_chat_stream_with_tools_golden.py -v`
    - **EXPECTED OUTCOME**: PASS（golden 对比全等）
    - 确认所有样本（A/B/C）SSE 序列与修复前完全一致

---

## B2: 子 agent 超时把整条 SSE 流杀掉

- [x] 4. 为 B2 写 bug condition 探索性测试（在 UNFIXED 代码上必然失败）
  - **Property 3: Bug Condition** - 子 agent LLM 超时导致对话无 done 事件
  - **CRITICAL**: 此测试 MUST 在 UNFIXED 代码上失败；失败即证 bug 存在；**不要试图修测试或代码**
  - **NOTE**: 测试即期望行为——修复后变绿即验证 fix
  - **GOAL**: 产出 SSE 流异常终止的可复现 counterexample
  - **Scoped PBT Approach**: 对一个 deterministic bug 做范围 PBT
    - 输入域：`ChatInvocation(will_trigger_tool=true)`；注入子 agent LLM 会抛出的异常类型从 `{openai.APITimeoutError, httpx.ConnectTimeout, httpx.ReadTimeout, asyncio.TimeoutError}` 采样
  - 实现位置建议：`server/tests/property_tests/test_chat_stream_subagent_timeout.py`
  - 用 `pytest-mock` / `unittest.mock` patch 一个 stub agent，使其在第一次 `on_tool_start(name="task")` 之后的 model 节点抛出抽样到的异常
  - 断言 1：SSE 事件序列中存在至少 1 条 `sub_agent_error`（UNFIXED 不会有）
  - 断言 2：SSE 事件序列中存在至少 1 条 `tool_error`（UNFIXED 不会有）
  - 断言 3：SSE 序列的**最后一个事件** `event:` 字段为 `done`（UNFIXED 为 `error`）
  - 断言 4：assistant message 的 `delivery_status` 最终为 `delivered` 或至少有合法的 done 流程；其 `extra_metadata.execution_steps[?name=='task'].status == 'error'`
  - **EXPECTED OUTCOME**: 测试 FAIL（最后事件是 error、缺 `sub_agent_error` 与 `tool_error`）
  - 记录 counterexample 到 `counterexamples/b2_subagent_timeout_kills_stream.md`，对比 server.log 289–500、913–1020
  - _Requirements: 1.4, 1.5, 1.6_

- [x] 5. 为 B2 写 preservation 属性测试（BEFORE 实现修复）
  - **Property 4: Preservation** - 子 agent 正常成功 & 主 agent fatal 路径不受影响
  - **IMPORTANT**: 先录 unfixed 行为再编码为 property
  - 实现位置建议：`server/tests/property_tests/test_chat_stream_subagent_preservation.py`
  - Observation step：对 UNFIXED 代码录制 2 组 golden
    - 样本 A：子 agent 正常返回（SSE 应含 `sub_agent_start` → `sub_agent_end` → `tool_end` → `done`）
    - 样本 B：主 agent 自身抛一个**非白名单**异常（如 `ValueError("bad input")`）——修复后 SSE 尾两条应为 `error, done`（对应 assistant message `delivery_status="failed"`）；**注意**：这条样本在 UNFIXED 代码上的 golden 仅用于捕获 `error` 事件 payload 结构，后续修复后的预期变为 "倒数第二条 error + 最后一条 done"，并由 preservation 测试直接断言
  - 断言（property）：在 UNFIXED 上样本 A 的事件序列与记录的 golden 一致；样本 B 在 UNFIXED 上尾事件为 `error`（记录作为 baseline），修复后 preservation 测试 SHALL 断言样本 B 尾两条为 `error, done` 且 `delivery_status=="failed"`
  - **EXPECTED OUTCOME**: PASS on UNFIXED；修复后必须仍 PASS
  - _Requirements: 3.4, 3.5, 3.6, 3.7_

- [x] 6. Fix for B2 — event_stream 对子 agent 超时做软失败处理

  - [x] 6.1 实现 `_SUBAGENT_TIMEOUT_ERRORS` 白名单与 catch/resume 循环
    - 编辑 `server/src/api/execution/router.py` 的 `event_stream()`（约 858 行起）
    - 在 chat_stream 顶部补 import：
      - `import openai as _openai_mod`
      - `import httpx as _httpx_mod`
    - 定义 `_SUBAGENT_TIMEOUT_ERRORS = (_openai_mod.APITimeoutError, _httpx_mod.ConnectTimeout, _httpx_mod.ReadTimeout, asyncio.TimeoutError)`
    - 把 `async for event in _agent.astream_events(...)` 循环外包一个 `restart_count ≤ 2` 的 `while` 循环
    - 在 `except _SUBAGENT_TIMEOUT_ERRORS as to_err:` 分支：
      - yield `sub_agent_error` SSE（payload: `{session_id, step: last_tool_step, name: "task", error_kind: type(to_err).__name__, error_message_preview: str(to_err)[:200]}`）
      - yield `tool_error` SSE（同 step，name="task"）
      - 通过 `tool_step_map`/`seen_task_ids` 定位到最近的 task step，更新 `collected_steps[...].status = "error"`、`output = <truncated error>`
      - 构造 `ToolMessage(tool_call_id=<last_task_tool_call_id>, content=f"sub-agent timed out: {type(to_err).__name__}")` 追加到 `agent_messages`
      - `restart_count += 1`；若 `restart_count ≤ 2`，continue 外层 while 重入 `astream_events({"messages": agent_messages})`；否则 break 进入正常闭流
    - 保留 `except Exception as exc:` 的 fatal 分支（非白名单异常）
    - **确保闭流（锁定"先 error 再 done"）**：
      - 正常成功 OR 白名单超时软失败后自然结束 → 走常规 `yield _sse_event("done", {...})`
      - `except Exception as exc:` fatal 分支 → 先 `yield _sse_event("error", {...})`，然后 fall-through 到闭流逻辑 `yield _sse_event("done", {"session_id": session_id, "reply": final_answer or "对话异常结束"})`
      - 同时把 assistant message 的 `delivery_status` 置为 `"failed"`（与正常 `"delivered"` 区分），`extra_metadata.execution_steps` 中未完成 step 标记为 `error`
      - **不提供二选一**：任何异常后都必须以 "倒数第二条 error + 最后一条 done" 两段式收尾
    - _Bug_Condition: `isBugCondition_B2(inv)` from design_
    - _Expected_Behavior: SSE 含 sub_agent_error + tool_error + done; step status = error (Property 3)_
    - _Preservation: 子 agent 正常 / 主 agent fatal 路径不变 (Property 4)_
    - _Requirements: 2.5, 2.6, 2.7, 2.8, 3.4, 3.5, 3.6, 3.7_

  - [x] 6.2 重新运行 Task 4 的探索性测试，验证通过
    - **Property 3: Expected Behavior** - 子 agent 超时降级 + done 闭流
    - **IMPORTANT**: 重跑 Task 4 同一条测试 — 不要写新的
    - `pytest server/tests/property_tests/test_chat_stream_subagent_timeout.py -v`
    - **EXPECTED OUTCOME**: PASS（包含 sub_agent_error + tool_error 且最后事件为 done）
    - _Requirements: Expected Behavior Properties 3 from design_

  - [x] 6.3 重新运行 Task 5 的 preservation 测试，确认无回归
    - **Property 4: Preservation**
    - `pytest server/tests/property_tests/test_chat_stream_subagent_preservation.py -v`
    - **EXPECTED OUTCOME**: PASS（子 agent 成功路径 golden 等价；主 agent fatal 仍以 error 收尾）

---

## B3: RouterLLM 对 DeepSeek 每轮浪费 2s 且把"你好"路由到 full_agent

- [-] 7. 为 B3 写 bug condition 探索性测试（在 UNFIXED 代码上必然失败）
  - **Property 5: Bug Condition** - DeepSeek + 短闲聊必然落入 json_mode 超时 + full_agent
  - **CRITICAL**: 此测试 MUST 在 UNFIXED 代码上失败；失败即证 bug 存在
  - **NOTE**: 测试即期望行为，修复后验证 fix
  - **GOAL**: 产出可自动化重现的延迟+路径 counterexample
  - **Scoped PBT Approach**: message 从 `["你好", "嗯", "ok", "hi", "早", "thanks", "谢谢"]` 采样（长度均 ≤ 6，与默认阈值对齐）；`provider=DEEPSEEK` 固定；`_is_deepseek_model` mock 返回 True；router cache 清空；测试中显式 `monkeypatch.setenv("OPS_ROUTER_HEURISTIC_MAX_LEN", "6")` 以锁定阈值语义（避免未来默认值漂移影响断言）
  - 实现位置建议：`server/tests/property_tests/test_router_llm_deepseek_short_greeting.py`
  - Patch `RouterLLM._classify_via_json_mode` 使其总是 sleep 2.1s 再返回（模拟 DeepSeek + 长 prompt 场景下的必然超时）
  - Patch `_classify_via_function_calling`：在 UNFIXED 代码里它本来就会直接 return None（DeepSeek 硬跳过）
  - 断言 1：`RouterLLM.classify(...)` 的 wall-clock 延迟 ≤ 250ms（UNFIXED 会 ≥ 2000ms）
  - 断言 2：返回的 `decision.route == "direct"` 且 `confidence ≥ 0.7`（UNFIXED 会返回 `fallback_executor("parse_error")` 路由为 `executor`、confidence 0.0）
  - 断言 3（gateway 集成可选）：模拟 gateway.handle(...)，断言不走 `using full_agent fallback` 分支
  - **EXPECTED OUTCOME**: 测试 FAIL（延迟 ≥ 2000ms + route=executor + confidence=0.0）
  - 记录 counterexample 到 `counterexamples/b3_router_deepseek_fullagent.md`，对比 server.log 278–289、911–917
  - _Requirements: 1.7, 1.8, 1.9, 1.10_

- [~] 8. 为 B3 写 preservation 属性测试（BEFORE 实现修复）
  - **Property 6: Preservation** - 非 DeepSeek / 含 ops 关键词 / 缓存命中 路径不变
  - **IMPORTANT**: 先录 unfixed 行为再编码为 property
  - 实现位置建议：`server/tests/property_tests/test_router_llm_preservation.py`
  - Observation step（UNFIXED 代码上录 3 组 baseline）：
    - 样本 A：provider=OPENAI，message="帮我查询一下过去 1h 的 Nginx 错误" → Tier 1 function_calling 成功，decision 有效
    - 样本 B：provider=DEEPSEEK，message="帮我重启一下 nginx" → 触发 `promote_if_ops_keyword`，最终 route=executor
    - 样本 C：router cache 命中（预先 set 一条 `router:decision:{...}` → 直接复用）
    - 样本 D：`OPS_ROUTER_SKIP_FOR_DEEPSEEK=1`，provider=DEEPSEEK，message="你好" → 走旧行为（你可以断言 `chosen_path=="fallback_executor"` 作为向后兼容出口的 golden）
    - 样本 E：`OPS_ROUTER_HEURISTIC_MAX_LEN="0"`（heuristic 被关闭），provider=DEEPSEEK，message="你好" → 行为**恢复为修复前**（走 json_mode fallback，最终 `chosen_path=="fallback_executor"`）；这是第 2 个 back-compat 出口
  - 对每个样本录制 `(chosen_path, decision.route, decision.confidence, decision.suggested_tools, reason)`
  - 写 property-based test：对上述 5 组输入在 UNFIXED 代码上重放，断言录制值等价；注意样本 D / E 需要在测试中显式 `monkeypatch.setenv(...)`
  - **EXPECTED OUTCOME**: PASS on UNFIXED；修复后仍 PASS
  - _Requirements: 3.8, 3.9, 3.10, 3.11_

- [x] 9. Fix for B3 — RouterLLM heuristic + DeepSeek function_calling auto + 800ms 超时

  - [x] 9.1 在 classify() 顶部加 heuristic_direct short-circuit
    - 编辑 `server/src/services/agent_runtime/router.py`
    - 在模块级（或 `RouterLLM.__init__` 中以便测试注入）读环境变量：
      ```
      _OPS_ROUTER_HEURISTIC_MAX_LEN = int(os.environ.get("OPS_ROUTER_HEURISTIC_MAX_LEN", "6"))
      ```
      默认值 `6`；设为 `0` 即完全关闭 heuristic（第 2 个 back-compat 出口，与 `OPS_ROUTER_SKIP_FOR_DEEPSEEK` 并列）
    - 在 `classify()` 的 cache lookup 之后、Tier 1 之前，调用 `heuristic_direct(message)`：
      ```
      trimmed = message.strip()
      if _OPS_ROUTER_HEURISTIC_MAX_LEN > 0 \
         and len(trimmed) <= _OPS_ROUTER_HEURISTIC_MAX_LEN \
         and not contains_ops_keyword(trimmed):
          return RouterDecision(route="direct", direct_answer=None,
                                subagent_name=None, suggested_tools=[],
                                reason="heuristic_short_greeting", confidence=0.8)
      ```
    - 用 `promote_if_ops_keyword` 已有的关键词集合做 `contains_ops_keyword`（复用，不要新定义以免漂移）
    - 新增 metric 分类：`router_path_total{path="heuristic_direct"}` +1
    - _Bug_Condition: isBugCondition_B3 + short greeting (len ≤ OPS_ROUTER_HEURISTIC_MAX_LEN)_
    - _Expected_Behavior: Property 5 第一段（≤ 250ms + direct）_
    - _Preservation: contains_ops_keyword 分支保持 executor 偏好；OPS_ROUTER_HEURISTIC_MAX_LEN=0 时彻底回退到修复前路径 from design_
    - _Requirements: 2.9, 2.10, 2.13_

  - [x] 9.2 让 DeepSeek 走 function_calling auto（而不是硬跳过）+ 环境开关
    - 编辑 `_classify_via_function_calling`（约 362–365 行）
    - 替换硬跳过为：
      ```
      if is_deepseek and _env_flag("OPS_ROUTER_SKIP_FOR_DEEPSEEK", default=False):
          logger.debug("router: skipping function_calling for DeepSeek (opt-out)")
          return None
      if is_deepseek:
          bound = llm.bind_tools([RouterDecisionTool], tool_choice="auto")
      else:
          bound = llm.bind_tools([RouterDecisionTool],
                                 tool_choice={"type": "tool", "name": "decide"})
      response = await bound.ainvoke(messages)
      return _parse_tool_call(response)
      ```
    - 新增 `_env_flag` 工具（或沿用项目已有 env helper）
    - _Requirements: 2.9, 3.11_

  - [x] 9.3 超时窗口从 2000ms 收紧到 800ms（可 env 覆盖）
    - 调整 `DEFAULT_TIMEOUT_MS = 800`，并在构造器里读 `os.environ.get("OPS_ROUTER_TIMEOUT_MS")` 覆盖
    - 更新 Phase B 相关 metric 不变（`router_timeout_total`、`router_path_total` 标签兼容）
    - _Requirements: 2.11_

  - [x] 9.4 Tier 3 兜底调整：超时不再默认 full_agent
    - 当 `_classify_via_json_mode` 也超时 / 解析失败时：
      - `if contains_ops_keyword(message):` → `RouterDecision(route="executor", suggested_tools=[], confidence=0.3, reason="timeout_ops_keyword")`（narrow graph 装配空工具集）
      - `else:` → `RouterDecision(route="direct", direct_answer=None, confidence=0.3, reason="timeout_non_ops")`（让 gateway 走 LLM 直答）
    - **不**再返回 `fallback_executor("parse_error")`（该常量仅保留为 back-compat 标签）
    - _Bug_Condition: isBugCondition_B3 + router LLM 超时_
    - _Expected_Behavior: Property 5 第二段（不落入 full_agent）_
    - _Requirements: 2.11, 2.12_

  - [x] 9.5 重新运行 Task 7 的探索性测试，验证通过
    - **Property 5: Expected Behavior** - DeepSeek 短闲聊 ≤ 250ms direct
    - **IMPORTANT**: 重跑 Task 7 同一条测试 — 不要新写
    - `pytest server/tests/property_tests/test_router_llm_deepseek_short_greeting.py -v`
    - **EXPECTED OUTCOME**: PASS
    - _Requirements: Expected Behavior Properties 5 from design_

  - [x] 9.6 重新运行 Task 8 的 preservation 测试，确认无回归
    - **Property 6: Preservation**
    - `pytest server/tests/property_tests/test_router_llm_preservation.py -v`
    - **EXPECTED OUTCOME**: PASS（5 组样本录制值均等价；样本 D 证明 `OPS_ROUTER_SKIP_FOR_DEEPSEEK=1` 兼容出口；样本 E 证明 `OPS_ROUTER_HEURISTIC_MAX_LEN=0` 彻底关闭 heuristic、回退到修复前路径）

---

## B4: 后端冷启动窗口前端 500（前端/观测层改动，纳入本 spec）

- [ ] 10. Fix for B4 — 冷启动窗口前端降级不再抛 5xx

  - [~] 10.1 为 B4 写 integration/e2e 探索性测试（在 UNFIXED 代码上必然失败）
    - **Property 7: Bug Condition** - 冷启动窗口前端 fetch 封装层产出未处理 5xx
    - **CRITICAL**: 此测试 MUST 在 UNFIXED 代码上失败；失败即证 bug 存在
    - **GOAL**: 产出"后端未启动 / 仅 TCP listen"场景下前端 fetch 层行为的 counterexample
    - 实现位置建议：`web/tests/integration/test_cold_start_proxy.spec.ts`（或沿用项目既有的 e2e 框架）
    - 模拟：后端进程尚未监听业务路由（uvicorn 刚 bind 但 FastAPI routes 未 ready，或直接让 dev proxy upstream 指向一个拒绝连接的端口）
    - 通过前端 fetch/axios 封装层发起 `GET /api/v1/notifications/unread-count` 等任一业务端点
    - 断言 1：响应不是未处理的 500（UNFIXED 会出未处理的 500 红条）
    - 断言 2：调用方不抛出未捕获 Promise rejection；fetch 封装层要么返回可被调用方识别的"冷启动降级"状态（503 + `cold_start: true`），要么在 3 次退避内拿到 2xx
    - **EXPECTED OUTCOME**: 测试 FAIL（UNFIXED 前端得到未处理的 5xx，调用方渲染报错）
    - 记录 counterexample 到 `counterexamples/b4_cold_start_5xx.md`
    - _Requirements: 1.11, 1.12_

  - [~] 10.2 实现后端 `/readyz` + Vite dev proxy `onProxyError` + 前端 axios interceptor
    - **后端**：在 `server/src/main.py` 新增 `GET /readyz`
      - 依次检查：`async_session_factory` 可以 `SELECT 1`、Redis `ping()` 成功、`get_deep_agent()` 已预热（进程级 once-flag）
      - 任一失败返回 `503` + JSON `{"ready": false, "pending": [<失败项名>...]}`
      - 全部通过返回 `200` + `{"ready": true}`
    - **Vite dev proxy**：在 `web/vite.config.ts`（或等价 dev proxy 配置）为 `/api/v1/*` 增加 `onProxyError` 钩子
      - upstream `ECONNREFUSED` / 非 2xx 启动期响应 → 将响应改写为 HTTP `503` + JSON `{"ready": false, "cold_start": true}`
      - **不要**返回 500（避免与后端真实业务 5xx 混淆）
    - **前端统一 axios response interceptor**（`web/src`）
      - 对所有 `/api/v1/*` 响应：当 `status === 503 && body.cold_start === true` 时做指数退避重试：`250ms / 500ms / 1000ms`（最多 3 次）
      - 3 次仍失败：触发一次轻量 toast，并让调用方渲染占位态
      - 对 5xx 且非 cold_start：仍走原错误处理分支（不退避、不吞错）
    - _Bug_Condition: isBugCondition_B4(now, startup_done) from design_
    - _Expected_Behavior: Property 7（Console 无未处理 5xx 红条 + 退避重试或占位降级）_
    - _Preservation: startup 完成后所有业务端点响应与修复前等价 from design_
    - _Requirements: 2.14, 2.15, 3.12_

  - [~] 10.3 重新运行 Task 10.1 的探索性测试，验证通过
    - **Property 7: Expected Behavior** - 冷启动窗口前端降级生效
    - **IMPORTANT**: 重跑 Task 10.1 同一条测试 — 不要新写
    - **EXPECTED OUTCOME**: PASS（前端 fetch 封装层 3 次退避内拿到 2xx，或以 503 + cold_start 降级态返回给调用方；Console 无未处理 5xx 红条）
    - _Requirements: Expected Behavior Properties 7 from design_

---

## 11. Checkpoint - 所有测试必须全部通过

- [~] 11. Checkpoint：全量回归
  - 运行 `cd server && pytest tests/property_tests/ -v`（至少 B1/B2/B3 的 6 条测试全绿）
  - 运行 B4 的 integration/e2e 测试（Task 10.1），确认通过
  - 运行项目已有 `pytest tests/benchmarks`（若有）确认未回归
  - 确认所有 counterexample markdown 已归档到 `server/tests/property_tests/counterexamples/`（含 `b4_cold_start_5xx.md`）
  - 如果有任何一条 Task 1-style 测试仍然失败（FAIL → FAIL，或 FAIL → ERROR），**停下来询问用户**，不要试图掩盖失败
  - 本任务完成 = B1 / B2 / B3 / B4 的 explore 测试 + preservation 测试 全部通过
