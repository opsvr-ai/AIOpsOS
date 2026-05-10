import axios from "axios";
import { useAuthStore } from "@/stores/authStore";
import { useSpaceStore } from "@/stores/spaceStore";

const api = axios.create({
  baseURL: "/api/v1",
  timeout: 30000,
});

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  const space = useSpaceStore.getState().currentSpace;
  if (space?.id) {
    config.headers["X-Space-Id"] = space.id;
  }
  return config;
});

let isRefreshing = false;
let refreshQueue: Array<{ resolve: (token: string) => void; reject: (e: unknown) => void }> = [];

/** Exponential backoff delays for cold-start retries (ms). */
const COLD_START_DELAYS = [250, 500, 1000];

/**
 * Returns true if the error is a cold-start signal:
 * - 503 + body.cold_start === true  (fixed Vite proxy rewrites ECONNREFUSED → 503+cold_start)
 * - 500 with no body / plain string body (unfixed Vite proxy passes ECONNREFUSED through as 500)
 */
function isColdStartError(error: unknown): boolean {
  if (!axios.isAxiosError(error) || !error.response) return false;
  const { status, data } = error.response;
  if (status === 503 && data?.cold_start === true) return true;
  // Unfixed proxy sends 500 with a plain string body for ECONNREFUSED
  if (status === 500 && (typeof data === "string" || !data)) return true;
  return false;
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // ── Cold-start retry logic ────────────────────────────────────────────────
    // 503 + cold_start: true  → exponential backoff retry (250ms / 500ms / 1000ms)
    // 500 (unfixed proxy)     → same treatment (ECONNREFUSED passed through as 500)
    // After 3 retries         → return a degraded response instead of throwing,
    //                           so callers never receive an unhandled 5xx rejection.
    if (isColdStartError(error) && originalRequest) {
      const retryCount: number = originalRequest._coldStartRetryCount ?? 0;
      if (retryCount < COLD_START_DELAYS.length) {
        originalRequest._coldStartRetryCount = retryCount + 1;
        const delay = COLD_START_DELAYS[retryCount];
        await new Promise<void>((resolve) => setTimeout(resolve, delay));
        // Retry the request
        return api(originalRequest);
      }
      // After max retries: reject with a recognizable ColdStartError.
      // Callers' catch blocks will handle this gracefully (e.g., show "加载失败" + retry button).
      // We do NOT resolve with a fake response object, because callers expect res.data
      // to match the endpoint's schema (e.g., an array for /spaces), not {cold_start: true}.
      const coldStartError = new Error("Service unavailable (cold-start)") as Error & {
        isColdStart: boolean;
        degraded: boolean;
        config: typeof originalRequest;
      };
      coldStartError.isColdStart = true;
      coldStartError.degraded = true;
      coldStartError.config = originalRequest;
      return Promise.reject(coldStartError);
    }

    // ── 401 token refresh ─────────────────────────────────────────────────────
    if (error.response?.status === 401 && !originalRequest._retry) {
      const { refreshToken, setToken, logout } = useAuthStore.getState();
      if (!refreshToken) {
        logout();
        window.location.href = "/login";
        return Promise.reject(error);
      }
      originalRequest._retry = true;
      if (!isRefreshing) {
        isRefreshing = true;
        try {
          const res = await axios.post("/api/v1/auth/refresh", { refresh_token: refreshToken });
          const { access_token, refresh_token } = res.data;
          setToken(access_token, refresh_token);
          refreshQueue.forEach((q) => q.resolve(access_token));
          refreshQueue = [];
          originalRequest.headers.Authorization = `Bearer ${access_token}`;
          return api(originalRequest);
        } catch {
          refreshQueue.forEach((q) => q.reject(new Error("refresh failed")));
          refreshQueue = [];
          logout();
          window.location.href = "/login";
          return Promise.reject(error);
        } finally {
          isRefreshing = false;
        }
      } else {
        return new Promise((resolve, reject) => {
          refreshQueue.push({
            resolve: (token: string) => {
              originalRequest.headers.Authorization = `Bearer ${token}`;
              resolve(api(originalRequest));
            },
            reject,
          });
        });
      }
    }
    return Promise.reject(error);
  },
);

export default api;
