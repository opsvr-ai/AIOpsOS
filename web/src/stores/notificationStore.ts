import { create } from 'zustand';
import api from '@/services/api';

interface NotificationItem {
  id: string;
  title: string;
  message: string | null;
  severity: string;
  alert_id: string | null;
  category: string | null;
  is_read: boolean;
  created_at: string | null;
}

interface NotificationState {
  notifications: NotificationItem[];
  unreadCount: number;
  loading: boolean;
  popoverOpen: boolean;

  setPopoverOpen: (open: boolean) => void;
  fetchUnreadCount: () => Promise<void>;
  fetchNotifications: () => Promise<void>;
  markRead: (id: string) => Promise<void>;
  markAllRead: () => Promise<void>;
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  unreadCount: 0,
  loading: false,
  popoverOpen: false,

  setPopoverOpen: (open: boolean) => set({ popoverOpen: open }),

  fetchUnreadCount: async () => {
    try {
      const res = await api.get('/notifications/unread-count');
      set({ unreadCount: res.data?.unread ?? 0 });
    } catch {
      /* ignore */
    }
  },

  fetchNotifications: async () => {
    set({ loading: true });
    try {
      const res = await api.get('/notifications', { params: { page_size: 10 } });
      set({ notifications: res.data ?? [] });
    } catch {
      /* ignore */
    }
    set({ loading: false });
  },

  markRead: async (id: string) => {
    try {
      await api.post(`/notifications/${id}/read`);
      const { notifications, unreadCount } = get();
      set({
        notifications: notifications.map((n) => (n.id === id ? { ...n, is_read: true } : n)),
        unreadCount: Math.max(0, unreadCount - 1),
      });
    } catch {
      /* ignore */
    }
  },

  markAllRead: async () => {
    try {
      await api.post('/notifications/read-all');
      const { notifications } = get();
      set({
        notifications: notifications.map((n) => ({ ...n, is_read: true })),
        unreadCount: 0,
      });
    } catch {
      /* ignore */
    }
  },
}));
