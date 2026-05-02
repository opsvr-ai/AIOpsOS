import { useState, useCallback } from 'react';
import {
  Table, Button, Tag, Space, Typography, Input, Select,
} from 'antd';
import { SearchOutlined, FileTextOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '@/services/api';

const { Title } = Typography;

interface LogEntry {
  id: string;
  ingested_at: string | null;
  timestamp: string | null;
  service: string;
  host: string;
  level: string;
  trace_id: string | null;
  message: string;
}

const LEVEL_COLORS: Record<string, string> = {
  ERROR: 'red', WARN: 'orange', WARNING: 'orange',
  INFO: 'blue', DEBUG: 'default', TRACE: 'default',
};

export default function LogIngestionPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [service, setService] = useState('');
  const [level, setLevel] = useState<string | undefined>();
  const [keyword, setKeyword] = useState('');
  const [traceId, setTraceId] = useState('');
  const [countData, setCountData] = useState<Record<string, number>>({});

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { limit: 100 };
      if (service) params.service = service;
      if (level) params.level = level;
      if (keyword) params.keyword = keyword;
      if (traceId) params.trace_id = traceId;
      const resp = await api.get('/logs/search', { params });
      setLogs(resp.data.items ?? []);
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, [service, level, keyword, traceId]);

  const fetchCount = useCallback(async () => {
    try {
      const params: Record<string, unknown> = {};
      if (service) params.service = service;
      if (level) params.level = level;
      const resp = await api.get('/logs/count', { params });
      setCountData(resp.data.counts ?? {});
    } catch {
      /* ignore */
    }
  }, [service, level]);

  const handleSearch = () => {
    fetchLogs();
    fetchCount();
  };

  const columns = [
    {
      title: '时间', dataIndex: 'timestamp', key: 'timestamp', width: 170,
      render: (t: string | null) => t ? new Date(t).toLocaleString() : '-',
    },
    { title: '服务', dataIndex: 'service', key: 'service', width: 120 },
    { title: '主机', dataIndex: 'host', key: 'host', width: 120 },
    {
      title: '级别', dataIndex: 'level', key: 'level', width: 80,
      render: (l: string) => <Tag color={LEVEL_COLORS[l] || 'default'}>{l}</Tag>,
    },
    {
      title: 'Trace ID', dataIndex: 'trace_id', key: 'trace_id', width: 140,
      render: (t: string | null) => t ? (
        <Typography.Text copyable style={{ fontSize: 12 }}>{t.slice(0, 12)}...</Typography.Text>
      ) : '-',
    },
    { title: '内容', dataIndex: 'message', key: 'message', ellipsis: true },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          <FileTextOutlined style={{ marginRight: 8 }} />
          日志检索
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
          placeholder="日志级别"
          style={{ width: 120 }}
          value={level}
          onChange={setLevel}
          options={[
            { value: 'ERROR', label: 'ERROR' },
            { value: 'WARN', label: 'WARN' },
            { value: 'INFO', label: 'INFO' },
            { value: 'DEBUG', label: 'DEBUG' },
          ]}
        />
        <Input
          placeholder="关键词"
          allowClear
          style={{ width: 180 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <Input
          placeholder="Trace ID"
          allowClear
          style={{ width: 200 }}
          value={traceId}
          onChange={(e) => setTraceId(e.target.value)}
        />
        <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
          查询
        </Button>
        <Button icon={<ReloadOutlined />} onClick={handleSearch} loading={loading}>
          刷新
        </Button>
      </Space>

      {Object.keys(countData).length > 0 && (
        <Space size={8} style={{ marginBottom: 12 }}>
          {Object.entries(countData).map(([lvl, cnt]) => (
            <Tag key={lvl} color={LEVEL_COLORS[lvl] || 'default'}>
              {lvl}: {cnt}
            </Tag>
          ))}
        </Space>
      )}

      <Table
        dataSource={logs}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="middle"
        pagination={{ pageSize: 50, showSizeChanger: false }}
      />
    </div>
  );
}
