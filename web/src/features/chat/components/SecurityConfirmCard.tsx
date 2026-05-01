import { useState } from 'react';
import { Card, Tag, Button, Space, Typography, Collapse, theme } from 'antd';
import {
  SafetyCertificateOutlined,
  CheckOutlined,
  CloseOutlined,
  WarningFilled,
  ThunderboltFilled,
  InfoCircleFilled,
  ExclamationCircleFilled,
} from '@ant-design/icons';
import type { InterruptData } from '@/stores/chatStore';

const { Text, Paragraph } = Typography;

const RISK_CONFIG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  low: { color: '#52c41a', icon: <InfoCircleFilled />, label: '低风险' },
  medium: { color: '#faad14', icon: <ExclamationCircleFilled />, label: '中风险' },
  high: { color: '#ff7a2f', icon: <WarningFilled />, label: '高风险' },
  critical: { color: '#ff4d4f', icon: <ThunderboltFilled />, label: '严重风险' },
};

interface Props {
  interruptData: InterruptData;
  resolved?: boolean;
  onRespond?: (approved: boolean) => void;
}

export default function SecurityConfirmCard({ interruptData, resolved, onRespond }: Props) {
  const { token } = theme.useToken();
  const [responding, setResponding] = useState(false);
  const data = interruptData.data;
  const risk = RISK_CONFIG[data.risk_level || 'medium'] || RISK_CONFIG.medium;

  const handleRespond = (approved: boolean) => {
    setResponding(true);
    onRespond?.(approved);
  };

  return (
    <Card
      style={{
        maxWidth: 520,
        border: `1px solid ${token.colorWarningBorder}`,
        borderRadius: 12,
        boxShadow: `0 2px 12px ${token.colorWarningBg}`,
        overflow: 'hidden',
      }}
      styles={{ body: { padding: 0 } }}
      title={
        <Space>
          <SafetyCertificateOutlined style={{ color: token.colorWarning, fontSize: 18 }} />
          <Text strong style={{ fontSize: 15, color: token.colorWarningText }}>
            安全确认
          </Text>
        </Space>
      }
      headStyle={{
        background: token.colorWarningBg,
        borderBottom: `1px solid ${token.colorWarningBorder}`,
        padding: '12px 20px',
        minHeight: 'auto',
      }}
    >
      <div style={{ padding: '20px 20px 16px' }}>
        <div style={{ marginBottom: 16 }}>
          <Tag
            icon={risk.icon}
            color={risk.color}
            style={{ fontSize: 13, padding: '2px 10px', borderRadius: 6 }}
          >
            {risk.label}
          </Tag>
        </div>

        <Text strong style={{ fontSize: 15, display: 'block', marginBottom: 8 }}>
          {data.action || '执行敏感操作'}
        </Text>

        {data.details && (
          <Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 12, whiteSpace: 'pre-wrap' }}>
            {data.details}
          </Paragraph>
        )}

        {data.impact_scope && (
          <div style={{
            background: token.colorFillQuaternary,
            padding: '8px 14px',
            borderRadius: 8,
            marginBottom: 12,
          }}>
            <Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
              影响范围：{data.impact_scope}
            </Text>
          </div>
        )}

        {data.code_snippet && (
          <Collapse
            ghost
            size="small"
            items={[{
              key: 'code',
              label: <Text style={{ fontSize: 12, color: token.colorTextTertiary }}>查看命令/代码</Text>,
              children: (
                <pre style={{
                  background: '#1e1e1e',
                  color: '#d4d4d4',
                  padding: 14,
                  borderRadius: 8,
                  fontSize: 12,
                  fontFamily: "'Fira Code', 'Cascadia Code', 'Consolas', monospace",
                  overflow: 'auto',
                  maxHeight: 200,
                  lineHeight: 1.5,
                  margin: 0,
                }}>
                  {data.code_snippet}
                </pre>
              ),
            }]}
          />
        )}
      </div>

      {!resolved && (
        <div style={{
          display: 'flex',
          gap: 10,
          padding: '12px 20px',
          borderTop: `1px solid ${token.colorBorderSecondary}`,
          background: token.colorFillQuaternary,
        }}>
          <Button
            type="primary"
            icon={<CheckOutlined />}
            onClick={() => handleRespond(true)}
            loading={responding}
            style={{ flex: 1, borderRadius: 8, height: 38, fontWeight: 500 }}
          >
            确认执行
          </Button>
          <Button
            danger
            icon={<CloseOutlined />}
            onClick={() => handleRespond(false)}
            loading={responding}
            style={{ flex: 1, borderRadius: 8, height: 38, fontWeight: 500 }}
          >
            拒绝
          </Button>
        </div>
      )}

      {resolved && (
        <div style={{
          padding: '10px 20px',
          borderTop: `1px solid ${token.colorBorderSecondary}`,
          background: token.colorFillQuaternary,
          textAlign: 'center',
        }}>
          <Text type="secondary" style={{ fontSize: 12 }}>已处理</Text>
        </div>
      )}
    </Card>
  );
}
