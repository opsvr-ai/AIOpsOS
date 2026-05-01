import { Drawer, Descriptions, Tag, Tabs, Typography, Space, Button, Switch } from 'antd';
import {
  ApiOutlined, LinkOutlined, ClusterOutlined,
  CheckCircleOutlined, ExclamationCircleOutlined,
  PauseCircleOutlined, ReloadOutlined, PlayCircleOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import IngestionLogsTable from './IngestionLogsTable';

const { Text, Paragraph } = Typography;

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

interface Props {
  ds: DataSourceItem | null;
  open: boolean;
  onClose: () => void;
  onToggle: (id: string, enabled: boolean) => void;
  onTest: (id: string) => void;
  onEdit: (ds: DataSourceItem) => void;
  logs: IngestionLog[];
  logsLoading: boolean;
  logsTotal: number;
  logsPage: number;
  logsPageSize: number;
  onLogsPageChange: (page: number, pageSize: number) => void;
}

const TYPE_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  kafka: { icon: <ClusterOutlined />, label: 'Kafka', color: '#b37feb' },
  webhook: { icon: <LinkOutlined />, label: 'Webhook', color: '#5cdbd3' },
  api: { icon: <ApiOutlined />, label: 'API', color: '#597ef7' },
};

const STATUS_CONFIG: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  active: { icon: <CheckCircleOutlined />, label: '正常', color: '#52c41a' },
  error: { icon: <ExclamationCircleOutlined />, label: '异常', color: '#ff4d4f' },
  paused: { icon: <PauseCircleOutlined />, label: '暂停', color: '#faad14' },
};

export default function DataSourceDetailDrawer({
  ds, open, onClose, onToggle, onTest, onEdit,
  logs, logsLoading, logsTotal, logsPage, logsPageSize, onLogsPageChange,
}: Props) {
  if (!ds) return null;

  const type = TYPE_CONFIG[ds.source_type] || TYPE_CONFIG.webhook;
  const status = STATUS_CONFIG[ds.status] || STATUS_CONFIG.active;
  const webhookUrl = ds.config?.endpoint_id
    ? `${window.location.origin}/api/v1/webhook/${ds.config.endpoint_id}`
    : '';

  return (
    <Drawer
      title={
        <Space>
          <span style={{ color: type.color }}>{type.icon}</span>
          <Text strong style={{ fontSize: 16 }}>{ds.name}</Text>
          <Tag icon={status.icon} color={status.color}>{status.label}</Tag>
        </Space>
      }
      open={open}
      onClose={onClose}
      width={640}
      extra={
        <Space>
          <Button icon={<PlayCircleOutlined />} onClick={() => onTest(ds.id)}>测试</Button>
          <Button icon={<ReloadOutlined />} onClick={() => onEdit(ds)}>编辑</Button>
        </Space>
      }
    >
      <Tabs
        defaultActiveKey="info"
        items={[
          {
            key: 'info',
            label: '基本信息',
            children: (
              <>
                <Descriptions column={2} size="small" bordered style={{ marginBottom: 16 }}>
                  <Descriptions.Item label="类型">
                    <Tag style={{ margin: 0 }}>{type.label}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="状态">
                    <Space>
                      <Switch size="small" checked={ds.is_enabled}
                        onChange={(v) => onToggle(ds.id, v)} />
                      <Text style={{ fontSize: 12 }}>{ds.is_enabled ? '已启用' : '已停用'}</Text>
                    </Space>
                  </Descriptions.Item>
                  <Descriptions.Item label="入库总量">{ds.total_ingested || 0}</Descriptions.Item>
                  <Descriptions.Item label="最近入库">
                    {ds.last_ingested_at ? dayjs(ds.last_ingested_at).format('YYYY-MM-DD HH:mm:ss') : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="创建时间">
                    {ds.created_at ? dayjs(ds.created_at).format('YYYY-MM-DD HH:mm:ss') : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="更新时间">
                    {ds.updated_at ? dayjs(ds.updated_at).format('YYYY-MM-DD HH:mm:ss') : '-'}
                  </Descriptions.Item>
                </Descriptions>

                {ds.description && (
                  <div style={{ marginBottom: 16 }}>
                    <Text strong style={{ fontSize: 13 }}>描述</Text>
                    <Paragraph type="secondary" style={{ marginTop: 4, fontSize: 13 }}>
                      {ds.description}
                    </Paragraph>
                  </div>
                )}

                {ds.error_message && (
                  <div style={{ marginBottom: 16, padding: 10, background: '#fff2f0', borderRadius: 8 }}>
                    <Text type="danger" style={{ fontSize: 12 }}>{ds.error_message}</Text>
                  </div>
                )}

                <Text strong style={{ fontSize: 13, display: 'block', marginBottom: 8 }}>配置</Text>
                <pre style={{
                  background: '#fafafa', padding: 12, borderRadius: 8,
                  fontSize: 12, maxHeight: 240, overflow: 'auto',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                }}>
                  {JSON.stringify(ds.config, null, 2)}
                </pre>

                {ds.source_type === 'webhook' && webhookUrl && (
                  <div style={{ marginTop: 12 }}>
                    <Text strong style={{ fontSize: 13 }}>Webhook URL</Text>
                    <Paragraph copyable style={{ marginTop: 4, fontSize: 12, fontFamily: 'monospace' }}>
                      {webhookUrl}
                    </Paragraph>
                    {(ds.config?.secret as string) && (
                      <>
                        <Text strong style={{ fontSize: 13 }}>密钥</Text>
                        <Paragraph copyable style={{ marginTop: 4, fontSize: 12, fontFamily: 'monospace' }}>
                          {ds.config.secret as string}
                        </Paragraph>
                      </>
                    )}
                  </div>
                )}

                {Object.keys(ds.normalization_rules || {}).length > 0 && (
                  <>
                    <Text strong style={{ fontSize: 13, display: 'block', marginTop: 12, marginBottom: 8 }}>
                      字段映射
                    </Text>
                    <pre style={{
                      background: '#fafafa', padding: 12, borderRadius: 8, fontSize: 12,
                    }}>
                      {JSON.stringify(ds.normalization_rules, null, 2)}
                    </pre>
                  </>
                )}
              </>
            ),
          },
          {
            key: 'logs',
            label: '入库日志',
            children: (
              <IngestionLogsTable
                logs={logs}
                loading={logsLoading}
                total={logsTotal}
                page={logsPage}
                pageSize={logsPageSize}
                onPageChange={onLogsPageChange}
              />
            ),
          },
        ]}
      />
    </Drawer>
  );
}
