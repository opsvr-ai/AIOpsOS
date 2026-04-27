import { Layout, Button, Space, Dropdown, Avatar, theme } from 'antd';
import {
  UserOutlined,
  LogoutOutlined,
  SunOutlined,
  MoonOutlined,
  RobotOutlined,
  ReadOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';
import { useThemeStore } from '@/stores/themeStore';
import { useNavigate } from 'react-router-dom';

export default function Header() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const { mode, toggle } = useThemeStore();
  const navigate = useNavigate();
  const { token } = theme.useToken();

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
        <Dropdown
          menu={{
            items: [
              {
                key: 'logout',
                icon: <LogoutOutlined />,
                label: '注销',
                onClick: logout,
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
    </Layout.Header>
  );
}
