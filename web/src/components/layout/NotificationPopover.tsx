import { List, Button, Typography, Space, Empty, theme } from 'antd';
import {
  BellOutlined,
  AlertOutlined,
  InfoCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useNotificationStore } from '@/stores/notificationStore';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';

dayjs.extend(relativeTime);

const { Text } = Typography;

const SEVERITY_ICON: Record<string, React.ReactNode> = {
  critical: <AlertOutlined style={{ color: '#ff4d4f' }} />,
  warning: <WarningOutlined style={{ color: '#faad14' }} />,
  info: <InfoCircleOutlined style={{ color: '#1890ff' }} />,
};

export default function NotificationPopover() {
  const { notifications, loading, markRead, markAllRead, setPopoverOpen } = useNotificationStore();
  const navigate = useNavigate();
  const { token } = theme.useToken();

  const handleClick = (item: (typeof notifications)[0]) => {
    if (!item.is_read) {
      markRead(item.id);
    }
    setPopoverOpen(false);
    if (item.alert_id) {
      navigate('/ops/alerts');
    } else if (item.category === 'space_invite' || item.category === 'space_request') {
      navigate('/spaces');
    }
  };

  return (
    <div style={{ width: 360, maxHeight: 440, display: 'flex', flexDirection: 'column' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '8px 0',
          borderBottom: `1px solid ${token.colorBorder}`,
          marginBottom: 4,
        }}
      >
        <Space size={4}>
          <BellOutlined />
          <Text strong style={{ fontSize: 14 }}>
            通知
          </Text>
        </Space>
        <Button type="link" size="small" onClick={markAllRead}>
          全部已读
        </Button>
      </div>

      {notifications.length === 0 ? (
        <Empty
          description="暂无通知"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ margin: '20px 0' }}
        />
      ) : (
        <List
          loading={loading}
          dataSource={notifications}
          style={{ overflow: 'auto', maxHeight: 340 }}
          renderItem={(item) => (
            <List.Item
              onClick={() => handleClick(item)}
              style={{
                cursor: 'pointer',
                padding: '8px 12px',
                borderRadius: 8,
                marginBottom: 2,
                background: item.is_read ? 'transparent' : token.colorFillSecondary,
                transition: 'background 0.15s',
              }}
            >
              <List.Item.Meta
                avatar={
                  <span style={{ fontSize: 16, lineHeight: '24px' }}>
                    {SEVERITY_ICON[item.severity] || SEVERITY_ICON.info}
                  </span>
                }
                title={
                  <Space size={4}>
                    {!item.is_read && (
                      <span
                        style={{
                          width: 6,
                          height: 6,
                          borderRadius: '50%',
                          background: token.colorPrimary,
                          display: 'inline-block',
                        }}
                      />
                    )}
                    <Text style={{ fontSize: 13, fontWeight: item.is_read ? 400 : 500 }}>
                      {item.title}
                    </Text>
                  </Space>
                }
                description={
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {item.created_at ? dayjs(item.created_at).fromNow() : ''}
                  </Text>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );
}
