import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Layout, Menu, theme, Button } from 'antd';
import { StarOutlined } from '@ant-design/icons';
import MyAssistantDrawer from '@/features/assistant/MyAssistantDrawer';
import {
  DashboardOutlined,
  MessageOutlined,
  AlertOutlined,
  ExperimentOutlined,
  ThunderboltOutlined,
  RobotOutlined,
  ToolOutlined,
  BookOutlined,
  SendOutlined,
  TeamOutlined,
  SettingOutlined,
  AppstoreOutlined,
  ClockCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  HddOutlined,
  MoonOutlined,
  SafetyCertificateOutlined,
  FileTextOutlined,
  ApiOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import { useThemeStore } from '@/stores/themeStore';
import { useAuthStore } from '@/stores/authStore';

const baseMenuItems = [
  {
    key: 'ops-group',
    icon: <DashboardOutlined />,
    label: '运维中心',
    children: [
      { key: '/ops', icon: <DashboardOutlined />, label: '总览' },
      { key: '/ops/chat', icon: <MessageOutlined />, label: '对话' },
      { key: '/ops/alerts', icon: <AlertOutlined />, label: '告警中心' },
      { key: '/ops/scenarios', icon: <ExperimentOutlined />, label: '场景运维' },
      { key: '/ops/datacenter', icon: <AppstoreOutlined />, label: '数据接入' },
      { key: '/ops/events', icon: <DatabaseOutlined />, label: '事件接入' },
      { key: '/ops/automation', icon: <ThunderboltOutlined />, label: '自动化' },
      { key: '/ops/cmdb', icon: <HddOutlined />, label: 'CMDB' },
      { key: '/ops/logs', icon: <FileTextOutlined />, label: '日志检索' },
      { key: '/ops/itsm', icon: <MessageOutlined />, label: 'ITSM' },
    ],
  },
  {
    key: 'ai-group',
    icon: <RobotOutlined />,
    label: 'AI中心',
    children: [
      { key: '/ai/agents', icon: <RobotOutlined />, label: '智能体' },
      { key: '/ai/tools', icon: <ToolOutlined />, label: '工具市场' },
      { key: '/ai/knowledge', icon: <BookOutlined />, label: '知识库' },
      { key: '/ai/cron', icon: <ClockCircleOutlined />, label: '定时任务' },
      { key: '/ai/sleep', icon: <MoonOutlined />, label: '睡眠管理' },
      { key: '/ai/memory', icon: <HddOutlined />, label: '记忆管理' },
      { key: 'assistant', icon: <StarOutlined />, label: '我的助理' },
    ],
  },
];

const adminMenuItems = [
  {
    key: 'control-group',
    icon: <AppstoreOutlined />,
    label: '控制中心',
    children: [
      { key: '/spaces', icon: <TeamOutlined />, label: '空间管理' },
      { key: '/control/channels', icon: <SendOutlined />, label: '消息渠道' },
      { key: '/control/model-providers', icon: <ApiOutlined />, label: '模型配置' },
      { key: '/control/permissions', icon: <SafetyCertificateOutlined />, label: '权限矩阵' },
      { key: '/control/users', icon: <TeamOutlined />, label: '用户管理' },
      { key: '/control/system', icon: <SettingOutlined />, label: '系统管理' },
      { key: '/control/logs', icon: <FileTextOutlined />, label: '日志查看' },
    ],
  },
];

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { token } = theme.useToken();
  const mode = useThemeStore((s) => s.mode);
  const isAdmin = useAuthStore((s) => s.user?.roles?.includes('admin'));
  const [collapsed, setCollapsed] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);

  const selectedKeys = [location.pathname === '/' ? '/ops' : location.pathname];
  const section = location.pathname.split('/')[1] || 'ops';
  const openKey = section + '-group';

  const menuItems = isAdmin ? [...baseMenuItems, ...adminMenuItems] : baseMenuItems;

  return (
    <Layout.Sider
      width={240}
      collapsedWidth={64}
      collapsible
      collapsed={collapsed}
      trigger={null}
      theme={mode === 'dark' ? 'dark' : 'light'}
      style={{
        background: token.colorBgContainer,
        borderRight: `1px solid ${token.colorBorder}`,
      }}
    >
      {/* Logo */}
      <div
        style={{
          height: 56,
          display: 'flex',
          alignItems: 'center',
          gap: collapsed ? 0 : 10,
          padding: collapsed ? '0 16px' : '0 20px',
          borderBottom: `1px solid ${token.colorBorder}`,
          justifyContent: collapsed ? 'center' : 'flex-start',
        }}
      >
        <div
          style={{
            width: 28,
            height: 28,
            borderRadius: 8,
            background: token.colorPrimary,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontSize: 14,
            fontWeight: 700,
            flexShrink: 0,
          }}
        >
          A
        </div>
        {!collapsed && (
          <span
            style={{ fontWeight: 700, fontSize: 16, color: token.colorText, letterSpacing: 0.5 }}
          >
            AIOpsOS
          </span>
        )}
      </div>

      {/* Collapse toggle */}
      <div style={{ padding: '4px 8px', textAlign: collapsed ? 'center' : 'right' }}>
        <Button
          type="text"
          size="small"
          icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          onClick={() => setCollapsed(!collapsed)}
          style={{ color: token.colorTextTertiary }}
        />
      </div>

      {/* Menu */}
      <Menu
        theme={mode === 'dark' ? 'dark' : 'light'}
        mode="inline"
        selectedKeys={selectedKeys}
        defaultOpenKeys={collapsed ? [] : [openKey]}
        items={menuItems}
        onClick={({ key }) => {
          if (key === 'assistant') {
            setAssistantOpen(true);
            return;
          }
          navigate(key);
        }}
        style={{
          background: 'transparent',
          borderRight: 'none',
          padding: '8px 0',
        }}
      />
      <MyAssistantDrawer open={assistantOpen} onClose={() => setAssistantOpen(false)} />
    </Layout.Sider>
  );
}
