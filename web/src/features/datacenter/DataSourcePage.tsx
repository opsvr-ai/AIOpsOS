import { useEffect, useState, useCallback } from 'react';
import {
  Button,
  Select,
  Input,
  Space,
  Typography,
  Row,
  Col,
  Spin,
  Empty,
  App,
  Pagination,
} from 'antd';
import { PlusOutlined, ReloadOutlined, SearchOutlined, AppstoreOutlined } from '@ant-design/icons';
import api from '@/services/api';
import DataSourceCard from './DataSourceCard';
import DataSourceFormModal from './DataSourceFormModal';
import DataSourceDetailDrawer from './DataSourceDetailDrawer';

const { Title } = Typography;

interface DataSourceItem {
  id: string;
  name: string;
  description: string | null;
  source_type: string;
  is_enabled: boolean;
  config: Record<string, unknown>;
  normalization_rules: Record<string, unknown>;
  last_ingested_at: string | null;
  total_ingested: number;
  status: string;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface IngestionLog {
  id: string;
  datasource_id: string;
  status: string;
  events_received: number;
  alerts_created: number;
  alerts_deduped: number;
  errors_count: number;
  errors_detail: unknown;
  duration_ms: number | null;
  request_url: string | null;
  response_status: number | null;
  created_at: string | null;
}

const PAGE_SIZE = 12;

export default function DataSourcePage() {
  const { message: msg } = App.useApp();

  const [loading, setLoading] = useState(true);
  const [datasources, setDatasources] = useState<DataSourceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);

  const [filterType, setFilterType] = useState<string | undefined>();
  const [filterStatus, setFilterStatus] = useState<string | undefined>();
  const [search, setSearch] = useState('');

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<DataSourceItem | null>(null);

  const [selected, setSelected] = useState<DataSourceItem | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const [logs, setLogs] = useState<IngestionLog[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsTotal, setLogsTotal] = useState(0);
  const [logsPage, setLogsPage] = useState(1);
  const [logsPageSize, setLogsPageSize] = useState(10);

  const fetchDatasources = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { page, page_size: PAGE_SIZE };
      if (filterType) params.source_type = filterType;
      if (filterStatus) params.status = filterStatus;
      if (search) params.search = search;
      const res = await api.get('/datasources', { params });
      setDatasources(res.data ?? []);
      // if API returns total in headers or meta, use that; otherwise estimate
      setTotal(
        (res.data?.length ?? 0) < PAGE_SIZE
          ? (page - 1) * PAGE_SIZE + (res.data?.length ?? 0)
          : (page + 1) * PAGE_SIZE,
      );
    } catch {
      msg.error('加载数据源失败');
    }
    setLoading(false);
  }, [page, filterType, filterStatus, search, msg]);

  const fetchLogs = useCallback(async (dsId: string, p: number, ps: number) => {
    setLogsLoading(true);
    try {
      const res = await api.get(`/datasources/${dsId}/logs`, {
        params: { page: p, page_size: ps },
      });
      setLogs(res.data ?? []);
      setLogsTotal(
        (res.data?.length ?? 0) < ps ? (p - 1) * ps + (res.data?.length ?? 0) : (p + 1) * ps,
      );
    } catch {
      /* ignore */
    }
    setLogsLoading(false);
  }, []);

  useEffect(() => {
    fetchDatasources();
  }, [fetchDatasources]);

  const handleCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };

  const handleEdit = (ds: DataSourceItem) => {
    setEditing(ds);
    setModalOpen(true);
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    try {
      if (editing) {
        await api.patch(`/datasources/${editing.id}`, values);
        msg.success('更新成功');
      } else {
        await api.post('/datasources', values);
        msg.success('创建成功');
      }
      setModalOpen(false);
      fetchDatasources();
    } catch {
      msg.error(editing ? '更新失败' : '创建失败');
    }
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await api.patch(`/datasources/${id}`, { is_enabled: enabled });
      setDatasources((prev) => prev.map((d) => (d.id === id ? { ...d, is_enabled: enabled } : d)));
    } catch {
      msg.error('操作失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/datasources/${id}`);
      msg.success('已删除');
      fetchDatasources();
    } catch {
      msg.error('删除失败');
    }
  };

  const handleTest = async (id: string) => {
    try {
      const res = await api.post(`/datasources/${id}/test`);
      if (res.data?.success) {
        msg.success(res.data.message || '测试成功');
      } else {
        msg.warning(res.data?.message || '测试失败');
      }
    } catch {
      msg.error('测试请求失败');
    }
  };

  const handleCardClick = (ds: DataSourceItem) => {
    setSelected(ds);
    setDrawerOpen(true);
    setLogsPage(1);
    fetchLogs(ds.id, 1, logsPageSize);
  };

  const handleLogsPageChange = (p: number, ps: number) => {
    setLogsPage(p);
    setLogsPageSize(ps);
    if (selected) fetchLogs(selected.id, p, ps);
  };

  const handleSearch = (value: string) => {
    setSearch(value);
    setPage(1);
  };

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        <Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          <AppstoreOutlined style={{ marginRight: 8 }} />
          数据接入
        </Title>
        <Space wrap>
          <Input.Search
            placeholder="搜索数据源..."
            allowClear
            style={{ width: 200 }}
            onSearch={handleSearch}
            prefix={<SearchOutlined />}
          />
          <Select
            allowClear
            placeholder="类型"
            style={{ width: 110 }}
            value={filterType}
            onChange={(v) => {
              setFilterType(v);
              setPage(1);
            }}
            options={[
              { label: 'Webhook', value: 'webhook' },
              { label: 'API', value: 'api' },
              { label: 'Kafka', value: 'kafka' },
            ]}
          />
          <Select
            allowClear
            placeholder="状态"
            style={{ width: 100 }}
            value={filterStatus}
            onChange={(v) => {
              setFilterStatus(v);
              setPage(1);
            }}
            options={[
              { label: '正常', value: 'active' },
              { label: '异常', value: 'error' },
              { label: '暂停', value: 'paused' },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={fetchDatasources} loading={loading}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            创建数据源
          </Button>
        </Space>
      </div>

      <Spin spinning={loading}>
        {datasources.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 60, background: '#fff', borderRadius: 12 }}>
            <Empty description="暂无数据源" />
          </div>
        ) : (
          <>
            <Row gutter={[12, 12]}>
              {datasources.map((ds) => (
                <Col key={ds.id} xs={24} sm={12} md={8} lg={6}>
                  <DataSourceCard
                    ds={ds}
                    onToggle={handleToggle}
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                    onClick={handleCardClick}
                    selected={selected?.id === ds.id}
                  />
                </Col>
              ))}
            </Row>
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: 20 }}>
              <Pagination
                current={page}
                total={total}
                pageSize={PAGE_SIZE}
                onChange={(p) => setPage(p)}
                showSizeChanger={false}
              />
            </div>
          </>
        )}
      </Spin>

      <DataSourceFormModal
        open={modalOpen}
        editing={editing}
        onCancel={() => setModalOpen(false)}
        onSubmit={handleSubmit}
      />

      <DataSourceDetailDrawer
        ds={selected}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onToggle={handleToggle}
        onTest={handleTest}
        onEdit={(ds) => {
          setDrawerOpen(false);
          handleEdit(ds);
        }}
        logs={logs}
        logsLoading={logsLoading}
        logsTotal={logsTotal}
        logsPage={logsPage}
        logsPageSize={logsPageSize}
        onLogsPageChange={handleLogsPageChange}
      />
    </div>
  );
}
