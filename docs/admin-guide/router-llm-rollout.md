# RouterLLM 全量放开 Runbook

**Spec:** `.kiro/specs/agent-runtime-optimization-evolution` — 任务 24.2
**Requirements:** Phase M DoD, R-7.3
**Related code:**
- 脚本 `server/scripts/rollout_router_llm.py`
- 基线基准 `server/tests/bench/test_chat_latency.py`
- Admin API `server/src/api/control/runtime_flags.py` (`PUT /api/v1/runtime-flags/{key}`)
- 默认种子 `server/src/services/feature_flags_bootstrap.py`（`router_llm_enabled` 默认 10% 灰度）

本手册描述如何把 `router_llm_enabled` 运行时 feature flag 从
10%（任务 24.1 的默认灰度值）提升到 100%，以及 7 天观察期内的验证步骤与回滚方案。

---

## 1. 概览

- **目标：** `router_llm_enabled.rollout_percent = 100`，`enabled = true`。
- **前置条件：** 已完成任务 24.1，该 flag 已按默认 10% 灰度上线。
- **保护性原则：** 观察 7 天，若 `test_chat_latency.py` p50 / p95 / p99 指标相对基线无回归，再进入 100%。若任意 SLO 破线或生产 RouterLLM 路径产生业务回归，执行回滚。

整个过程只修改 `runtime_feature_flags` 一行数据，无需代码部署或重启服务；FeatureFlagService 会在 ≤ 15s 内感知变更（R-7.3）。

---

## 2. 7 天验证步骤

### 2.1 每日基准检查

在代表性实例（或预发布环境）上每日跑一次 dispatcher 基准：

```bash
cd server
RUN_BENCH=1 \
  ./.venv/Scripts/python3.exe -m pytest \
    tests/bench/test_chat_latency.py \
    -x --tb=short
```

关键指标（见 R-1.5 及测试内硬编码的预算）：

| 指标 | 预算 | 说明 |
| ---- | ---- | ---- |
| p50  | ≤ 200 ms | dispatcher 中位数 |
| p95  | ≤ 1000 ms | 一类阈，单条回归即警告 |
| p99  | ≤ 2000 ms | 尾延迟，常用于定位 GC / DB 抖动 |

**容差：** 7 天滚动窗口中允许单次 p95 抖动 ≤ 基线 +10%；若连续 2 天超阈或任意一天超过 `+20%`，暂停放量并排查。

### 2.2 Prometheus 监控观察

同步观察 `/metrics` 上的以下系列（由 Phase G 提供）：

- `router_path_total{path="function_calling"}` 占比 ≥ 95%（R-10.2）
- `router_timeout_total` 每分钟增量 < 1（R-10.4）
- `chat_turn_latency_ms{stage="router"}` p95 ≤ 500ms
- `trajectory_emit_dropped_total` 持平或零增长（零丢失 — R-6.3）

任意一个出现异常尖峰应触发 2.3 回滚。

### 2.3 业务侧验证

手工运行 `data/eval_sets/v1/*.jsonl` 五套场景作为对照组（参考
`scripts/eval_run.py`），与升级前基线对比加权均分变动 ≤ 0.2。

---

## 3. 执行放量

通过环境变量指向目标控制面并提供管理员 token：

```bash
export AIOPSOS_CONTROL_URL="https://control.example.com:8001"
export AIOPSOS_ADMIN_TOKEN="<admin-bearer-token>"
```

**干跑**（不发送请求，仅打印请求计划）：

```bash
cd server
./.venv/Scripts/python3.exe -m scripts.rollout_router_llm --dry-run
```

预期输出片段：

```
[dry-run] PUT https://control.example.com:8001/api/v1/runtime-flags/router_llm_enabled
[dry-run] body: {"enabled": true, "rollout_percent": 100}
[dry-run] router_llm_enabled: FULL rollout (100%)
```

**正式放量：**

```bash
cd server
./.venv/Scripts/python3.exe -m scripts.rollout_router_llm
```

脚本会发送一次 `PUT /api/v1/runtime-flags/router_llm_enabled`，请求体为
`{"enabled": true, "rollout_percent": 100}`；服务端使用 upsert 语义保留原有
`data` 描述字段（见 `RuntimeFlagUpsert`）。

**确认：** ≤ 15s 后在任一实例上观察到
`FeatureFlagService.is_enabled("router_llm_enabled", user_id=...)` 恒为 True，
且 `router_path_total` 中 `fallback_executor` 占比降至 RouterLLM 失败率水位。

---

## 4. 回滚方案

**轻度回滚**（回到 10% 灰度）：

```bash
cd server
./.venv/Scripts/python3.exe -m scripts.rollout_router_llm --rollout 10
```

**硬关闭**（完全停用 RouterLLM，全部走 fallback executor — R-1.3）：

```bash
cd server
./.venv/Scripts/python3.exe -m scripts.rollout_router_llm --rollout 0 --disable
```

两种回滚都是同一个 PUT 请求，差别仅在 body 的 `rollout_percent` / `enabled`
字段。回滚后 15 秒内所有 /chat 请求将跳过 RouterLLM 直接进入 ExecutorAgent，
满足 R-10.4 的降级安全性。

回滚后应：

1. 在监控上确认 `router_path_total{path="function_calling"}` 下降。
2. 记录回滚原因到 `docs/admin-guide/evolution-runbook.md` 的事件日志节。
3. 为下一轮放量打开工单，附带本次观察到的基线数据。

---

## 5. 故障排查

| 症状 | 可能原因 | 处理 |
| ---- | -------- | ---- |
| PUT 返回 401/403 | `AIOPSOS_ADMIN_TOKEN` 缺失或权限不足 | 检查令牌 / `require_admin` 审计日志 |
| PUT 返回 422 | body 校验不通过（rollout 越界） | 脚本会在本地 argparse 校验 0-100，可检查是否显式设置了非法值 |
| 10% 已稳定但 100% 后 p95 回归 | 新路径缓存未预热 / LLM provider 端限流 | 先回 50% 观察 1 小时，再决定继续或回到 10% |
| 改动后 15s 内未生效 | `FeatureFlagService` 后台刷新任务被阻塞 | 查 `/metrics` 上 `feature_flag_refresh_seconds`；必要时重启实例触发冷启动刷新 |
| 审计日志中查不到 upsert | 请求未走 `require_admin` 链路 | 核对 `AIOPSOS_CONTROL_URL` 是否指向控制面（非执行面） |

---

## 6. 附录：请求契约

脚本发送的请求与服务端契约完全一致：

- **URL:** `PUT {base_url}{api_prefix}/runtime-flags/router_llm_enabled`
  默认 `base_url = http://localhost:8001`，`api_prefix = /api/v1`。
- **Headers:**
  - `Content-Type: application/json`
  - `Accept: application/json`
  - `Authorization: Bearer <token>`（仅当设置了 `AIOPSOS_ADMIN_TOKEN`）
- **Body:** `{"enabled": true, "rollout_percent": 100}`
- **响应:** `RuntimeFlagOut`，其中 `rollout_percent`、`enabled`、`data.description`
  保持与上一次 upsert 一致（本次未传 `data`）。

脚本源代码及单测见：

- `server/scripts/rollout_router_llm.py`
- `server/tests/scripts/test_rollout_router_llm.py`
