import { useEffect } from 'react';
import { ConfigProvider, App as AntApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import zhCN from 'antd/locale/zh_CN';
import { useThemeStore } from '@/stores/themeStore';
import { darkTheme, lightTheme } from '@/theme';
import { appRoutes } from '@/router';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30000 } },
});

const router = createBrowserRouter(appRoutes);

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
          <RouterProvider router={router} />
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
