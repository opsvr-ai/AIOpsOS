import { lazy, Suspense } from 'react';
import { Spin } from 'antd';

const MainLayout = lazy(() => import('@/components/layout/MainLayout'));
const DashboardPage = lazy(() => import('@/features/dashboard/DashboardPage'));
const ChatPage = lazy(() => import('@/features/chat/ChatPage'));
const AlertsPage = lazy(() => import('@/features/alerts/AlertsPage'));
const ScenarioPage = lazy(() => import('@/features/scenarios/ScenarioPage'));
const AutomationPage = lazy(() => import('@/features/automation/AutomationPage'));
const SleepManagementPage = lazy(() => import('@/features/sleep/SleepManagementPage'));
const AgentsPage = lazy(() => import('@/features/agents/AgentsPage'));
const ToolsPage = lazy(() => import('@/features/tools/ToolsPage'));
const KnowledgePage = lazy(() => import('@/features/knowledge/KnowledgePage'));
const ChannelsPage = lazy(() => import('@/features/channels/ChannelsPage'));
const UsersPage = lazy(() => import('@/features/users/UsersPage'));
const CronPage = lazy(() => import('@/features/cron/CronPage'));
const SystemPage = lazy(() => import('@/features/system/SystemPage'));
const MemoryPage = lazy(() => import('@/features/memory/MemoryPage'));
const DocsPage = lazy(() => import('@/features/docs/DocsPage'));
const LoginPage = lazy(() => import('@/features/LoginPage'));

const Lazy = ({ children }: { children: React.ReactNode }) => (
  <Suspense
    fallback={
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 100 }}>
        <Spin size="large" />
      </div>
    }
  >
    {children}
  </Suspense>
);

export const appRoutes = [
  {
    path: '/login',
    element: (
      <Lazy>
        <LoginPage />
      </Lazy>
    ),
  },
  {
    path: '/',
    element: (
      <Lazy>
        <MainLayout />
      </Lazy>
    ),
    children: [
      {
        index: true,
        element: (
          <Lazy>
            <DashboardPage />
          </Lazy>
        ),
      },
      {
        path: 'ops',
        element: (
          <Lazy>
            <DashboardPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/chat',
        element: (
          <Lazy>
            <ChatPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/alerts',
        element: (
          <Lazy>
            <AlertsPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/scenarios',
        element: (
          <Lazy>
            <ScenarioPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/automation',
        element: (
          <Lazy>
            <AutomationPage />
          </Lazy>
        ),
      },
      {
        path: 'ai/agents',
        element: (
          <Lazy>
            <AgentsPage />
          </Lazy>
        ),
      },
      {
        path: 'ai/tools',
        element: (
          <Lazy>
            <ToolsPage />
          </Lazy>
        ),
      },
      {
        path: 'ai/knowledge',
        element: (
          <Lazy>
            <KnowledgePage />
          </Lazy>
        ),
      },
      {
        path: 'ai/cron',
        element: (
          <Lazy>
            <CronPage />
          </Lazy>
        ),
      },
      {
        path: 'ai/sleep',
        element: (
          <Lazy>
            <SleepManagementPage />
          </Lazy>
        ),
      },
      {
        path: 'ai/memory',
        element: (
          <Lazy>
            <MemoryPage />
          </Lazy>
        ),
      },
      {
        path: 'docs',
        element: (
          <Lazy>
            <DocsPage />
          </Lazy>
        ),
      },
      {
        path: 'control/channels',
        element: (
          <Lazy>
            <ChannelsPage />
          </Lazy>
        ),
      },
      {
        path: 'control/users',
        element: (
          <Lazy>
            <UsersPage />
          </Lazy>
        ),
      },
      {
        path: 'control/system',
        element: (
          <Lazy>
            <SystemPage />
          </Lazy>
        ),
      },
    ],
  },
];
