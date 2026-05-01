import { Layout, theme } from 'antd';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Header from './Header';
import PageFadeIn from '@/components/ui/PageFadeIn';

export default function MainLayout() {
  const { token } = theme.useToken();
  const location = useLocation();

  return (
    <Layout style={{ height: '100vh' }}>
      <Sidebar />
      <Layout>
        <Header />
        <Layout.Content
          style={{
            padding: 20,
            overflow: 'auto',
            background: token.colorBgBase,
          }}
        >
          <PageFadeIn key={location.pathname}>
            <Outlet />
          </PageFadeIn>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
