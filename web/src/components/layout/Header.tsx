import { useState } from 'react';
import { Layout, Button, Space, Dropdown, Avatar, theme } from 'antd';
import {
  UserOutlined,
  LogoutOutlined,
  SunOutlined,
  MoonOutlined,
  RobotOutlined,
  ReadOutlined,
  BugOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';
import { useThemeStore } from '@/stores/themeStore';
import { useNavigate } from 'react-router-dom';
import NotificationBell from './NotificationBell';
import SpaceSelector from './SpaceSelector';
import FeedbackModal from '@/features/feedback/FeedbackModal';

export default function Header() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const { mode, toggle } = useThemeStore();
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  return (
    <Layout.Header
      style={{
        background: token.colorBgContainer,
        borderBottom: `1px solid ${token.colorBorder}`,
        padding: '0 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'flex-end',
        height: 56,
      }}
    >
      <Space size="small">
        <SpaceSelector />
        <Button
          type="text"
          icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />}
          onClick={toggle}
          style={{ color: token.colorTextSecondary, fontSize: 16 }}
        />
        <Button
          type="text"
          icon={<ReadOutlined />}
          onClick={() => navigate('/docs')}
          style={{ color: token.colorTextSecondary, fontSize: 16 }}
          title="文档中心"
        />
        <Button
          type="text"
          icon={<RobotOutlined />}
          onClick={() => navigate('/ai/agents')}
          style={{ color: token.colorTextSecondary, fontSize: 16 }}
        />
        <Button
          type="text"
          icon={<BugOutlined />}
          onClick={() => setFeedbackOpen(true)}
          style={{ color: token.colorTextSecondary, fontSize: 16 }}
          title="需求反馈"
        />
        <NotificationBell />
        <Dropdown
          menu={{
            items: [
              {
                key: 'profile',
                icon: <UserOutlined />,
                label: '个人信息',
                onClick: () => navigate('/profile'),
              },
              { type: 'divider' },
              {
                key: 'logout',
                icon: <LogoutOutlined />,
                label: '注销',
                onClick: () => {
                  logout();
                  navigate('/login');
                },
              },
            ],
          }}
          placement="bottomRight"
        >
          <Button
            type="text"
            style={{
              color: token.colorTextSecondary,
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <Avatar
              size={24}
              icon={<UserOutlined />}
              style={{ backgroundColor: token.colorPrimary, fontSize: 12 }}
            />
            <span style={{ fontSize: 13 }}>{user?.username ?? '未登录'}</span>
          </Button>
        </Dropdown>
      </Space>
      <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
    </Layout.Header>
  );
}
