import { useEffect, useState, useCallback } from 'react';
import { Card, Table, Tag, Space, Typography, App, Empty, theme } from 'antd';
import { TeamOutlined, UserOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface User {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  created_at: string;
}

export default function UsersPage() {
  const { token } = theme.useToken();
  const { message: msg } = App.useApp();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  const fetch = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/me');
      setUsers(res.data ? [res.data] : []);
    } catch {
      setUsers([]);
    } finally {
      setLoading(false);
    }
  }, [msg]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const columns = [
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      render: (v: string) => (
        <Space>
          <UserOutlined style={{ color: token.colorPrimary }} />
          <span style={{ fontWeight: 500 }}>{v}</span>
        </Space>
      ),
    },
    { title: '邮箱', dataIndex: 'email', key: 'email' },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (v: boolean) => (
        <Tag color={v ? 'success' : 'default'} style={{ borderRadius: 4 }}>
          {v ? '正常' : '禁用'}
        </Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <Typography.Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          <TeamOutlined style={{ marginRight: 8 }} />
          身份与权限
        </Typography.Title>
      </div>
      <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={users}
          columns={columns}
          rowKey="id"
          pagination={false}
          size="middle"
          loading={loading}
          locale={{ emptyText: <Empty description="暂无用户数据" /> }}
        />
      </Card>
    </div>
  );
}
