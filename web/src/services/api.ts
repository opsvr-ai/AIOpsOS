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

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
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
