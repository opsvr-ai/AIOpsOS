import { useState, useCallback } from 'react';
import { Table, Tag, Space, Typography, Input, Select, Button } from 'antd';
import { SearchOutlined, MessageOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '@/services/api';

const { Title } = Typography;

interface ItsmTicket {
  id: string;
  external_id: string | null;
  ticket_type: string;
  title: string;
  status: string;
  priority: string;
  affected_service: string;
  assigned_to: string | null;
  created_at: string | null;
  resolved_at: string | null;
  linked_alert_ids: string[] | null;
}

const TICKET_TYPE_LABELS: Record<string, string> = {
  incident: '事件单',
  change: '变更单',
  problem: '问题单',
  request: '服务请求',
};

const PRIORITY_COLORS: Record<string, string> = {
  critical: 'red',
  high: 'orange',
  medium: 'blue',
  low: 'default',
};

const STATUS_COLORS: Record<string, string> = {
  open: 'blue',
  in_progress: 'orange',
  resolved: 'green',
  closed: 'default',
};

export default function ItsmPage() {
  const [tickets, setTickets] = useState<ItsmTicket[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [service, setService] = useState('');
  const [ticketType, setTicketType] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState('');

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { page, page_size: 30 };
      if (service) params.service = service;
      if (ticketType) params.ticket_type = ticketType;
      if (status) params.status = status;
      if (keyword) params.keyword = keyword;
      const resp = await api.get('/itsm/tickets', { params });
      setTickets(resp.data.items ?? []);
      setTotal(resp.data.total ?? 0);
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, [page, service, ticketType, status, keyword]);

  const handleSearch = () => {
    setPage(1);
    fetchTickets();
  };

  const columns = [
    {
      title: '工单ID',
      dataIndex: 'external_id',
      key: 'external_id',
      width: 130,
      render: (t: string | null) => t ?? '-',
    },
    {
      title: '类型',
      dataIndex: 'ticket_type',
      key: 'ticket_type',
      width: 80,
      render: (t: string) => <Tag>{TICKET_TYPE_LABELS[t] || t}</Tag>,
    },
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 80,
      render: (p: string) => <Tag color={PRIORITY_COLORS[p] || 'default'}>{p}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (s: string) => <Tag color={STATUS_COLORS[s] || 'default'}>{s}</Tag>,
    },
    { title: '影响服务', dataIndex: 'affected_service', key: 'affected_service', width: 110 },
    { title: '指派人', dataIndex: 'assigned_to', key: 'assigned_to', width: 90 },
    {
      title: '关联告警',
      dataIndex: 'linked_alert_ids',
      key: 'linked_alert_ids',
      width: 90,
      render: (ids: string[] | null) => ((ids?.length ?? 0) > 0 ? `${ids!.length}条` : '-'),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (t: string | null) => (t ? new Date(t).toLocaleString() : '-'),
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
        <Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          <MessageOutlined style={{ marginRight: 8 }} />
          ITSM 工单管理
        </Title>
      </div>

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          placeholder="服务名"
          allowClear
          style={{ width: 150 }}
          value={service}
          onChange={(e) => setService(e.target.value)}
        />
        <Select
          allowClear
          placeholder="工单类型"
          style={{ width: 120 }}
          value={ticketType}
          onChange={setTicketType}
          options={[
            { value: 'incident', label: '事件单' },
            { value: 'change', label: '变更单' },
            { value: 'problem', label: '问题单' },
            { value: 'request', label: '服务请求' },
          ]}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={setStatus}
          options={[
            { value: 'open', label: '待处理' },
            { value: 'in_progress', label: '处理中' },
            { value: 'resolved', label: '已解决' },
            { value: 'closed', label: '已关闭' },
          ]}
        />
        <Input
          placeholder="关键词"
          allowClear
          style={{ width: 180 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
          查询
        </Button>
        <Button icon={<ReloadOutlined />} onClick={handleSearch} loading={loading}>
          刷新
        </Button>
      </Space>

      <Table
        dataSource={tickets}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="middle"
        pagination={{
          current: page,
          total,
          pageSize: 30,
          onChange: (p) => {
            setPage(p);
            fetchTickets();
          },
          showSizeChanger: false,
        }}
      />
    </div>
  );
}
