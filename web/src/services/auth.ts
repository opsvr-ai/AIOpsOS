import api from "./api";

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RegisterRequest {
  username: string;
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export const authApi = {
  login: (data: LoginRequest) => api.post<TokenResponse>("/auth/login", data),
  register: (data: RegisterRequest) => api.post("/auth/register", data),
  refresh: (refreshToken: string) =>
    api.post<TokenResponse>("/auth/refresh", { refresh_token: refreshToken }),
  getMe: () => api.get<{ id: string; username: string; email: string }>("/auth/me"),
};
