import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  user: {
    id: string;
    username: string;
    email: string;
    default_space_id?: string;
    roles: string[];
  } | null;
  setupRequired: boolean;
  setAuth: (
    token: string,
    refreshToken: string,
    user: {
      id: string;
      username: string;
      email: string;
      default_space_id?: string;
      roles: string[];
    },
  ) => void;
  setSetupRequired: (v: boolean) => void;
  setToken: (token: string, refreshToken: string) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      refreshToken: null,
      user: null,
      setupRequired: false,
      setAuth: (token, refreshToken, user) => set({ token, refreshToken, user }),
      setSetupRequired: (setupRequired) => set({ setupRequired }),
      setToken: (token, refreshToken) => set({ token, refreshToken }),
      logout: () => set({ token: null, refreshToken: null, user: null, setupRequired: false }),
    }),
    { name: 'aiopsos-auth' },
  ),
);
