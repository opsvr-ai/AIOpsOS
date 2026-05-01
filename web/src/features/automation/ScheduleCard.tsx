import { Card, Tag, Switch, Button, Space, Typography, Tooltip, Popconfirm } from 'antd';
import {
  ClockCircleOutlined, EditOutlined, DeleteOutlined,
  ThunderboltOutlined, CheckCircleOutlined, CloseCircleOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';

const { Text } = Typography;

interface Schedule {
  id: string;
  name: string;
  cron_expression: string;
  scenario_id: string;
  params: Record<string, unknown>;
  is_active: boolean;
  next_run: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface Execution {
  id: string;
  schedule_id: string;
  status: string;
  result: Record<string, unknown>;
  created_at: string | null;
}

interface Props {
  schedule: Schedule;
  executions: Execution[];
  onToggle: (id: string, active: boolean) => void;
  onEdit: (schedule: Schedule) => void;
  onDelete: (id: string) => void;
  scenarios: Record<string, string>;
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  success: <CheckCircleOutlined style={{ color: '#52c41a' }} />,
  failed: <CloseCircleOutlined style={{ color: '#ff4d4f' }} />,
  running: <SyncOutlined spin style={{ color: '#1890ff' }} />,
};

export default function ScheduleCard({ schedule, executions, onToggle, onEdit, onDelete, scenarios }: Props) {
  const lastExec = executions[0];
  const scenarioName = scenarios[schedule.scenario_id] || schedule.scenario_id?.slice(0, 8) || '—';

  return (
    <Card
      size="small"
      hoverable
      style={{
        borderRadius: 10,
        borderLeft: `3px solid ${schedule.is_active ? '#1890ff' : '#d9d9d9'}`,
        opacity: schedule.is_active ? 1 : 0.65,
        transition: 'all 0.2s',
      }}
      styles={{ body: { padding: '14px 16px' } }}
      onClick={() => onEdit(schedule)}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <Space size={6}>
          <ThunderboltOutlined style={{ color: schedule.is_active ? '#1890ff' : '#8b8b8b' }} />
          <Text strong style={{ fontSize: 14 }}>{schedule.name}</Text>
        </Space>
        <Switch
          size="small"
          checked={schedule.is_active}
          onChange={(v, e) => {
            e.stopPropagation();
            onToggle(schedule.id, v);
          }}
          onClick={(_, e) => e.stopPropagation()}
        />
      </div>

      {/* Meta */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        <Tag color="blue" style={{ borderRadius: 4, fontFamily: 'monospace', fontSize: 11 }}>
          {schedule.cron_expression}
        </Tag>
        <Tag style={{ borderRadius: 4, fontSize: 11 }}>{scenarioName}</Tag>
        {schedule.next_run && (
          <Tooltip title={dayjs(schedule.next_run).format('YYYY-MM-DD HH:mm:ss')}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              <ClockCircleOutlined style={{ marginRight: 3 }} />
              {dayjs(schedule.next_run).fromNow()}
            </Text>
          </Tooltip>
        )}
      </div>

      {/* Last execution */}
      {lastExec && (
        <div
          style={{
            fontSize: 11,
            color: '#8b8b8b',
            padding: '6px 8px',
            background: '#fafafa',
            borderRadius: 6,
            marginBottom: 8,
          }}
        >
          <Space size={4}>
            {STATUS_ICON[lastExec.status] || null}
            <span>上次: {lastExec.status}</span>
            <span>·</span>
            <span>{lastExec.created_at ? dayjs(lastExec.created_at).fromNow() : ''}</span>
          </Space>
        </div>
      )}

      {/* Actions */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 4 }}
        onClick={(e) => e.stopPropagation()}>
        <Button size="small" type="text" icon={<EditOutlined />}
          onClick={(e) => { e.stopPropagation(); onEdit(schedule); }}>
          编辑
        </Button>
        <Popconfirm
          title="确定删除此调度？"
          onConfirm={(e) => { e?.stopPropagation(); onDelete(schedule.id); }}
          onCancel={(e) => e?.stopPropagation()}
        >
          <Button size="small" type="text" danger icon={<DeleteOutlined />}
            onClick={(e) => e.stopPropagation()}>
            删除
          </Button>
        </Popconfirm>
      </div>
    </Card>
  );
}
