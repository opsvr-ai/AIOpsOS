/**
 * B4 Cold-Start Exploration Test
 *
 * **Validates: Requirements 1.11, 1.12**
 *
 * Property 7: Bug Condition — 冷启动窗口前端 fetch 封装层产出未处理 5xx
 *
 * CRITICAL: 此测试 MUST 在 UNFIXED 代码上失败；失败即证 bug 存在。
 *
 * 测试场景：后端进程尚未监听业务路由（uvicorn 刚 bind 但 FastAPI routes 未 ready，
 * 或 Vite dev proxy upstream 指向一个拒绝连接的端口），前端 axios 封装层发起
 * GET /api/v1/notifications/unread-count 等业务端点。
 *
 * 断言 1：响应不是未处理的 500（UNFIXED 会出未处理的 500 红条）
 * 断言 2：调用方不抛出未捕获 Promise rejection；fetch 封装层要么返回可被调用方
 *         识别的"冷启动降级"状态（503 + cold_start: true），要么在 3 次退避内拿到 2xx
 *
 * EXPECTED OUTCOME (UNFIXED): 测试 FAIL（UNFIXED 前端得到未处理的 5xx，调用方渲染报错）
 * EXPECTED OUTCOME (FIXED):   测试 PASS（interceptor 做退避重试或返回降级态）
 *
 * NOTE: 测试通过 axios adapter mock 在 adapter 层注入错误，使 axios response interceptor
 * 能够正常触发（不同于 vi.spyOn(api, 'get') 会绕过 interceptor）。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import axios, { AxiosError, type InternalAxiosRequestConfig, type AxiosResponse } from 'axios';

// ─── Store mocks ─────────────────────────────────────────────────────────────

vi.mock('@/stores/authStore', () => ({
  useAuthStore: {
    getState: () => ({ token: 'test-token', refreshToken: null, logout: vi.fn(), setToken: vi.fn() }),
  },
}));

vi.mock('@/stores/spaceStore', () => ({
  useSpaceStore: {
    getState: () => ({ currentSpace: { id: 'test-space-id' } }),
  },
}));

// ─── Adapter helpers ──────────────────────────────────────────────────────────

type AdapterFn = (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>;

/**
 * 创建一个 axios adapter，模拟 Vite dev proxy 在 upstream ECONNREFUSED 时的默认行为：
 * 透传为 HTTP 500（UNFIXED 行为）。
 */
function makeAdapter500(): AdapterFn {
  return async (config: InternalAxiosRequestConfig) => {
    const err = new AxiosError(
      'Request failed with status code 500',
      'ERR_BAD_RESPONSE',
      config,
      null,
      {
        status: 500,
        statusText: 'Internal Server Error',
        data: 'Internal Server Error',
        headers: {},
        config,
      } as AxiosResponse,
    );
    return Promise.reject(err);
  };
}

/**
 * 创建一个 axios adapter，模拟 Vite dev proxy 修复后的行为：
 * upstream ECONNREFUSED → 改写为 503 + { ready: false, cold_start: true }
 */
function makeAdapter503ColdStart(): AdapterFn {
  return async (config: InternalAxiosRequestConfig) => {
    const err = new AxiosError(
      'Request failed with status code 503',
      'ERR_BAD_RESPONSE',
      config,
      null,
      {
        status: 503,
        statusText: 'Service Unavailable',
        data: { ready: false, cold_start: true },
        headers: {},
        config,
      } as AxiosResponse,
    );
    return Promise.reject(err);
  };
}

/**
 * 创建一个 axios adapter，前两次返回 503+cold_start，第三次返回 200。
 * 用于验证 interceptor 的内部重试逻辑。
 */
function makeAdapterRetryThenSuccess(): { adapter: AdapterFn; callCount: () => number } {
  let count = 0;
  const adapter: AdapterFn = async (config: InternalAxiosRequestConfig) => {
    count++;
    if (count < 3) {
      const err = new AxiosError(
        'Request failed with status code 503',
        'ERR_BAD_RESPONSE',
        config,
        null,
        {
          status: 503,
          statusText: 'Service Unavailable',
          data: { ready: false, cold_start: true },
          headers: {},
          config,
        } as AxiosResponse,
      );
      return Promise.reject(err);
    }
    return Promise.resolve({
      status: 200,
      statusText: 'OK',
      data: { count: 0 },
      headers: {},
      config,
    } as AxiosResponse);
  };
  return { adapter, callCount: () => count };
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('B4 Cold-Start: Frontend fetch wrapper behavior on 5xx (UNFIXED code MUST FAIL)', () => {
  let originalAdapter: unknown;

  beforeEach(async () => {
    vi.clearAllMocks();
    // Reset module cache so each test gets a fresh api instance with clean interceptor state
    vi.resetModules();
    // Re-apply store mocks after resetModules
    vi.mock('@/stores/authStore', () => ({
      useAuthStore: {
        getState: () => ({ token: 'test-token', refreshToken: null, logout: vi.fn(), setToken: vi.fn() }),
      },
    }));
    vi.mock('@/stores/spaceStore', () => ({
      useSpaceStore: {
        getState: () => ({ currentSpace: { id: 'test-space-id' } }),
      },
    }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  /**
   * 断言 1: UNFIXED 代码下，当 Vite proxy 透传 500 时，
   * api 封装层直接 reject，调用方收到未处理的 AxiosError(500)。
   *
   * 修复后：此测试应 PASS，因为 interceptor 识别 500（unfixed proxy 行为），
   * 做退避重试，最终返回降级状态而非抛出。
   *
   * UNFIXED 行为：api.ts 的 response interceptor 只处理 401，
   * 对 500 直接 return Promise.reject(error)，调用方收到 AxiosError。
   */
  it('Assertion 1: caller should NOT receive an unhandled 500 error during cold-start window', async () => {
    const { default: api } = await import('@/services/api');

    // Inject adapter at the axios adapter level so the response interceptor fires
    originalAdapter = api.defaults.adapter;
    api.defaults.adapter = makeAdapter500();

    let caughtError: AxiosError | null = null;
    let responseStatus: number | null = null;

    try {
      const response = await api.get('/notifications/unread-count');
      responseStatus = response.status;
    } catch (err) {
      if (err instanceof AxiosError) {
        caughtError = err;
      }
    } finally {
      api.defaults.adapter = originalAdapter as typeof api.defaults.adapter;
    }

    // UNFIXED: caughtError is not null (500 propagates to caller)
    // FIXED: caughtError should be null (interceptor handles cold-start gracefully)
    //
    // This assertion FAILS on UNFIXED code because the caller receives a raw 500 error.
    expect(caughtError, 'UNFIXED: caller received unhandled 500 error — cold-start not handled').toBeNull();

    // If we got here without error, the response should be a valid degraded state
    if (responseStatus !== null) {
      expect([200, 503]).toContain(responseStatus);
    }
  });

  /**
   * 断言 2: UNFIXED 代码下，当 proxy 产生 503 + cold_start: true 时，
   * axios interceptor 不做退避重试，直接 reject 给调用方。
   *
   * 修复后：interceptor 应识别 503 + cold_start: true，做指数退避重试
   * (250ms / 500ms / 1000ms)，最多 3 次；3 次仍失败则返回降级状态而非抛出。
   *
   * UNFIXED 行为：api.ts 的 response interceptor 只处理 401，
   * 对 503 直接 return Promise.reject(error)，调用方收到 AxiosError(503)。
   */
  it('Assertion 2: caller should NOT receive unhandled Promise rejection on cold-start 503', async () => {
    const { default: api } = await import('@/services/api');

    // Inject adapter at the axios adapter level so the response interceptor fires
    originalAdapter = api.defaults.adapter;
    api.defaults.adapter = makeAdapter503ColdStart();

    let unhandledRejection = false;
    let caughtError: AxiosError | null = null;

    const unhandledHandler = () => { unhandledRejection = true; };
    process.on('unhandledRejection', unhandledHandler);

    try {
      await api.get('/notifications/unread-count');
    } catch (err) {
      if (err instanceof AxiosError) {
        caughtError = err;
      }
    } finally {
      api.defaults.adapter = originalAdapter as typeof api.defaults.adapter;
      process.removeListener('unhandledRejection', unhandledHandler);
    }

    // UNFIXED: caughtError is not null (503 propagates to caller without retry)
    // FIXED: caughtError should be null (interceptor retried 3 times, then returned degraded state)
    //
    // This assertion FAILS on UNFIXED code because the caller receives a raw 503 error.
    expect(
      caughtError,
      'UNFIXED: caller received unhandled 503 cold_start error — no retry/degradation in interceptor',
    ).toBeNull();

    expect(unhandledRejection, 'unhandledRejection event should not fire').toBe(false);
  });

  /**
   * 断言 3: UNFIXED 代码下，axios interceptor 对 cold-start 场景没有退避重试逻辑。
   *
   * 验证方式：adapter 前两次返回 503+cold_start，第三次返回 200。
   * 修复后 interceptor 应在同一次调用内自动重试（不需要调用方重试），
   * 最终拿到 200。
   *
   * UNFIXED 行为：每次调用都立即 reject，调用方需要自己处理重试。
   */
  it('Assertion 3: fetch wrapper should handle cold-start retry internally (not expose to caller)', async () => {
    const { default: api } = await import('@/services/api');

    const { adapter, callCount } = makeAdapterRetryThenSuccess();
    originalAdapter = api.defaults.adapter;
    api.defaults.adapter = adapter;

    let finalError: AxiosError | null = null;
    let finalStatus: number | null = null;

    try {
      const response = await api.get('/notifications/unread-count');
      finalStatus = response.status;
    } catch (err) {
      if (err instanceof AxiosError) {
        finalError = err;
      }
    } finally {
      api.defaults.adapter = originalAdapter as typeof api.defaults.adapter;
    }

    // UNFIXED: finalError is not null (first 503 propagates immediately, no internal retry)
    // FIXED: finalError is null, finalStatus is 200 (interceptor retried internally)
    //
    // This assertion FAILS on UNFIXED code because the interceptor has no retry logic.
    expect(
      finalError,
      'UNFIXED: interceptor has no cold-start retry logic — first 503 propagates to caller immediately',
    ).toBeNull();

    if (finalStatus !== null) {
      expect(finalStatus).toBe(200);
    }

    // UNFIXED: callCount will be 1 (no retry)
    // FIXED: callCount will be 3 (2 retries + 1 success)
    expect(
      callCount(),
      `UNFIXED: interceptor made ${callCount()} call(s) instead of retrying up to 3 times`,
    ).toBeGreaterThanOrEqual(2);
  });

  /**
   * 断言 4 (白盒): 验证 api.ts 的 response interceptor 当前不包含 cold_start 处理逻辑。
   *
   * 这是一个直接的代码行为验证：
   * - UNFIXED: interceptor 只处理 401，对其他错误直接 reject
   * - FIXED: interceptor 应包含 503+cold_start 的退避重试逻辑
   *
   * 通过检查 interceptor 对 503 的实际行为来验证。
   * api 实例应处理 503+cold_start（返回降级态），plain axios 实例不处理（直接 reject）。
   */
  it('Assertion 4 (whitebox): api.ts response interceptor must handle 503+cold_start (currently missing)', async () => {
    const { default: api } = await import('@/services/api');

    // Create a plain axios instance to compare behavior (no cold-start interceptor)
    const plainAxios = axios.create({ baseURL: '/api/v1' });

    const coldStartAdapter = makeAdapter503ColdStart();

    // Inject adapter into both instances
    const origApiAdapter = api.defaults.adapter;
    const origPlainAdapter = plainAxios.defaults.adapter;
    api.defaults.adapter = coldStartAdapter;
    plainAxios.defaults.adapter = coldStartAdapter;

    let apiError: AxiosError | null = null;
    let plainError: AxiosError | null = null;

    try {
      await api.get('/notifications/unread-count');
    } catch (err) {
      if (err instanceof AxiosError) apiError = err;
    } finally {
      api.defaults.adapter = origApiAdapter as typeof api.defaults.adapter;
    }

    try {
      await plainAxios.get('/notifications/unread-count');
    } catch (err) {
      if (err instanceof AxiosError) plainError = err;
    } finally {
      plainAxios.defaults.adapter = origPlainAdapter as typeof plainAxios.defaults.adapter;
    }

    // UNFIXED: both api and plainAxios behave identically (both reject with 503)
    // This proves the api interceptor has NO cold-start handling.
    //
    // FIXED: api should handle 503+cold_start gracefully (apiError === null),
    // while plainAxios still rejects (plainError !== null).
    //
    // This assertion FAILS on UNFIXED code because api behaves same as plain axios.
    expect(
      apiError,
      'UNFIXED: api.ts interceptor has no cold_start handling — behaves same as plain axios on 503',
    ).toBeNull();

    // plain axios should still reject (no interceptor)
    expect(
      plainError,
      'plain axios (no interceptor) should still reject with 503',
    ).not.toBeNull();
  });
});
