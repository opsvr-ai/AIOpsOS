import { useEffect } from 'react';
import { Button, Badge, Popover, theme } from 'antd';
import { BellOutlined } from '@ant-design/icons';
import { useNotificationStore } from '@/stores/notificationStore';
import NotificationPopover from './NotificationPopover';

export default function NotificationBell() {
  const { token } = theme.useToken();
  const {
    unreadCount, popoverOpen, setPopoverOpen,
    fetchUnreadCount, fetchNotifications,
  } = useNotificationStore();

  useEffect(() => {
    fetchUnreadCount();
    const interval = setInterval(fetchUnreadCount, 30000);
    return () => clearInterval(interval);
  }, [fetchUnreadCount]);

  const handleOpen = (visible: boolean) => {
    setPopoverOpen(visible);
    if (visible) {
      fetchNotifications();
    }
  };

  return (
    <Popover
      content={<NotificationPopover />}
      trigger="click"
      open={popoverOpen}
      onOpenChange={handleOpen}
      placement="bottomRight"
      arrow={false}
    >
      <Badge count={unreadCount} overflowCount={99} size="small" offset={[-2, 4]}>
        <Button
          type="text"
          icon={<BellOutlined />}
          style={{ color: token.colorTextSecondary, fontSize: 16 }}
          title="通知"
        />
      </Badge>
    </Popover>
  );
}
