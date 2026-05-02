import { useState, useEffect, useCallback } from 'react';
import {
  Table, Tabs, Button, Tag, Space, Typography, Input, Select, App,
} from 'antd';
import {
  SyncOutlined, CheckOutlined, CloseOutlined, DatabaseOutlined,
  ReloadOutlined, SearchOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

const { Title } = Typography;

interface CmdbNode {
  id: string;
  ci_type: string;
  name: string;
  external_id: string;
  source: string;
  properties: Record<string, unknown>;
  synced_at: string | null;
}

interface ReviewItem {
  id: string;
  sync_log_id: string | null;
  review_type: string;
  source_data: Record<string, unknown>;
  transformed_data: Record<string, unknown>;
  llm_confidence: number;
  llm_reason: string;
  diff_summary: Record<string, unknown> | null;
  status: string;
  reviewer: string | null;
  review_note: string | null;
  created_at: string | null;
}

interface SyncLog {
  id: string;
  datasource_id: string | null;
  mode: string;
  status: string;
  nodes_created: number;
  nodes_updated: number;
  nodes_deleted: number;
  edges_count: number;
  review_count: number;
  started_at: string | null;
  finished_at: string | null;
}

const CI_TYPE_COLORS: Record<string, string> = {
  server: 'blue', app: 'green', db: 'red', vip: 'purple',
  lb: 'orange', rack: 'cyan', container: 'geekblue', unknown: 'default',
};

const STATUS_COLORS: Record<string, string> = {
  pending: 'orange', approved: 'green', rejected: 'red',
  running: 'blue', success: 'green', failed: 'red',
};

export default function CmdbPage() {
  const { message: msg } = App.useApp();

  const [activeTab, setActiveTab] = useState('nodes');
  const [nodes, setNodes] = useState<CmdbNode[]>([]);
  const [nodesTotal, setNodesTotal] = useState(0);
  const [nodesPage, setNodesPage] = useState(1);
  const [nodesLoading, setNodesLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [ciType, setCiType] = useState<string | undefined>();

  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const [reviewTotal, setReviewTotal] = useState(0);
  const [reviewPage, setReviewPage] = useState(1);
  const [reviewLoading, setReviewLoading] = useState(false);

  const [syncLogs, setSyncLogs] = useState<SyncLog[]>([]);
  const [syncLogsTotal, setSyncLogsTotal] = useState(0);
  const [syncLogsPage, setSyncLogsPage] = useState(1);
  const [syncLoading, setSyncLoading] = useState(false);

  const [syncing, setSyncing] = useState(false);

  const fetchNodes = useCallback(async () => {
    setNodesLoading(true);
    try {
      const params: Record<string, unknown> = { page: nodesPage, page_size: 50 };
      if (search) params.search = search;
      if (ciType) params.ci_type = ciType;
      const resp = await api.get('/cmdb/nodes', { params });
      setNodes(resp.data.items ?? []);
      setNodesTotal(resp.data.total ?? 0);
    } catch {
      msg.error('获取CMDB节点失败');
    }
    setNodesLoading(false);
  }, [nodesPage, search, ciType, msg]);

  const fetchReviewItems = useCallback(async () => {
    setReviewLoading(true);
    try {
      const resp = await api.get('/cmdb/review-items', {
        params: { page: reviewPage, page_size: 20 },
      });
      setReviewItems(resp.data.items ?? []);
      setReviewTotal(resp.data.total ?? 0);
    } catch {
      msg.error('获取审核项失败');
    }
    setReviewLoading(false);
  }, [reviewPage, msg]);

  const fetchSyncLogs = useCallback(async () => {
    setSyncLoading(true);
    try {
      const resp = await api.get('/cmdb/sync-logs', {
        params: { page: syncLogsPage, page_size: 20 },
      });
      setSyncLogs(resp.data.items ?? []);
      setSyncLogsTotal(resp.data.total ?? 0);
    } catch {
      msg.error('获取同步日志失败');
    }
    setSyncLoading(false);
  }, [syncLogsPage, msg]);

  useEffect(() => {
    if (activeTab === 'nodes') fetchNodes();
    else if (activeTab === 'review') fetchReviewItems();
    else if (activeTab === 'sync') fetchSyncLogs();
  }, [activeTab, fetchNodes, fetchReviewItems, fetchSyncLogs]);

  const handleApprove = async (id: string) => {
    try {
      await api.post(`/cmdb/review-items/${id}/approve`, { reviewer: 'admin' });
      msg.success('已通过');
      fetchReviewItems();
    } catch {
      msg.error('操作失败');
    }
  };

  const handleReject = async (id: string) => {
    try {
      await api.post(`/cmdb/review-items/${id}/reject`, { reviewer: 'admin' });
      msg.success('已拒绝');
      fetchReviewItems();
    } catch {
      msg.error('操作失败');
    }
  };

  const handleSync = async (datasourceId: string) => {
    setSyncing(true);
    try {
      await api.post(`/datasources/${datasourceId}/sync`, { mode: 'incremental' });
      msg.success('同步已触发');
      fetchSyncLogs();
    } catch {
      msg.error('同步失败');
    }
    setSyncing(false);
  };

  const nodeColumns = [
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '类型', dataIndex: 'ci_type', key: 'ci_type', width: 100,
      render: (t: string) => <Tag color={CI_TYPE_COLORS[t] || 'default'}>{t || 'unknown'}</Tag>,
    },
    { title: 'External ID', dataIndex: 'external_id', key: 'external_id', ellipsis: true, width: 180 },
    { title: '来源', dataIndex: 'source', key: 'source', width: 100 },
    {
      title: '属性', dataIndex: 'properties', key: 'properties', width: 120,
      render: (p: Record<string, unknown>) => (
        <Typography.Text ellipsis style={{ maxWidth: 100, fontSize: 12 }} type="secondary">
          {p ? Object.keys(p).join(', ') : '-'}
        </Typography.Text>
      ),
    },
  ];

  const reviewColumns = [
    { title: '类型', dataIndex: 'review_type', key: 'review_type', width: 90,
      render: (t: string) => <Tag>{t === 'semantic' ? '语义校验' : '异常检测'}</Tag>,
    },
    { title: '置信度', dataIndex: 'llm_confidence', key: 'llm_confidence', width: 80,
      render: (c: number) => (
        <Tag color={c >= 80 ? 'green' : c >= 50 ? 'orange' : 'red'}>{c}%</Tag>
      ),
    },
    { title: '原因', dataIndex: 'llm_reason', key: 'llm_reason', ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (s: string) => <Tag color={STATUS_COLORS[s]}>{s}</Tag>,
    },
    { title: '操作', key: 'actions', width: 160,
      render: (_: unknown, item: ReviewItem) => (
        item.status === 'pending' ? (
          <Space size={4}>
            <Button size="small" type="primary" icon={<CheckOutlined />}
              onClick={() => handleApprove(item.id)}>通过</Button>
            <Button size="small" danger icon={<CloseOutlined />}
              onClick={() => handleReject(item.id)}>拒绝</Button>
          </Space>
        ) : <Typography.Text type="secondary">-</Typography.Text>
      ),
    },
  ];

  const syncLogColumns = [
    { title: '模式', dataIndex: 'mode', key: 'mode', width: 90 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (s: string) => <Tag color={STATUS_COLORS[s] || 'default'}>{s}</Tag>,
    },
    { title: '新增', dataIndex: 'nodes_created', key: 'nodes_created', width: 60 },
    { title: '更新', dataIndex: 'nodes_updated', key: 'nodes_updated', width: 60 },
    { title: '删除', dataIndex: 'nodes_deleted', key: 'nodes_deleted', width: 60 },
    { title: '边数', dataIndex: 'edges_count', key: 'edges_count', width: 60 },
    { title: '审核项', dataIndex: 'review_count', key: 'review_count', width: 70 },
    {
      title: '开始时间', dataIndex: 'started_at', key: 'started_at', width: 170,
      render: (t: string | null) => t ? new Date(t).toLocaleString() : '-',
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          <DatabaseOutlined style={{ marginRight: 8 }} />
          CMDB 配置管理
        </Title>
      </div>

      <Tabs activeKey={activeTab} onChange={setActiveTab}
        items={[
          {
            key: 'nodes',
            label: '节点管理',
            children: (
              <div>
                <Space wrap style={{ marginBottom: 16 }}>
                  <Input.Search
                    placeholder="搜索名称/External ID"
                    allowClear
                    style={{ width: 240 }}
                    prefix={<SearchOutlined />}
                    onSearch={(v) => { setSearch(v); setNodesPage(1); }}
                  />
                  <Select
                    allowClear
                    placeholder="CI类型"
                    style={{ width: 130 }}
                    value={ciType}
                    onChange={(v) => { setCiType(v); setNodesPage(1); }}
                    options={[
                      { value: 'server', label: '服务器' },
                      { value: 'app', label: '应用' },
                      { value: 'db', label: '数据库' },
                      { value: 'vip', label: 'VIP' },
                      { value: 'lb', label: '负载均衡' },
                      { value: 'rack', label: '机柜' },
                    ]}
                  />
                  <Button icon={<ReloadOutlined />} onClick={fetchNodes}>刷新</Button>
                </Space>
                <Table
                  dataSource={nodes}
                  columns={nodeColumns}
                  rowKey="id"
                  loading={nodesLoading}
                  size="middle"
                  pagination={{
                    current: nodesPage,
                    total: nodesTotal,
                    pageSize: 50,
                    onChange: (p) => setNodesPage(p),
                    showSizeChanger: false,
                  }}
                />
              </div>
            ),
          },
          {
            key: 'review',
            label: '审核队列',
            children: (
              <Table
                dataSource={reviewItems}
                columns={reviewColumns}
                rowKey="id"
                loading={reviewLoading}
                size="middle"
                expandable={{
                  expandedRowRender: (item: ReviewItem) => (
                    <div style={{ padding: 8 }}>
                      <Typography.Text strong>源数据: </Typography.Text>
                      <pre style={{ fontSize: 12, maxHeight: 200, overflow: 'auto' }}>
                        {JSON.stringify(item.source_data, null, 2)}
                      </pre>
                      <Typography.Text strong>转换后: </Typography.Text>
                      <pre style={{ fontSize: 12, maxHeight: 200, overflow: 'auto' }}>
                        {JSON.stringify(item.transformed_data, null, 2)}
                      </pre>
                    </div>
                  ),
                }}
                pagination={{
                  current: reviewPage,
                  total: reviewTotal,
                  pageSize: 20,
                  onChange: (p) => setReviewPage(p),
                  showSizeChanger: false,
                }}
              />
            ),
          },
          {
            key: 'sync',
            label: '同步日志',
            children: (
              <div>
                <Space style={{ marginBottom: 16 }}>
                  <Button icon={<ReloadOutlined />} onClick={fetchSyncLogs}>刷新</Button>
                  <Button type="primary" icon={<SyncOutlined />} loading={syncing}
                    onClick={() => handleSync('')}>触发同步</Button>
                </Space>
                <Table
                  dataSource={syncLogs}
                  columns={syncLogColumns}
                  rowKey="id"
                  loading={syncLoading}
                  size="middle"
                  pagination={{
                    current: syncLogsPage,
                    total: syncLogsTotal,
                    pageSize: 20,
                    onChange: (p) => setSyncLogsPage(p),
                    showSizeChanger: false,
                  }}
                />
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}
