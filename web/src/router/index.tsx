import { lazy, Suspense } from 'react';
import { Spin } from 'antd';

const MainLayout = lazy(() => import('@/components/layout/MainLayout'));
const DashboardPage = lazy(() => import('@/features/dashboard/DashboardPage'));
const ChatPage = lazy(() => import('@/features/chat/ChatPage'));
const AlertsPage = lazy(() => import('@/features/alerts/AlertsPage'));
const ScenarioPage = lazy(() => import('@/features/scenarios/ScenarioPage'));
const DataSourcePage = lazy(() => import('@/features/datacenter/DataSourcePage'));
const EventsPage = lazy(() => import('@/features/events/EventsPage'));
const AutomationPage = lazy(() => import('@/features/automation/AutomationPage'));
const SleepManagementPage = lazy(() => import('@/features/sleep/SleepManagementPage'));
const AgentsPage = lazy(() => import('@/features/agents/AgentsPage'));
const ToolsPage = lazy(() => import('@/features/tools/ToolsPage'));
const KnowledgePage = lazy(() => import('@/features/knowledge/KnowledgePage'));
const WikiPage = lazy(() => import('@/features/knowledge/WikiPage'));
const ChannelsPage = lazy(() => import('@/features/channels/ChannelsPage'));
const UsersPage = lazy(() => import('@/features/users/UsersPage'));
const CronPage = lazy(() => import('@/features/cron/CronPage'));
const SystemPage = lazy(() => import('@/features/system/SystemPage'));
const PermissionMatrix = lazy(() => import('@/features/permissions/PermissionMatrix'));
const MemoryPage = lazy(() => import('@/features/memory/MemoryPage'));
const DocsPage = lazy(() => import('@/features/docs/DocsPage'));
const FeedbackPage = lazy(() => import('@/features/feedback/FeedbackPage'));
const LogsPage = lazy(() => import('@/features/logs/LogsPage'));
const CmdbPage = lazy(() => import('@/features/cmdb/CmdbPage'));
const LogIngestionPage = lazy(() => import('@/features/logs/LogIngestionPage'));
const ItsmPage = lazy(() => import('@/features/itsm/ItsmPage'));
const ReportListPage = lazy(() => import('@/features/reports/ReportListPage'));
const ReportViewerPage = lazy(() => import('@/features/reports/ReportViewerPage'));
const ModelProvidersPage = lazy(() => import('@/features/model-providers/ModelProvidersPage'));
const SpacesPage = lazy(() => import('@/features/spaces/SpacesPage'));
const SpaceDetailPage = lazy(() => import('@/features/spaces/SpaceDetailPage'));
const LoginPage = lazy(() => import('@/features/LoginPage'));
const RegisterPage = lazy(() => import('@/features/RegisterPage'));
const InviteAcceptPage = lazy(() => import('@/features/InviteAcceptPage'));
const OnboardingPage = lazy(() => import('@/features/onboarding/OnboardingPage'));
const ProfilePage = lazy(() => import('@/features/ProfilePage'));
const RequireAdmin = lazy(() => import('@/components/auth/RequireAdmin'));

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
    path: '/onboarding',
    element: (
      <Lazy>
        <OnboardingPage />
      </Lazy>
    ),
  },
  {
    path: '/login',
    element: (
      <Lazy>
        <LoginPage />
      </Lazy>
    ),
  },
  {
    path: '/register',
    element: (
      <Lazy>
        <RegisterPage />
      </Lazy>
    ),
  },
  {
    path: '/invite/:token',
    element: (
      <Lazy>
        <InviteAcceptPage />
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
        path: 'ops/events',
        element: (
          <Lazy>
            <EventsPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/datacenter',
        element: (
          <Lazy>
            <DataSourcePage />
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
        path: 'ops/cmdb',
        element: (
          <Lazy>
            <CmdbPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/logs',
        element: (
          <Lazy>
            <LogIngestionPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/itsm',
        element: (
          <Lazy>
            <ItsmPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/reports',
        element: (
          <Lazy>
            <ReportListPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/reports/:reportId',
        element: (
          <Lazy>
            <ReportViewerPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/cmdb',
        element: (
          <Lazy>
            <CmdbPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/logs',
        element: (
          <Lazy>
            <LogIngestionPage />
          </Lazy>
        ),
      },
      {
        path: 'ops/itsm',
        element: (
          <Lazy>
            <ItsmPage />
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
        path: 'ai/knowledge/wiki',
        element: (
          <Lazy>
            <WikiPage />
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
        path: 'profile',
        element: (
          <Lazy>
            <ProfilePage />
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
        path: 'control',
        element: (
          <Lazy>
            <RequireAdmin />
          </Lazy>
        ),
        children: [
          {
            path: 'channels',
            element: (
              <Lazy>
                <ChannelsPage />
              </Lazy>
            ),
          },
          {
            path: 'users',
            element: (
              <Lazy>
                <UsersPage />
              </Lazy>
            ),
          },
          {
            path: 'permissions',
            element: (
              <Lazy>
                <PermissionMatrix />
              </Lazy>
            ),
          },
          {
            path: 'system',
            element: (
              <Lazy>
                <SystemPage />
              </Lazy>
            ),
          },
          {
            path: 'feedback',
            element: (
              <Lazy>
                <FeedbackPage />
              </Lazy>
            ),
          },
          {
            path: 'logs',
            element: (
              <Lazy>
                <LogsPage />
              </Lazy>
            ),
          },
          {
            path: 'model-providers',
            element: (
              <Lazy>
                <ModelProvidersPage />
              </Lazy>
            ),
          },
        ],
      },
      {
        path: 'spaces',
        element: (
          <Lazy>
            <RequireAdmin />
          </Lazy>
        ),
        children: [
          {
            index: true,
            element: (
              <Lazy>
                <SpacesPage />
              </Lazy>
            ),
          },
          {
            path: ':spaceId',
            element: (
              <Lazy>
                <SpaceDetailPage />
              </Lazy>
            ),
          },
        ],
      },
    ],
  },
];
