import api from './api';

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

export interface ProfileData {
  display_name?: string;
  phone?: string;
  department?: string;
  title?: string;
  email?: string;
}

export interface PasswordChangeData {
  old_password: string;
  new_password: string;
}

export const authApi = {
  login: (data: LoginRequest & { login_type?: string }) =>
    api.post<TokenResponse>('/auth/login', data),
  register: (data: RegisterRequest) => api.post('/auth/register', data),
  refresh: (refreshToken: string) =>
    api.post<TokenResponse>('/auth/refresh', { refresh_token: refreshToken }),
  getMe: () =>
    api.get<{
      id: string;
      username: string;
      email: string;
      default_space_id?: string;
      roles: { id: string; name: string }[];
      display_name?: string;
      phone?: string;
      department?: string;
      title?: string;
      source?: string;
      status?: string;
      setup_required?: boolean;
    }>('/auth/me'),
  updateProfile: (data: ProfileData) => api.put('/auth/profile', data),
  changePassword: (data: PasswordChangeData) => api.put('/auth/password', data),
  getInvitation: (token: string) =>
    api.get<{ email: string; space_id?: string; space_name?: string; expires_at: string }>(
      `/auth/invitation/${token}`,
    ),
  acceptInvitation: (token: string, data: RegisterRequest) =>
    api.post<TokenResponse>(`/auth/accept-invitation/${token}`, data),
  inviteUser: (data: { email: string; space_id?: string; platform_url?: string }) =>
    api.post('/users/invite', data),
  getUsers: (params?: {
    q?: string;
    source?: string;
    status?: string;
    page?: number;
    page_size?: number;
  }) => api.get('/users', { params }),
  approveUser: (userId: string, data: { approved: boolean; message?: string }) =>
    api.post(`/users/${userId}/approve`, data),
  deleteUser: (userId: string) => api.delete(`/users/${userId}`),
};
