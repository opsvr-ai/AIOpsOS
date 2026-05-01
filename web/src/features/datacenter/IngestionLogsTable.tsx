import { Table, Tag, Typography } from 'antd';
import { CheckCircleOutlined, ExclamationCircleOutlined, WarningOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

const { Text } = Typography;

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

interface Props {
  logs: IngestionLog[];
  loading: boolean;
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number, pageSize: number) => void;
}

const STATUS_TAG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  success: { color: '#52c41a', icon: <CheckCircleOutlined />, label: '成功' },
  partial: { color: '#faad14', icon: <WarningOutlined />, label: '部分成功' },
  failed: { color: '#ff4d4f', icon: <ExclamationCircleOutlined />, label: '失败' },
};

export default function IngestionLogsTable({ logs, loading, total, page, pageSize, onPageChange }: Props) {
  return (
    <Table<IngestionLog>
      dataSource={logs}
      loading={loading}
      rowKey="id"
      size="small"
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        pageSizeOptions: ['10', '20', '50'],
        onChange: onPageChange,
        showTotal: (t) => `共 ${t} 条`,
      }}
      columns={[
        {
          title: '时间',
          dataIndex: 'created_at',
          width: 170,
          render: (v: string) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-',
        },
        {
          title: '状态',
          dataIndex: 'status',
          width: 110,
          render: (v: string) => {
            const cfg = STATUS_TAG[v] || STATUS_TAG.failed;
            return <Tag icon={cfg.icon} color={cfg.color} style={{ margin: 0 }}>{cfg.label}</Tag>;
          },
        },
        {
          title: '接收',
          dataIndex: 'events_received',
          width: 70,
          render: (v: number) => <Text>{v ?? 0}</Text>,
        },
        {
          title: '创建告警',
          dataIndex: 'alerts_created',
          width: 90,
          render: (v: number) => <Text>{v ?? 0}</Text>,
        },
        {
          title: '去重',
          dataIndex: 'alerts_deduped',
          width: 70,
          render: (v: number) => <Text>{v ?? 0}</Text>,
        },
        {
          title: '错误',
          dataIndex: 'errors_count',
          width: 70,
          render: (v: number) => (
            <Text style={{ color: v > 0 ? '#ff4d4f' : undefined }}>{v ?? 0}</Text>
          ),
        },
        {
          title: '耗时',
          dataIndex: 'duration_ms',
          width: 80,
          render: (v: number | null) => v != null ? `${v}ms` : '-',
        },
        {
          title: 'URL',
          dataIndex: 'request_url',
          ellipsis: true,
          width: 200,
          render: (v: string | null) => v ? <Text style={{ fontSize: 11 }}>{v}</Text> : '-',
        },
        {
          title: 'HTTP',
          dataIndex: 'response_status',
          width: 70,
          render: (v: number | null) => {
            if (v == null) return '-';
            const ok = v >= 200 && v < 300;
            return <Text style={{ color: ok ? '#52c41a' : '#ff4d4f' }}>{v}</Text>;
          },
        },
      ]}
    />
  );
}
