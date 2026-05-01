import { theme, Avatar } from 'antd';
import { UserOutlined } from '@ant-design/icons';

export default function UserAvatar({
  state,
  size = 32,
}: {
  state: 'sleeping' | 'waiting' | 'reading';
  size?: number;
}) {
  const { token } = theme.useToken();
  const cls = `user-avatar-${state}`;
  return (
    <Avatar
      size={size}
      icon={<UserOutlined />}
      className={cls}
      style={{
        backgroundColor: state === 'sleeping' ? token.colorFill : token.colorPrimaryBg,
        color: state === 'sleeping' ? token.colorTextQuaternary : token.colorTextSecondary,
        flexShrink: 0,
        marginTop: 2,
      }}
    />
  );
}
