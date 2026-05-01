import { Card, Tag, Switch, Typography, Tooltip, Popconfirm, theme } from 'antd';
import {
  ApiOutlined,
  LinkOutlined,
  ClusterOutlined,
  FileTextOutlined,
  MessageOutlined,
  DatabaseOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  PauseCircleOutlined,
  EditOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';

dayjs.extend(relativeTime);

const { Text } = Typography;

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

interface Props {
  ds: DataSourceItem;
  onToggle: (id: string, enabled: boolean) => void;
  onEdit: (ds: DataSourceItem) => void;
  onDelete: (id: string) => void;
  onClick: (ds: DataSourceItem) => void;
  selected?: boolean;
}

const TYPE_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  kafka: { icon: <ClusterOutlined />, label: 'Kafka', color: '#b37feb' },
  webhook: { icon: <LinkOutlined />, label: 'Webhook', color: '#5cdbd3' },
  api: { icon: <ApiOutlined />, label: 'API', color: '#597ef7' },
  log: { icon: <FileTextOutlined />, label: '日志', color: '#fa8c16' },
  itsm: { icon: <MessageOutlined />, label: 'ITSM', color: '#13c2c2' },
  cmdb: { icon: <DatabaseOutlined />, label: 'CMDB', color: '#2f54eb' },
};

const STATUS_CONFIG: Record<string, { icon: React.ReactNode; color: string }> = {
  active: { icon: <CheckCircleOutlined />, color: '#52c41a' },
  error: { icon: <ExclamationCircleOutlined />, color: '#ff4d4f' },
  paused: { icon: <PauseCircleOutlined />, color: '#faad14' },
};

export default function DataSourceCard({
  ds,
  onToggle,
  onEdit,
  onDelete,
  onClick,
  selected,
}: Props) {
  const { token } = theme.useToken();
  const type = TYPE_CONFIG[ds.source_type] || TYPE_CONFIG.webhook;
  const status = STATUS_CONFIG[ds.status] || STATUS_CONFIG.active;

  return (
    <Card
      hoverable
      size="small"
      onClick={() => onClick(ds)}
      style={{
        borderRadius: 12,
        border: selected ? `2px solid ${token.colorPrimary}` : `1px solid ${token.colorBorder}`,
        transition: 'border-color 0.2s, box-shadow 0.2s',
        cursor: 'pointer',
      }}
      styles={{ body: { padding: '16px' } }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 8,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}>
          <span style={{ fontSize: 18, color: type.color }}>{type.icon}</span>
          <Text strong ellipsis style={{ fontSize: 14, maxWidth: 140 }}>
            {ds.name}
          </Text>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          <Tag
            color={status.color}
            style={{ margin: 0, fontSize: 11, lineHeight: '18px', padding: '0 6px' }}
          >
            {ds.status === 'active' ? '正常' : ds.status === 'error' ? '异常' : '暂停'}
          </Tag>
          <Switch
            size="small"
            checked={ds.is_enabled}
            onChange={(v) => onToggle(ds.id, v)}
            onClick={(_, e) => e.stopPropagation()}
          />
        </div>
      </div>

      {ds.description && (
        <Text
          type="secondary"
          style={{ fontSize: 12, display: 'block', marginBottom: 10, lineHeight: '18px' }}
        >
          {ds.description.slice(0, 80)}
          {ds.description.length > 80 ? '...' : ''}
        </Text>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Tag style={{ margin: 0, fontSize: 11, borderRadius: 4 }}>{type.label}</Tag>
        <Text type="secondary" style={{ fontSize: 11 }}>
          <ClockCircleOutlined style={{ marginRight: 4 }} />
          已入库 {ds.total_ingested || 0}
        </Text>
      </div>

      {ds.last_ingested_at && (
        <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 6 }}>
          最近: {dayjs(ds.last_ingested_at).fromNow()}
        </Text>
      )}

      <div
        style={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 4,
          marginTop: 10,
          borderTop: `1px solid ${token.colorBorder}`,
          paddingTop: 8,
        }}
      >
        <Tooltip title="编辑">
          <EditOutlined
            style={{ fontSize: 13, color: token.colorTextSecondary, cursor: 'pointer', padding: 2 }}
            onClick={(e) => {
              e.stopPropagation();
              onEdit(ds);
            }}
          />
        </Tooltip>
        <Popconfirm
          title="确定删除此数据源？"
          onConfirm={(e) => {
            e?.stopPropagation();
            onDelete(ds.id);
          }}
          onCancel={(e) => e?.stopPropagation()}
        >
          <DeleteOutlined
            style={{ fontSize: 13, color: '#ff4d4f', cursor: 'pointer', padding: 2 }}
            onClick={(e) => e.stopPropagation()}
          />
        </Popconfirm>
      </div>
    </Card>
  );
}
