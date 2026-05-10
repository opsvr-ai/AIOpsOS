# B4 Cold-Start 5xx — Counterexample Record

**Bug**: 后端冷启动窗口前端 fetch 封装层产出未处理 5xx  
**Property**: Property 7 — Bug Condition  
**Test File**: `web/tests/integration/test_cold_start_proxy.spec.ts`  
**Requirements**: 1.11, 1.12  
**Status**: CONFIRMED (test FAILS on UNFIXED code as expected)  
**Date**: 2026-05-09

---

## Bug Description

在后端冷启动窗口（`isBugCondition_B4(now, startup_done) = true`）内，前端页面加载时发起的 API 请求（如 `GET /api/v1/notifications/unread-count`）会遭遇：

1. **Vite dev proxy 默认行为**：upstream `ECONNREFUSED` → 透传为 HTTP `500 Internal Server Error`
2. **前端 axios 封装层（`web/src/services/api.ts`）**：response interceptor 只处理 `401`（token refresh），对 `500` / `503` 直接 `return Promise.reject(error)`
3. **结果**：调用方收到未处理的 `AxiosError`，渲染报错红条

---

## Counterexamples Found

### Counterexample 1: Vite proxy 透传 500 → 调用方收到未处理 AxiosError

**Input**: `GET /api/v1/notifications/unread-count` during cold-start window  
**Simulated proxy behavior**: `ECONNREFUSED` → HTTP `500 Internal Server Error`  
**Expected (FIXED)**: `caughtError === null` (interceptor handles gracefully)  
**Actual (UNFIXED)**:
```
AxiosError: Request failed with status code 500
  code: 'ERR_BAD_RESPONSE'
  response.status: 500
  response.data: 'Internal Server Error'
```
**Assertion failure**:
```
AssertionError: UNFIXED: caller received unhandled 500 error — cold-start not handled
  expected AxiosError: Request failed with status co… { …(4) } to be null
  at tests/integration/test_cold_start_proxy.spec.ts:133:98
```

---

### Counterexample 2: 503 + cold_start: true → 无退避重试，直接 reject

**Input**: `GET /api/v1/notifications/unread-count` with proxy returning `503 + { ready: false, cold_start: true }`  
**Expected (FIXED)**: `caughtError === null` (interceptor retries with 250ms/500ms/1000ms backoff)  
**Actual (UNFIXED)**:
```
AxiosError: Request failed with status code 503
  code: 'ERR_BAD_RESPONSE'
  response.status: 503
  response.data: { ready: false, cold_start: true }
```
**Assertion failure**:
```
AssertionError: UNFIXED: caller received unhandled 503 cold_start error — no retry/degradation in interceptor
  expected AxiosError: Request failed with status co… { …(4) } to be null
  at tests/integration/test_cold_start_proxy.spec.ts:184:7
```

---

### Counterexample 3: 无内部重试逻辑 — 第一次 503 立即传播给调用方

**Input**: 连续 3 次请求，前 2 次返回 `503+cold_start`，第 3 次返回 `200`  
**Expected (FIXED)**: `finalError === null`, `callCount >= 2` (interceptor 内部自动重试)  
**Actual (UNFIXED)**:
```
finalError: AxiosError(503)
callCount: 1  (no retry attempted)
```
**Assertion failure**:
```
AssertionError: UNFIXED: interceptor has no cold-start retry logic — first 503 propagates to caller immediately
  expected AxiosError: Request failed with status co… { …(4) } to be null
  at tests/integration/test_cold_start_proxy.spec.ts:229:7
```

---

### Counterexample 4 (白盒): api.ts interceptor 与 plain axios 行为完全相同

**Input**: `GET /api/v1/notifications/unread-count` with `503+cold_start` response  
**Expected (FIXED)**: `apiError === null` (api.ts interceptor handles cold_start), `plainError !== null` (plain axios rejects)  
**Actual (UNFIXED)**:
```
apiError: AxiosError(503)    ← same as plain axios
plainError: AxiosError(503)  ← expected
```
**Assertion failure**:
```
AssertionError: UNFIXED: api.ts interceptor has no cold_start handling — behaves same as plain axios on 503
  expected AxiosError: Request failed with status co… { …(4) } to be null
  at tests/integration/test_cold_start_proxy.spec.ts:291:7
```

---

## Root Cause Analysis

### 1. `web/src/services/api.ts` — Response Interceptor 缺失 cold-start 处理

当前 interceptor（第 23–62 行）只处理 `401`：

```typescript
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (error.response?.status === 401 && !originalRequest._retry) {
      // ... token refresh logic ...
    }
    return Promise.reject(error);  // ← 所有非 401 错误直接 reject，包括 500/503
  },
);
```

**缺失的逻辑**：
- 检测 `status === 503 && body.cold_start === true`
- 指数退避重试：`250ms / 500ms / 1000ms`（最多 3 次）
- 3 次失败后：触发 toast + 返回降级状态（而非抛出）

### 2. `web/vite.config.ts` — Proxy 无 `onProxyError` 钩子

当前 `/api` proxy 配置：

```typescript
'/api': {
  target: 'http://localhost:8000',
  changeOrigin: true,
  ws: true,
  // ← 缺少 onProxyError 钩子
},
```

**缺失的逻辑**：
```typescript
onProxyError: (err, req, res) => {
  // ECONNREFUSED → 改写为 503 + { ready: false, cold_start: true }
  // 而不是默认的 500
}
```

---

## Fix Required (Task 10.2)

1. **后端**：`server/src/main.py` 新增 `GET /readyz` 端点
2. **Vite proxy**：`web/vite.config.ts` 为 `/api/v1/*` 增加 `onProxyError` 钩子，将 `ECONNREFUSED` 改写为 `503 + { ready: false, cold_start: true }`
3. **前端 axios interceptor**：`web/src/services/api.ts` 增加 `503+cold_start` 退避重试逻辑

---

## Test Run Output

```
 RUN  v1.6.1 E:/dev/AIOpsOS/web

 ❯ tests/integration/test_cold_start_proxy.spec.ts (4)
   ❯ B4 Cold-Start: Frontend fetch wrapper behavior on 5xx (UNFIXED code MUST FAIL) (4)
     × Assertion 1: caller should NOT receive an unhandled 500 error during cold-start window
     × Assertion 2: caller should NOT receive unhandled Promise rejection on cold-start 503
     × Assertion 3: fetch wrapper should handle cold-start retry internally (not expose to caller)
     × Assertion 4 (whitebox): api.ts response interceptor must handle 503+cold_start (currently missing)

 Test Files  1 failed (1)
      Tests  4 failed (4)
   Start at  13:31:07
   Duration  517ms
```

**结论**：4 条断言全部失败，证明 B4 bug 真实存在。测试 FAIL = Task 10.1 完成。
