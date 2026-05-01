import { useState } from 'react';
import {
  Drawer, Descriptions, Tag, Typography, Button, Space, Collapse,
} from 'antd';
import {
  CheckCircleOutlined, StopOutlined, ThunderboltOutlined,
  ClockCircleOutlined, 
} from '@ant-design/icons';
import AnalysisResultView from './AnalysisResultView';
import api from '@/services/api';

const { Text } = Typography;

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
  created_at: string;
  updated_at: string;
}

interface Props {
  alert: AlertItem | null;
  open: boolean;
  onClose: () => void;
  onAction: () => void;
}

const SEVERITY_COLOR: Record<string, string> = { critical: '#ff4d4f', warning: '#faad14', info: '#1890ff' };
const STATUS_COLOR: Record<string, string> = {
  pending: 'default', analyzing: 'processing', awaiting_review: 'warning',
  confirmed: 'success', dismissed: 'error', closed: 'default',
};
const STATUS_LABEL: Record<string, string> = {
  pending: '待处理', analyzing: '分析中', awaiting_review: '待审核',
  confirmed: '已确认', dismissed: '已忽略', closed: '已关闭',
};

export default function AlertDetailDrawer({ alert, open, onClose, onAction }: Props) {
  const [acting, setActing] = useState(false);

  if (!alert) return null;

  const handleAction = async (action: string) => {
    setActing(true);
    try {
      await api.post(`/alerts/${alert.id}/action`, { action });
      onAction();
    } catch {
      // handled by api interceptor
    } finally {
      setActing(false);
    }
  };

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
    <Drawer
      title={`告警详情: ${alert.title}`}
      open={open}
      onClose={onClose}
      width={560}
      extra={
        <Space>
          {canAct('analyze') && (
            <Button
              icon={<ThunderboltOutlined />}
              loading={acting}
              onClick={() => handleAction('analyze')}
            >
              分析
            </Button>
          )}
          {canAct('confirm') && (
            <Button
              type="primary"
              icon={<CheckCircleOutlined />}
              loading={acting}
              onClick={() => handleAction('confirm')}
            >
              确认
            </Button>
          )}
          {canAct('dismiss') && (
            <Button
              danger
              icon={<StopOutlined />}
              loading={acting}
              onClick={() => handleAction('dismiss')}
            >
              忽略
            </Button>
          )}
          {canAct('close') && (
            <Button icon={<CheckCircleOutlined />} loading={acting} onClick={() => handleAction('close')}>
              关闭
            </Button>
          )}
        </Space>
      }
    >
      <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
        <Descriptions.Item label="严重级别">
          <Tag color={SEVERITY_COLOR[alert.severity]}>{alert.severity}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={STATUS_COLOR[alert.status]}>{STATUS_LABEL[alert.status] || alert.status}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="来源">{alert.source}</Descriptions.Item>
        <Descriptions.Item label="创建时间">
          <ClockCircleOutlined style={{ marginRight: 4 }} />
          {new Date(alert.created_at).toLocaleString('zh-CN')}
        </Descriptions.Item>
        {alert.confirmed_by && (
          <Descriptions.Item label="确认人">{alert.confirmed_by}</Descriptions.Item>
        )}
        {alert.confirmed_at && (
          <Descriptions.Item label="确认时间">
            {new Date(alert.confirmed_at).toLocaleString('zh-CN')}
          </Descriptions.Item>
        )}
      </Descriptions>

      <Collapse
        defaultActiveKey={alert.analysis_result?.summary ? ['analysis'] : ['raw']}
        items={[
          {
            key: 'analysis',
            label: 'AI 分析结果',
            children: <AnalysisResultView analysisResult={alert.analysis_result} />,
          },
          {
            key: 'raw',
            label: '原始事件数据',
            children: (
              <pre style={{
                background: '#1e1e2e', color: '#cdd6f4', padding: 12, borderRadius: 6,
                fontSize: 12, maxHeight: 300, overflow: 'auto',
              }}>
                {JSON.stringify(alert.raw_event, null, 2)}
              </pre>
            ),
          },
          {
            key: 'context',
            label: '上下文信息',
            children: alert.enriched_context && Object.keys(alert.enriched_context).length > 0
              ? (
                <pre style={{
                  background: '#1e1e2e', color: '#cdd6f4', padding: 12, borderRadius: 6,
                  fontSize: 12, maxHeight: 300, overflow: 'auto',
                }}>
                  {JSON.stringify(alert.enriched_context, null, 2)}
                </pre>
              )
              : <Text type="secondary">暂无上下文</Text>,
          },
        ]}
      />
    </Drawer>
  );
}
