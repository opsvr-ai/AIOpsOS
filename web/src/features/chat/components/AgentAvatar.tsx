import { theme, Avatar } from 'antd';
import { RobotOutlined, ToolOutlined } from '@ant-design/icons';

export default function AgentAvatar({
  state,
  size = 36,
}: {
  state: 'idle' | 'thinking' | 'planning' | 'executing';
  size?: number;
}) {
  const { token } = theme.useToken();
  const cls = state === 'idle' ? 'agent-avatar-wave' : `agent-avatar-${state}`;

  if (state === 'executing') {
    return (
      <Avatar
        size={size}
        icon={<ToolOutlined />}
        className={cls}
        style={{
          backgroundColor: token.colorWarningBg,
          color: token.colorWarning,
          flexShrink: 0,
          marginTop: 2,
          border: `2px solid ${token.colorWarningBorder}`,
        }}
      />
    );
  }
  return (
    <Avatar
      size={size}
      icon={<RobotOutlined />}
      className={cls}
      style={{
        background: `linear-gradient(135deg, ${token.colorPrimary}, ${token.colorPrimaryActive})`,
        flexShrink: 0,
        marginTop: 2,
        boxShadow: `0 2px 8px ${token.colorPrimary}40`,
      }}
    />
  );
}
