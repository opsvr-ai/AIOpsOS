import { useState, useEffect } from 'react';
import {
  Modal,
  Steps,
  Button,
  Form,
  Input,
  Select,
  Switch,
  Space,
  Typography,
  Descriptions,
  Tag,
} from 'antd';
import type { CronJobData } from './CronJobCard';

const SCHEDULE_PRESETS = [
  { value: '*/5 * * * *', label: '每5分钟', desc: '每5分钟执行一次' },
  { value: '*/15 * * * *', label: '每15分钟', desc: '每15分钟执行一次' },
  { value: '0 * * * *', label: '每小时', desc: '每整点执行' },
  { value: '0 9 * * *', label: '每天上午9点', desc: '每天上午9:00执行' },
  { value: '0 9 * * 1-5', label: '工作日9点', desc: '周一至周五上午9:00执行' },
  { value: '0 2 * * 0', label: '每周日凌晨2点', desc: '每周日凌晨2:00执行' },
  { value: '30m', label: '30分钟后（一次）', desc: '30分钟后执行一次' },
  { value: '2h', label: '2小时后（一次）', desc: '2小时后执行一次' },
  { value: '1d', label: '1天后（一次）', desc: '1天后执行一次' },
  { value: 'once', label: '立即执行（一次）', desc: '保存后立即触发执行' },
];

interface Props {
  open: boolean;
  editing: CronJobData | null;
  saving: boolean;
  onCancel: () => void;
  onSave: (values: Record<string, unknown>) => void;
}

export default function CreateCronWizard({ open, editing, saving, onCancel, onSave }: Props) {
  const [current, setCurrent] = useState(0);
  const [form] = Form.useForm();
  const [scheduleType, setScheduleType] = useState<'preset' | 'custom'>('preset');

  const fName = Form.useWatch('name', form);
  const fPrompt = Form.useWatch('prompt', form);
  const fSchedule = Form.useWatch('schedule', form);
  const fTimezone = Form.useWatch('timezone_str', form);
  const fTimeout = Form.useWatch('timeout_seconds', form);
  const fRetries = Form.useWatch('max_retries', form);
  const fSkills: string[] | undefined = Form.useWatch('skills', form);
  const fEnabled = Form.useWatch('enabled', form);

  useEffect(() => {
    if (open && editing) {
      const isPreset = SCHEDULE_PRESETS.some((p) => p.value === editing.schedule);
      setScheduleType(isPreset ? 'preset' : 'custom');
      form.setFieldsValue({
        name: editing.name,
        prompt: editing.prompt,
        schedule: editing.schedule,
        timezone_str: editing.timezone_str || 'Asia/Shanghai',
        skills: editing.skills || [],
        enabled: editing.enabled,
        timeout_seconds: editing.timeout_seconds || undefined,
        max_retries: editing.max_retries || undefined,
      });
    } else if (open && !editing) {
      setScheduleType('preset');
      form.resetFields();
    }
    setCurrent(0);
  }, [open, editing, form]);

  const handleNext = async () => {
    try {
      await form.validateFields(
        current === 0 ? ['name', 'prompt'] : current === 1 ? ['schedule'] : [],
      );
      setCurrent((c) => c + 1);
    } catch {
      /* validation errors shown by Form */
    }
  };

  const handlePrev = () => setCurrent((c) => c - 1);

  const handleFinish = async () => {
    try {
      const values = await form.validateFields();
      onSave(values);
    } catch {
      /* validation errors */
    }
  };

  const handleCancel = () => {
    setCurrent(0);
    form.resetFields();
    onCancel();
  };

  const steps = [
    {
      title: '任务信息',
      content: (
        <div style={{ padding: '16px 0' }}>
          <Form.Item
            name="name"
            label="任务名称"
            rules={[{ required: true, message: '请输入任务名称' }]}
          >
            <Input placeholder="例如：每日系统巡检" maxLength={256} />
          </Form.Item>
          <Form.Item
            name="prompt"
            label="提示词"
            rules={[{ required: true, message: '请输入 AI 提示词' }]}
            extra="描述 AI 需要执行的具体任务和输出格式"
          >
            <Input.TextArea
              rows={6}
              placeholder="例如：检查所有服务的健康状态，汇总 CPU/内存/磁盘使用率，对异常指标生成告警建议..."
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
          </Form.Item>
          <Form.Item name="skills" label="加载技能">
            <Select mode="tags" placeholder="输入技能名称后回车" style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </div>
      ),
    },
    {
      title: '执行配置',
      content: (
        <div style={{ padding: '16px 0' }}>
          <Form.Item label="调度方式">
            <Select
              value={scheduleType}
              onChange={(v) => {
                setScheduleType(v);
                form.setFieldValue('schedule', undefined);
              }}
              options={[
                { value: 'preset', label: '预设调度' },
                { value: 'custom', label: '自定义表达式' },
              ]}
              style={{ width: 160, marginBottom: 12 }}
            />
          </Form.Item>
          {scheduleType === 'preset' ? (
            <Form.Item
              name="schedule"
              label="预设策略"
              rules={[{ required: true, message: '请选择调度策略' }]}
            >
              <Select
                options={SCHEDULE_PRESETS.map((p) => ({
                  value: p.value,
                  label: (
                    <Space direction="vertical" size={0}>
                      <Typography.Text>{p.label}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                        {p.desc}
                      </Typography.Text>
                    </Space>
                  ),
                }))}
                optionLabelProp="label"
              />
            </Form.Item>
          ) : (
            <Form.Item
              name="schedule"
              label="Cron 表达式 / 间隔"
              rules={[{ required: true, message: '请输入调度表达式' }]}
              extra="支持 5 字段 Cron 表达式（分 时 日 月 周）、间隔（30m/2h/1d）或 once"
            >
              <Input placeholder="0 9 * * *" style={{ fontFamily: 'monospace' }} />
            </Form.Item>
          )}
          <Form.Item name="timezone_str" label="时区">
            <Input placeholder="Asia/Shanghai" />
          </Form.Item>
          <Space size={16}>
            <Form.Item name="timeout_seconds" label="超时（秒）" style={{ marginBottom: 0 }}>
              <Input type="number" placeholder="300" style={{ width: 140 }} min={1} max={86400} />
            </Form.Item>
            <Form.Item name="max_retries" label="重试次数" style={{ marginBottom: 0 }}>
              <Input type="number" placeholder="0" style={{ width: 120 }} min={0} max={10} />
            </Form.Item>
          </Space>
        </div>
      ),
    },
    {
      title: '确认提交',
      content: (
        <div style={{ padding: '16px 0' }}>
          <Typography.Title level={5} style={{ marginBottom: 16 }}>
            请确认以下配置
          </Typography.Title>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="任务名称">{fName || '-'}</Descriptions.Item>
            <Descriptions.Item label="提示词">
              <Typography.Paragraph
                ellipsis={{ rows: 3, expandable: true }}
                style={{ margin: 0, fontSize: 12, fontFamily: 'monospace', maxWidth: 400 }}
              >
                {fPrompt || '-'}
              </Typography.Paragraph>
            </Descriptions.Item>
            <Descriptions.Item label="调度策略">
              <Tag color="blue" style={{ fontFamily: 'monospace' }}>
                {fSchedule || '-'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="时区">{fTimezone || 'Asia/Shanghai'}</Descriptions.Item>
            <Descriptions.Item label="超时">{fTimeout ? `${fTimeout}s` : '默认'}</Descriptions.Item>
            <Descriptions.Item label="重试">
              {fRetries ? `${fRetries} 次` : '不重试'}
            </Descriptions.Item>
            <Descriptions.Item label="加载技能">
              {fSkills && fSkills.length > 0
                ? fSkills.map((s: string) => (
                    <Tag key={s} color="blue" style={{ marginBottom: 4 }}>
                      {s}
                    </Tag>
                  ))
                : '无'}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={fEnabled !== false ? 'green' : 'default'}>
                {fEnabled !== false ? '启用' : '停用'}
              </Tag>
            </Descriptions.Item>
          </Descriptions>
        </div>
      ),
    },
  ];

  return (
    <Modal
      title={editing ? '编辑定时任务' : '创建定时任务'}
      open={open}
      onCancel={handleCancel}
      width={640}
      destroyOnHidden
      footer={
        <Space>
          <Button onClick={handleCancel}>取消</Button>
          {current > 0 && <Button onClick={handlePrev}>上一步</Button>}
          {current < steps.length - 1 && (
            <Button type="primary" onClick={handleNext}>
              下一步
            </Button>
          )}
          {current === steps.length - 1 && (
            <Button type="primary" loading={saving} onClick={handleFinish}>
              确认创建
            </Button>
          )}
        </Space>
      }
    >
      <Steps
        current={current}
        size="small"
        style={{ marginBottom: 8 }}
        items={steps.map((s) => ({ title: s.title }))}
      />
      <Form
        form={form}
        layout="vertical"
        initialValues={{ enabled: true, timezone_str: 'Asia/Shanghai', schedule: 'once' }}
      >
        {steps[current].content}
      </Form>
    </Modal>
  );
}
