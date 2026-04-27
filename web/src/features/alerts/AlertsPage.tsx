import { useEffect, useState, useCallback } from 'react';
import { Card, Table, Tag, Button, Space, Typography, Select, Input, App, theme } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface AlertItem {
  id: string;
  title: string;
  severity: string;
  status: string;
  source: string;
  description?: string;
  created_at: string;
}

export default function AlertsPage() {
  const { token } = theme.useToken();
  const { message: msg } = App.useApp();
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [severityFilter, setSeverityFilter] = useState<string | undefined>();
  const [search, setSearch] = useState('');

  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = { page: '1', page_size: '50', sort_order: 'desc' };
      if (statusFilter) params.status = statusFilter;
      if (severityFilter) params.severity = severityFilter;
      if (search) params.search = search;
      const qs = new URLSearchParams(params).toString();
      const res = await api.get(`/alerts?${qs}`);
      setAlerts(res.data ?? []);
    } catch {
      msg.error('加载告警失败');
    } finally {
      setLoading(false);
    }
  }, [statusFilter, severityFilter, search, msg]);

  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  const handleAction = async (id: string, action: string) => {
    try {
      await api.post(`/alerts/${id}/action`, { action });
      msg.success(action === 'confirm' ? '已确认' : '已忽略');
      fetchAlerts();
    } catch {
      msg.error('操作失败');
    }
  };

  const severityColor = (sev: string) => {
    switch (sev) {
      case 'critical':
        return '#DC2626';
      case 'warning':
        return '#F59E0B';
      case 'info':
        return '#3B82F6';
      default:
        return token.colorTextTertiary;
    }
  };

  const columns = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (v: string, r: AlertItem) => (
        <Space>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              background: severityColor(r.severity),
              display: 'inline-block',
              flexShrink: 0,
            }}
          />
          <span style={{ fontWeight: r.severity === 'critical' ? 600 : 400 }}>{v}</span>
        </Space>
      ),
    },
    {
      title: '级别',
      dataIndex: 'severity',
      key: 'severity',
      width: 90,
      render: (v: string) => (
        <Tag color={severityColor(v)} style={{ borderRadius: 4 }}>
          {v}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (v: string) => (
        <Tag color={v === 'active' ? 'processing' : 'default'} style={{ borderRadius: 4 }}>
          {v === 'active' ? '进行中' : v === 'confirmed' ? '已确认' : '已忽略'}
        </Tag>
      ),
    },
    { title: '来源', dataIndex: 'source', key: 'source', width: 120 },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_: unknown, r: AlertItem) =>
        r.status === 'active' ? (
          <Space>
            <Button
              size="small"
              type="text"
              icon={<CheckCircleOutlined />}
              style={{ color: token.colorSuccess }}
              onClick={() => handleAction(r.id, 'confirm')}
            >
              确认
            </Button>
            <Button
              size="small"
              type="text"
              icon={<CloseCircleOutlined />}
              style={{ color: token.colorTextTertiary }}
              onClick={() => handleAction(r.id, 'dismiss')}
            >
              忽略
            </Button>
          </Space>
        ) : null,
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
          告警中心
        </Typography.Title>
        <Space>
          <Input.Search
            placeholder="搜索告警..."
            allowClear
            style={{ width: 200 }}
            onSearch={(v) => setSearch(v)}
          />
          <Select
            placeholder="级别"
            allowClear
            style={{ width: 100 }}
            value={severityFilter}
            onChange={setSeverityFilter}
            options={[
              { value: 'critical', label: '严重' },
              { value: 'warning', label: '警告' },
              { value: 'info', label: '信息' },
            ]}
          />
          <Select
            placeholder="状态"
            allowClear
            style={{ width: 100 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: 'active', label: '进行中' },
              { value: 'confirmed', label: '已确认' },
              { value: 'dismissed', label: '已忽略' },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={fetchAlerts}>
            刷新
          </Button>
        </Space>
      </div>
      <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={alerts}
          columns={columns}
          rowKey="id"
          pagination={{ pageSize: 15, showSizeChanger: false }}
          size="middle"
          loading={loading}
        />
      </Card>
    </div>
  );
}
