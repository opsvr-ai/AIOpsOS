import { useState } from 'react';
import { Card, Tag, Typography, Button, Space, Checkbox, Tooltip } from 'antd';
import {
  CheckCircleOutlined,
  StopOutlined,
  ThunderboltOutlined,
  ClockCircleOutlined,
  ExpandAltOutlined,
  BookOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';

dayjs.extend(relativeTime);

const { Text, Paragraph } = Typography;

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

interface Props {
  alert: AlertItem;
  selected: boolean;
  onSelect: (id: string, checked: boolean) => void;
  onAction: (id: string, action: string) => void;
  onDetail: (alert: AlertItem) => void;
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

export default function AlertCard({ alert, selected, onSelect, onAction, onDetail }: Props) {
  const [expanded, setExpanded] = useState(false);
  const hasAnalysis: boolean = !!(alert.analysis_result && alert.analysis_result.summary);

  const canAct = (action: string): boolean => {
    const allowed: Record<string, string[]> = {
      pending: ['analyze', 'dismiss'],
      analyzing: ['dismiss'],
      awaiting_review: ['confirm', 'dismiss'],
      confirmed: ['close'],
      dismissed: ['close'],
    };
    return (allowed[alert.status] || []).includes(action);
  };

  return (
    <Card
      size="small"
      style={{
        borderLeft: `4px solid ${SEVERITY_COLOR[alert.severity] || '#8b8b8b'}`,
        opacity: alert.status === 'closed' ? 0.6 : 1,
      }}
      styles={{ body: { padding: '12px 16px' } }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 8,
          cursor: 'pointer',
        }}
        onClick={() => onSelect(alert.id, !selected)}
      >
        <Checkbox
          checked={selected}
          onChange={(e) => {
            e.stopPropagation();
            onSelect(alert.id, e.target.checked);
          }}
        />
        <Text strong style={{ flex: 1, fontSize: 14 }}>
          {alert.title}
        </Text>
        <Tag color={STATUS_COLOR[alert.status]}>{STATUS_LABEL[alert.status] || alert.status}</Tag>
        <Tooltip title={dayjs(alert.created_at).format('YYYY-MM-DD HH:mm:ss')}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            <ClockCircleOutlined style={{ marginRight: 4 }} />
            {dayjs(alert.created_at).fromNow()}
          </Text>
        </Tooltip>
      </div>

      {/* Tags */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
        <Tag color={SEVERITY_COLOR[alert.severity]}>{alert.severity}</Tag>
        <Tag>{alert.source}</Tag>
        {hasAnalysis && <Tag color="purple">已分析</Tag>}
        {alert.knowledge_entry_id && (
          <Tooltip title="查看知识条目">
            <a
              href={`/ai/knowledge/wiki?page=${encodeURIComponent(alert.knowledge_entry_id)}`}
              style={{ display: 'inline-flex', alignItems: 'center' }}
            >
              <Tag icon={<BookOutlined />} color="geekblue">
                知识条目
              </Tag>
            </a>
          </Tooltip>
        )}
      </div>

      {/* Analysis preview */}
      {hasAnalysis && expanded && (
        <Paragraph
          type="secondary"
          style={{
            fontSize: 13,
            marginBottom: 8,
            padding: 8,
            background: '#1a1a2e',
            borderRadius: 6,
          }}
          ellipsis={{ rows: 3 }}
        >
          {String(alert.analysis_result.summary)}
        </Paragraph>
      )}

      {/* Actions */}
      <div
        style={{ display: 'flex', gap: 6, justifyContent: 'space-between', alignItems: 'center' }}
      >
        <Space size={4}>
          <Button
            size="small"
            icon={<ExpandAltOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded(!expanded);
            }}
          >
            {expanded ? '收起' : '展开'}
          </Button>
          <Button
            size="small"
            onClick={(e) => {
              e.stopPropagation();
              onDetail(alert);
            }}
          >
            详情
          </Button>
        </Space>
        <Space size={4}>
          {canAct('analyze') && (
            <Button
              size="small"
              type="primary"
              icon={<ThunderboltOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                onAction(alert.id, 'analyze');
              }}
            >
              分析
            </Button>
          )}
          {canAct('confirm') && (
            <Button
              size="small"
              type="primary"
              icon={<CheckCircleOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                onAction(alert.id, 'confirm');
              }}
            >
              确认
            </Button>
          )}
          {canAct('dismiss') && (
            <Button
              size="small"
              danger
              icon={<StopOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                onAction(alert.id, 'dismiss');
              }}
            >
              忽略
            </Button>
          )}
          {canAct('close') && (
            <Button
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                onAction(alert.id, 'close');
              }}
            >
              关闭
            </Button>
          )}
        </Space>
      </div>
    </Card>
  );
}
