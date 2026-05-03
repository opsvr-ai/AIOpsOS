import { useEffect, useState, useCallback, useRef } from 'react';
import {
  Typography,
  Select,
  Button,
  Space,
  Card,
  Input,
  Modal,
  Form,
  App,
  Segmented,
  Empty,
  Spin,
  Table,
  Tag,
  Pagination,
  theme,
  Row,
  Col,
} from 'antd';
import { ReloadOutlined, PlusOutlined, SearchOutlined, BookOutlined } from '@ant-design/icons';
import api from '@/services/api';
import AlertCard from './AlertCard';
import AlertDetailDrawer from './AlertDetailDrawer';

const { Title } = Typography;

// ── Types ──────────────────────────────────────────────────

interface AlertItem {
  id: string;
  title: string;
  severity: string;
  status: string;
  source: string;
  raw_event: Record<string, unknown>;
  enriched_context: Record<string, unknown>;
  analysis_result: Record<string, unknown>;
  confirmed_by: string | null;
  confirmed_at: string | null;
  event_id: string | null;
  knowledge_entry_id: string | null;
  assigned_to: string | null;
  created_at: string;
  updated_at: string;
}

const SEVERITY_COLOR: Record<string, string> = {
  critical: '#ff4d4f',
  warning: '#faad14',
  info: '#1890ff',
};
const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  analyzing: 'processing',
  awaiting_review: 'warning',
  confirmed: 'success',
  dismissed: 'error',
  closed: 'default',
};
const STATUS_LABEL: Record<string, string> = {
  pending: '待处理',
  analyzing: '分析中',
  awaiting_review: '待审核',
  confirmed: '已确认',
  dismissed: '已忽略',
  closed: '已关闭',
};

// ── Component ──────────────────────────────────────────────

export default function AlertsPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();

  // Data
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [severityFilter, setSeverityFilter] = useState<string | undefined>(undefined);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const searchTimerRef = useRef<ReturnType<typeof setTimeout>>();

  // View
  const [viewMode, setViewMode] = useState<'card' | 'table'>('card');

  // Selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Detail drawer
  const [detailAlert, setDetailAlert] = useState<AlertItem | null>(null);

  // Create modal
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [creating, setCreating] = useState(false);

  // Pagination
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [totalAlerts, setTotalAlerts] = useState(0);

  useEffect(() => {
    searchTimerRef.current = setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => clearTimeout(searchTimerRef.current);
  }, [search]);

  // ── Fetch ──────────────────────────────────────────────

  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = {
        page,
        page_size: pageSize,
        sort_order: 'desc',
      };
      if (statusFilter) params.status = statusFilter;
      if (severityFilter) params.severity = severityFilter;
      if (debouncedSearch) params.search = debouncedSearch;
      const res = await api.get('/alerts', { params });
      const body = res.data;
      setAlerts(Array.isArray(body) ? body : (body?.items ?? []));
      setTotalAlerts(body?.total ?? (Array.isArray(body) ? body.length : 0));
    } catch {
      msg.error('加载告警失败');
    } finally {
      setLoading(false);
    }
  }, [msg, statusFilter, severityFilter, debouncedSearch, page, pageSize]);

  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  // ── Actions ────────────────────────────────────────────

  const handleAction = async (id: string, action: string) => {
    try {
      await api.post(`/alerts/${id}/action`, { action });
      if (action === 'confirm') {
        // Auto-create knowledge entry from confirmed alert
        try {
          await api.post(`/knowledge/from-alert/${id}`);
        } catch {
          // Knowledge extraction is best-effort, don't block confirm
        }
      }
      msg.success(
        action === 'confirm' ? '已确认并入库' : action === 'dismiss' ? '已忽略' : '操作成功',
      );
      fetchAlerts();
    } catch {
      msg.error('操作失败');
    }
  };

  const handleBatchAction = async (action: string) => {
    if (selectedIds.size === 0) return;
    try {
      await api.post('/alerts/batch-action', { alert_ids: [...selectedIds], action });
      msg.success(`已${action === 'confirm' ? '确认' : '忽略'} ${selectedIds.size} 条告警`);
      setSelectedIds(new Set());
      fetchAlerts();
    } catch {
      msg.error('批量操作失败');
    }
  };

  const handleSelect = (id: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const handleSelectAll = () => {
    if (selectedIds.size === alerts.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(alerts.map((a) => a.id)));
    }
  };

  const handleCreate = async (values: Record<string, unknown>) => {
    setCreating(true);
    try {
      await api.post('/alerts', values);
      msg.success('告警已创建');
      setCreateOpen(false);
      createForm.resetFields();
      fetchAlerts();
    } catch {
      msg.error('创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handlePageChange = (p: number) => {
    setPage(p);
    setSelectedIds(new Set());
  };

  // ── Table columns ──────────────────────────────────────

  const columns = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      render: (text: string, record: AlertItem) => (
        <Space>
          <span
            style={{
              display: 'inline-block',
              width: 8,
              height: 8,
              borderRadius: '50%',
              backgroundColor: SEVERITY_COLOR[record.severity] || '#8b8b8b',
            }}
          />
          {text}
        </Space>
      ),
    },
    {
      title: '级别',
      dataIndex: 'severity',
      key: 'severity',
      width: 80,
      render: (s: string) => <Tag color={SEVERITY_COLOR[s]}>{s}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] || s}</Tag>,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 120,
    },
    {
      title: '知识条目',
      dataIndex: 'knowledge_entry_id',
      key: 'knowledge_entry_id',
      width: 120,
      render: (v: string | null) =>
        v ? (
          <a
            href={`/ai/knowledge/wiki?page=${encodeURIComponent(v)}`}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
          >
            <BookOutlined /> 查看
          </a>
        ) : (
          <span style={{ color: '#666' }}>-</span>
        ),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (t: string) => new Date(t).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_: unknown, record: AlertItem) => (
        <Space size={4}>
          {record.status === 'pending' && (
            <Button size="small" onClick={() => handleAction(record.id, 'analyze')}>
              分析
            </Button>
          )}
          {(record.status === 'pending' || record.status === 'awaiting_review') && (
            <Button size="small" type="primary" onClick={() => handleAction(record.id, 'confirm')}>
              确认
            </Button>
          )}
          {record.status !== 'closed' && record.status !== 'confirmed' && (
            <Button size="small" danger onClick={() => handleAction(record.id, 'dismiss')}>
              忽略
            </Button>
          )}
          <Button size="small" onClick={() => setDetailAlert(record)}>
            详情
          </Button>
        </Space>
      ),
    },
  ];

  // ── Render ─────────────────────────────────────────────

  return (
    <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        <Title level={4} style={{ margin: 0 }}>
          告警中心
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchAlerts}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            创建告警
          </Button>
        </Space>
      </div>

      {/* Filter bar */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={[12, 12]} align="middle">
          <Col xs={24} sm={6}>
            <Input
              placeholder="搜索告警..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              prefix={<SearchOutlined style={{ color: token.colorTextTertiary }} />}
              allowClear
            />
          </Col>
          <Col xs={12} sm={5}>
            <Select
              value={severityFilter}
              onChange={(v) => {
                setSeverityFilter(v);
                setPage(1);
              }}
              placeholder="严重级别"
              allowClear
              style={{ width: '100%' }}
              options={[
                { value: 'critical', label: '严重' },
                { value: 'warning', label: '警告' },
                { value: 'info', label: '信息' },
              ]}
            />
          </Col>
          <Col xs={12} sm={5}>
            <Select
              value={statusFilter}
              onChange={(v) => {
                setStatusFilter(v);
                setPage(1);
              }}
              placeholder="状态"
              allowClear
              style={{ width: '100%' }}
              options={[
                { value: 'pending', label: '待处理' },
                { value: 'analyzing', label: '分析中' },
                { value: 'awaiting_review', label: '待审核' },
                { value: 'confirmed', label: '已确认' },
                { value: 'dismissed', label: '已忽略' },
                { value: 'closed', label: '已关闭' },
              ]}
            />
          </Col>
          <Col xs={24} sm={8} style={{ textAlign: 'right' }}>
            <Segmented
              value={viewMode}
              onChange={(v) => setViewMode(v as 'card' | 'table')}
              options={[
                { value: 'card', label: '卡片视图' },
                { value: 'table', label: '列表视图' },
              ]}
            />
          </Col>
        </Row>
      </Card>

      {/* Batch action bar */}
      {selectedIds.size > 0 && (
        <div
          style={{
            marginBottom: 12,
            padding: '8px 16px',
            background: token.colorPrimaryBg,
            borderRadius: 8,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <span>
            已选 <strong>{selectedIds.size}</strong> 项
          </span>
          <Button size="small" onClick={handleSelectAll}>
            {selectedIds.size === alerts.length ? '取消全选' : '全选'}
          </Button>
          <Button size="small" type="primary" onClick={() => handleBatchAction('confirm')}>
            批量确认
          </Button>
          <Button size="small" danger onClick={() => handleBatchAction('dismiss')}>
            批量忽略
          </Button>
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      ) : alerts.length === 0 ? (
        <Empty description="暂无告警" style={{ marginTop: 60 }} />
      ) : viewMode === 'card' ? (
        <Space direction="vertical" size={12} style={{ width: '100%', marginBottom: 16 }}>
          {alerts.map((alert) => (
            <AlertCard
              key={alert.id}
              alert={alert}
              selected={selectedIds.has(alert.id)}
              onSelect={handleSelect}
              onAction={handleAction}
              onDetail={setDetailAlert}
            />
          ))}
        </Space>
      ) : (
        <Table
          dataSource={alerts}
          columns={columns}
          rowKey="id"
          size="middle"
          pagination={false}
          scroll={{ x: 800 }}
        />
      )}

      {/* Pagination */}
      {alerts.length > 0 && (
        <div style={{ textAlign: 'right', marginTop: 16 }}>
          <Pagination
            current={page}
            pageSize={pageSize}
            total={totalAlerts}
            showSizeChanger={false}
            onChange={handlePageChange}
          />
        </div>
      )}

      {/* Detail drawer */}
      <AlertDetailDrawer
        alert={detailAlert}
        open={!!detailAlert}
        onClose={() => setDetailAlert(null)}
        onAction={fetchAlerts}
      />

      {/* Create modal */}
      <Modal
        title="创建告警"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        confirmLoading={creating}
      >
        <Form form={createForm} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="title"
            label="标题"
            rules={[{ required: true, message: '请输入告警标题' }]}
          >
            <Input placeholder="例如: CPU使用率过高" />
          </Form.Item>
          <Form.Item
            name="source"
            label="来源"
            rules={[{ required: true, message: '请输入告警来源' }]}
          >
            <Input placeholder="例如: prometheus" />
          </Form.Item>
          <Form.Item name="severity" label="严重级别" initialValue="warning">
            <Select
              options={[
                { value: 'critical', label: '严重 Critical' },
                { value: 'warning', label: '警告 Warning' },
                { value: 'info', label: '信息 Info' },
              ]}
            />
          </Form.Item>
          <Form.Item name="raw_event" label="原始事件 (JSON)" initialValue={{}}>
            <Input.TextArea rows={4} placeholder='{"key": "value"}' />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
