import { Card, Tag, Switch, Typography, Tooltip, Popconfirm, Button, Space, theme } from 'antd';
import {
  ClockCircleOutlined,
  ThunderboltOutlined,
  HistoryOutlined,
  EditOutlined,
  DeleteOutlined,
  FileTextOutlined,
} from '@ant-design/icons';

const { Text, Paragraph } = Typography;

interface CronJobData {
  id: string;
  name: string;
  prompt: string;
  schedule: string;
  timezone_str: string;
  skills: string[];
  enabled_toolsets: string[];
  delivery: Record<string, unknown> | null;
  timeout_seconds: string | null;
  max_retries: string | null;
  enabled: boolean;
  last_run: string | null;
  next_run: string | null;
  last_output: string | null;
  created_at: string;
  updated_at: string;
}

interface Props {
  job: CronJobData;
  index?: number;
  onToggle: (job: CronJobData) => void;
  onTrigger: (id: string) => void;
  onEdit: (job: CronJobData) => void;
  onDelete: (id: string) => void;
  onViewOutput: (job: CronJobData) => void;
}

export type { CronJobData };

export default function CronJobCard({
  job,
  index = 0,
  onToggle,
  onTrigger,
  onEdit,
  onDelete,
  onViewOutput,
}: Props) {
  const { token } = theme.useToken();

  return (
    <Card
      hoverable
      size="small"
      className="cron-card-enter"
      style={{
        borderRadius: 12,
        height: '100%',
        animationDelay: `${index * 40}ms`,
      }}
      styles={{ body: { padding: '12px 16px 16px' } }}
      title={
        <div
          style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
            <Text strong ellipsis style={{ fontSize: 14, maxWidth: 140 }}>
              {job.name}
            </Text>
            <Tag
              color={job.enabled ? 'green' : 'default'}
              style={{
                margin: 0,
                fontSize: 11,
                lineHeight: '18px',
                padding: '0 6px',
                flexShrink: 0,
              }}
            >
              {job.enabled ? '运行中' : '已停用'}
            </Tag>
          </div>
          <Switch size="small" checked={job.enabled} onChange={() => onToggle(job)} />
        </div>
      }
      actions={[
        <Tooltip title="手动触发" key="trigger">
          <Button
            type="text"
            size="small"
            icon={<ThunderboltOutlined />}
            onClick={() => onTrigger(job.id)}
          />
        </Tooltip>,
        <Tooltip title="编辑" key="edit">
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => onEdit(job)} />
        </Tooltip>,
        <Popconfirm key="delete" title="确认删除此任务？" onConfirm={() => onDelete(job.id)}>
          <Button type="text" size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>,
      ]}
    >
      {/* Prompt preview */}
      <Paragraph
        type="secondary"
        ellipsis={{ rows: 2 }}
        style={{ fontSize: 12, marginBottom: 10, lineHeight: '18px', minHeight: 36 }}
      >
        {job.prompt.slice(0, 200)}
      </Paragraph>

      {/* Schedule + execution info */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', marginBottom: 6 }}>
        <Space size={4}>
          <ClockCircleOutlined style={{ color: token.colorTextTertiary, fontSize: 11 }} />
          <Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>
            {job.schedule}
          </Text>
        </Space>
        {job.next_run && (
          <Space size={4}>
            <HistoryOutlined style={{ color: token.colorTextTertiary, fontSize: 11 }} />
            <Text type="secondary" style={{ fontSize: 11 }}>
              下次:{' '}
              {new Date(job.next_run).toLocaleString('zh-CN', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
              })}
            </Text>
          </Space>
        )}
        {job.last_run && (
          <Text type="secondary" style={{ fontSize: 11 }}>
            上次:{' '}
            {new Date(job.last_run).toLocaleString('zh-CN', {
              month: '2-digit',
              day: '2-digit',
              hour: '2-digit',
              minute: '2-digit',
            })}
          </Text>
        )}
      </div>

      {/* Skills tags */}
      {job.skills?.length > 0 && (
        <div style={{ marginBottom: 6 }}>
          {job.skills.map((s) => (
            <Tag key={s} color="blue" style={{ fontSize: 10, margin: '0 4px 2px 0' }}>
              {s}
            </Tag>
          ))}
        </div>
      )}

      {/* Last output preview */}
      <div style={{ borderTop: `1px solid ${token.colorBorder}`, paddingTop: 8, marginTop: 4 }}>
        {job.last_output ? (
          <div style={{ cursor: 'pointer' }} onClick={() => onViewOutput(job)}>
            <Space size={4}>
              <FileTextOutlined style={{ fontSize: 11, color: token.colorTextTertiary }} />
              <Text
                type="secondary"
                ellipsis
                style={{ fontSize: 11, fontFamily: 'monospace', maxWidth: 180, lineHeight: '16px' }}
              >
                {job.last_output.slice(0, 80).replace(/\n/g, ' ')}
              </Text>
              <Text style={{ fontSize: 10, color: token.colorPrimary, flexShrink: 0 }}>查看 →</Text>
            </Space>
          </div>
        ) : (
          <Text type="secondary" style={{ fontSize: 11, fontStyle: 'italic' }}>
            暂无执行记录
          </Text>
        )}
      </div>
    </Card>
  );
}
