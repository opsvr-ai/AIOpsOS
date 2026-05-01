import { Card, Tag, Switch, Button, Space, Typography, Tooltip, Popconfirm } from 'antd';
import {
  EditOutlined, DeleteOutlined, AlertOutlined,
  ClockCircleOutlined, NumberOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

interface TriggerRule {
  id: string;
  name: string;
  condition: Record<string, unknown>;
  scenario_id: string;
  frequency_limit: number | null;
  time_window_start: string | null;
  time_window_end: string | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

interface Props {
  trigger: TriggerRule;
  onToggle: (id: string, active: boolean) => void;
  onEdit: (trigger: TriggerRule) => void;
  onDelete: (id: string) => void;
  scenarios: Record<string, string>;
}

function describeCondition(cond: Record<string, unknown>): string {
  const t = cond.type as string;
  if (t === 'and' || t === 'or') {
    const subs = (cond.conditions as Array<Record<string, unknown>>) || [];
    const joiner = t === 'and' ? ' AND ' : ' OR ';
    return subs.map(describeCondition).join(joiner);
  }
  if (t === 'simple' || !t) {
    const field = cond.field || '?';
    const op = cond.op || 'eq';
    const value = cond.value;
    const opLabel: Record<string, string> = {
      eq: '=', neq: '≠', in: '∈', not_in: '∉', contains: '⊃',
      gt: '>', lt: '<', gte: '≥', lte: '≤', regex: '≈',
    };
    return `${field} ${opLabel[op as string] || op} ${JSON.stringify(value)}`;
  }
  return JSON.stringify(cond);
}

export default function TriggerRuleCard({ trigger, onToggle, onEdit, onDelete, scenarios }: Props) {
  const scenarioName = scenarios[trigger.scenario_id] || trigger.scenario_id?.slice(0, 8) || '—';
  const conditionDesc = describeCondition(trigger.condition);

  return (
    <Card
      size="small"
      hoverable
      style={{
        borderRadius: 10,
        borderLeft: `3px solid ${trigger.is_active ? '#faad14' : '#d9d9d9'}`,
        opacity: trigger.is_active ? 1 : 0.65,
        transition: 'all 0.2s',
      }}
      styles={{ body: { padding: '14px 16px' } }}
      onClick={() => onEdit(trigger)}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <Space size={6}>
          <AlertOutlined style={{ color: trigger.is_active ? '#faad14' : '#8b8b8b' }} />
          <Text strong style={{ fontSize: 14 }}>{trigger.name}</Text>
        </Space>
        <Switch
          size="small"
          checked={trigger.is_active}
          onChange={(v, e) => {
            e.stopPropagation();
            onToggle(trigger.id, v);
          }}
          onClick={(_, e) => e.stopPropagation()}
        />
      </div>

      {/* Condition */}
      <div
        style={{
          fontSize: 12,
          fontFamily: 'monospace',
          padding: '6px 10px',
          background: '#fffbe6',
          borderRadius: 6,
          marginBottom: 8,
          border: '1px solid #ffe58f',
          wordBreak: 'break-all',
        }}
      >
        {conditionDesc}
      </div>

      {/* Meta */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        <Tag style={{ borderRadius: 4, fontSize: 11 }}>{scenarioName}</Tag>
        {trigger.frequency_limit && (
          <Tooltip title={`频率限制: 每小时最多 ${trigger.frequency_limit} 次`}>
            <Tag color="blue" style={{ borderRadius: 4, fontSize: 11 }}>
              <NumberOutlined style={{ marginRight: 2 }} />
              ≤{trigger.frequency_limit}/h
            </Tag>
          </Tooltip>
        )}
        {(trigger.time_window_start || trigger.time_window_end) && (
          <Tooltip title={`时间窗口: ${trigger.time_window_start || '00:00'} - ${trigger.time_window_end || '23:59'}`}>
            <Tag color="green" style={{ borderRadius: 4, fontSize: 11 }}>
              <ClockCircleOutlined style={{ marginRight: 2 }} />
              {trigger.time_window_start || '00:00'}-{trigger.time_window_end || '23:59'}
            </Tag>
          </Tooltip>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 4 }}
        onClick={(e) => e.stopPropagation()}>
        <Button size="small" type="text" icon={<EditOutlined />}
          onClick={(e) => { e.stopPropagation(); onEdit(trigger); }}>
          编辑
        </Button>
        <Popconfirm
          title="确定删除此触发规则？"
          onConfirm={(e) => { e?.stopPropagation(); onDelete(trigger.id); }}
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
