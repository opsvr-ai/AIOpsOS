import { useEffect } from 'react';
import { ConfigProvider, App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import zhCN from 'antd/locale/zh_CN';
import { useThemeStore } from '@/stores/themeStore';
import { useAuthStore } from '@/stores/authStore';
import { authApi } from '@/services/auth';
import { darkTheme, lightTheme } from '@/theme';
import { appRoutes } from '@/router';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30000 } },
});

const router = createBrowserRouter(appRoutes, {
  // @ts-expect-error -- v7_startTransition exists at runtime in react-router-dom 6.30 but types are stale
  future: { v7_startTransition: true },
});

function AuthRefresher() {
  const token = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);
  const setAuth = useAuthStore((s) => s.setAuth);
  const setSetupRequired = useAuthStore((s) => s.setSetupRequired);
  const setupRequired = useAuthStore((s) => s.setupRequired);

  useEffect(() => {
    if (!token) return;
    if (!user || !user.id || user.roles.length === 0 || setupRequired) {
      authApi
        .getMe()
        .then((res) => {
          const d = res.data;
          setAuth(token, useAuthStore.getState().refreshToken || '', {
            id: d.id,
            username: d.username,
            email: d.email,
            default_space_id: d.default_space_id,
            roles: d.roles.map((r) => r.name),
          });
          setSetupRequired(d.setup_required || false);
        })
        .catch((err) => {
          // If cold-start error, retry after a short delay
          if (err?.isColdStart || err?.degraded) {
            setTimeout(() => {
              authApi.getMe().then((res) => {
                const d = res.data;
                setAuth(token, useAuthStore.getState().refreshToken || '', {
                  id: d.id,
                  username: d.username,
                  email: d.email,
                  default_space_id: d.default_space_id,
                  roles: d.roles.map((r) => r.name),
                });
                setSetupRequired(d.setup_required || false);
              }).catch(() => {});
            }, 2000);
          }
        });
    }
  }, []);

  useEffect(() => {
    if (setupRequired && window.location.pathname !== '/onboarding') {
      window.location.replace('/onboarding');
    }
  }, [setupRequired]);

  return null;
}

export default function App() {
  const mode = useThemeStore((s) => s.mode);

  useEffect(() => {
    const bg = mode === 'dark' ? '#020617' : '#F8FAFC';
    const color = mode === 'dark' ? '#F1F5F9' : '#0F172A';
    document.body.style.background = bg;
    document.body.style.color = color;
  }, [mode]);

  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider theme={mode === 'dark' ? darkTheme : lightTheme} locale={zhCN}>
        <AntApp>
          <AuthRefresher />
          <RouterProvider router={router} />
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
