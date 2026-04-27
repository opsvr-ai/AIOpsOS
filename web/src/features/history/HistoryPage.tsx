import { useEffect, useState, useCallback } from 'react';
import { Card, Table, Tag, Space, Typography, Input, App, Empty, theme } from 'antd';
import { HistoryOutlined, MessageOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface Session {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export default function HistoryPage() {
  const { token } = theme.useToken();
  const { message: msg } = App.useApp();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const fetch = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/sessions');
      setSessions(res.data ?? []);
    } catch {
      msg.error('加载失败');
    } finally {
      setLoading(false);
    }
  }, [msg]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const filtered = search
    ? sessions.filter((s) => s.title?.toLowerCase().includes(search.toLowerCase()))
    : sessions;

  const columns = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (v: string) => (
        <Space>
          <MessageOutlined style={{ color: token.colorPrimary }} />
          <span style={{ fontWeight: 500 }}>{v || '新对话'}</span>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (v: string) => (
        <Tag color={v === 'active' ? 'processing' : 'default'} style={{ borderRadius: 4 }}>
          {v}
        </Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
  ];

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
        }}
      >
        <Typography.Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          <HistoryOutlined style={{ marginRight: 8 }} />
          历史记录
        </Typography.Title>
        <Input.Search
          placeholder="搜索对话..."
          allowClear
          style={{ width: 240 }}
          onSearch={(v) => setSearch(v)}
          onChange={(e) => !e.target.value && setSearch('')}
        />
      </div>
      <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={filtered}
          columns={columns}
          rowKey="id"
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="middle"
          loading={loading}
          locale={{ emptyText: <Empty description="暂无历史记录" /> }}
        />
      </Card>
    </div>
  );
}
