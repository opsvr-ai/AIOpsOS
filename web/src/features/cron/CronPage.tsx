import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Button,
  Space,
  Typography,
  Tag,
  Popconfirm,
  App,
  Empty,
  Spin,
  theme,
  Modal,
  Form,
  Input,
  Select,
  Switch,
  Tooltip,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  ReloadOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
  PauseCircleOutlined,
  CaretRightOutlined,
  HistoryOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

interface CronJobData {
  id: string;
  name: string;
  prompt: string;
  schedule: string;
  timezone_str: string;
  skills: string[];
  enabled_toolsets: string[];
  delivery: Record<string, unknown> | null;
  enabled: boolean;
  last_run: string | null;
  next_run: string | null;
  last_output: string | null;
  created_at: string;
  updated_at: string;
}

const SCHEDULE_PRESETS = [
  { value: '*/5 * * * *', label: '每5分钟' },
  { value: '0 * * * *', label: '每小时' },
  { value: '0 9 * * *', label: '每天上午9点' },
  { value: '0 9 * * 1-5', label: '工作日9点' },
  { value: '30m', label: '30分钟后一次' },
  { value: '2h', label: '2小时后一次' },
  { value: '1d', label: '1天后一次' },
  { value: 'once', label: '立即执行一次' },
];

export default function CronPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();
  const [jobs, setJobs] = useState<CronJobData[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<CronJobData | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const [scheduleType, setScheduleType] = useState<'preset' | 'custom'>('preset');

  const loadJobs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/cron/jobs');
      setJobs(res.data ?? []);
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ enabled: true, timezone_str: 'Asia/Shanghai', schedule: 'once' });
    setScheduleType('preset');
    setModalOpen(true);
  };

  const openEdit = (job: CronJobData) => {
    setEditing(job);
    form.setFieldsValue(job);
    const isPreset = SCHEDULE_PRESETS.some((p) => p.value === job.schedule);
    setScheduleType(isPreset ? 'preset' : 'custom');
    setModalOpen(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const values = await form.validateFields();
      if (editing) {
        await api.patch(`/cron/jobs/${editing.id}`, values);
        msg.success('更新成功');
      } else {
        await api.post('/cron/jobs', values);
        msg.success('创建成功');
      }
      setModalOpen(false);
      loadJobs();
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    await api.delete(`/cron/jobs/${id}`);
    msg.success('删除成功');
    loadJobs();
  };

  const handleTrigger = async (id: string) => {
    await api.post(`/cron/jobs/${id}/trigger`);
    msg.success('已触发，将在下次轮询时执行');
    loadJobs();
  };

  const handleToggle = async (job: CronJobData) => {
    await api.patch(`/cron/jobs/${job.id}`, { enabled: !job.enabled });
    loadJobs();
  };

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
        }}
      >
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            定时任务
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            管理周期性 AI 任务，支持 Cron 表达式和间隔调度
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadJobs}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            创建任务
          </Button>
        </Space>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      ) : jobs.length === 0 ? (
        <Empty description="暂无定时任务" style={{ paddingTop: 80 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            创建第一个任务
          </Button>
        </Empty>
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {jobs.map((job) => (
            <Card
              key={job.id}
              size="small"
              styles={{ body: { padding: '16px 20px' } }}
              style={{
                borderLeft: `3px solid ${job.enabled ? token.colorPrimary : token.colorBorderSecondary}`,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'flex-start',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Space align="center" style={{ marginBottom: 4 }}>
                    <Typography.Text strong style={{ fontSize: 15 }}>
                      {job.name}
                    </Typography.Text>
                    <Tag color={job.enabled ? 'green' : 'default'}>
                      {job.enabled ? '运行中' : '已停用'}
                    </Tag>
                  </Space>
                  <Typography.Paragraph
                    type="secondary"
                    style={{ margin: '0 0 8px 0', fontSize: 13 }}
                    ellipsis={{ rows: 2 }}
                  >
                    {job.prompt.slice(0, 200)}
                  </Typography.Paragraph>
                  <Space size={16} wrap>
                    <Space size={4}>
                      <ClockCircleOutlined
                        style={{ color: token.colorTextTertiary, fontSize: 12 }}
                      />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {job.schedule}
                      </Typography.Text>
                    </Space>
                    {job.next_run && (
                      <Space size={4}>
                        <HistoryOutlined style={{ color: token.colorTextTertiary, fontSize: 12 }} />
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          下次: {new Date(job.next_run).toLocaleString('zh-CN')}
                        </Typography.Text>
                      </Space>
                    )}
                    {job.last_run && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        上次: {new Date(job.last_run).toLocaleString('zh-CN')}
                      </Typography.Text>
                    )}
                    {job.skills?.length > 0 && (
                      <Space size={4}>
                        {job.skills.map((s) => (
                          <Tag key={s} color="blue" style={{ fontSize: 10, margin: 0 }}>
                            {s}
                          </Tag>
                        ))}
                      </Space>
                    )}
                  </Space>
                </div>
                <Space style={{ flexShrink: 0, marginLeft: 16 }}>
                  <Tooltip title={job.enabled ? '停用' : '启用'}>
                    <Button
                      size="small"
                      icon={job.enabled ? <PauseCircleOutlined /> : <CaretRightOutlined />}
                      onClick={() => handleToggle(job)}
                    />
                  </Tooltip>
                  <Tooltip title="手动触发">
                    <Button
                      size="small"
                      icon={<ThunderboltOutlined />}
                      onClick={() => handleTrigger(job.id)}
                    />
                  </Tooltip>
                  <Button size="small" onClick={() => openEdit(job)}>
                    编辑
                  </Button>
                  <Popconfirm title="确认删除此任务？" onConfirm={() => handleDelete(job.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              </div>
            </Card>
          ))}
        </Space>
      )}

      <Modal
        title={editing ? '编辑定时任务' : '创建定时任务'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        width={600}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="任务名称" maxLength={256} />
          </Form.Item>
          <Form.Item name="prompt" label="提示词" rules={[{ required: true }]}>
            <Input.TextArea
              rows={5}
              placeholder="AI 任务提示词..."
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
          </Form.Item>
          <Form.Item label="调度策略">
            <Select
              value={scheduleType}
              onChange={(v) => setScheduleType(v)}
              options={[
                { value: 'preset', label: '预设' },
                { value: 'custom', label: '自定义' },
              ]}
              style={{ width: 120, marginBottom: 8 }}
            />
          </Form.Item>
          {scheduleType === 'preset' ? (
            <Form.Item name="schedule" label="预设" rules={[{ required: true }]}>
              <Select options={SCHEDULE_PRESETS} />
            </Form.Item>
          ) : (
            <Form.Item
              name="schedule"
              label="Cron 表达式 / 间隔"
              rules={[{ required: true }]}
              extra="支持 Cron 表达式 (5字段)、间隔 ('30m', '2h', '1d') 或 'once'"
            >
              <Input placeholder="0 9 * * *" />
            </Form.Item>
          )}
          <Form.Item name="timezone_str" label="时区">
            <Input placeholder="Asia/Shanghai" />
          </Form.Item>
          <Form.Item name="skills" label="技能">
            <Select mode="tags" placeholder="输入技能名称后回车" />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
