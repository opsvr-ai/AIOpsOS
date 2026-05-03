import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Button,
  Modal,
  Form,
  Input,
  Select,
  Space,
  Typography,
  Tabs,
  Switch,
  App,
  Empty,
  Row,
  Col,
  Spin,
  InputNumber,
  TimePicker,
} from 'antd';
import {
  PlusOutlined,
  ThunderboltOutlined,
  AlertOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import api from '@/services/api';
import ScheduleCard from './ScheduleCard';
import TriggerRuleCard from './TriggerRuleCard';
import CronBuilder from './CronBuilder';
import dayjs from 'dayjs';

const { Title } = Typography;

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

interface ScenarioOption {
  id: string;
  name: string;
}

export default function AutomationPage() {
  const { message: msg } = App.useApp();

  const [scenarios, setScenarios] = useState<ScenarioOption[]>([]);
  const [scenarioMap, setScenarioMap] = useState<Record<string, string>>({});
  const [tab, setTab] = useState('schedules');
  const [loading, setLoading] = useState(true);

  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [executions, setExecutions] = useState<Record<string, Execution[]>>({});

  const [triggers, setTriggers] = useState<TriggerRule[]>([]);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);
  const [editingTrigger, setEditingTrigger] = useState<TriggerRule | null>(null);
  const [form] = Form.useForm();
  const [triggerForm] = Form.useForm();

  const fetchScenarios = useCallback(async () => {
    try {
      const res = await api.get('/scenarios');
      const data: ScenarioOption[] = res.data ?? [];
      setScenarios(data);
      const map: Record<string, string> = {};
      data.forEach((s) => {
        map[s.id] = s.name;
      });
      setScenarioMap(map);
    } catch {
      /* ignore */
    }
  }, []);

  const fetchSchedules = useCallback(async () => {
    try {
      const res = await api.get('/schedules');
      const data: Schedule[] = res.data ?? [];
      setSchedules(data);
      const execResults = await Promise.allSettled(
        data.map((s) => api.get(`/schedules/${s.id}/executions`)),
      );
      const newExecs: Record<string, Execution[]> = {};
      execResults.forEach((result, i) => {
        newExecs[data[i].id] =
          result.status === 'fulfilled' ? ((result.value.data as Execution[]) ?? []) : [];
      });
      setExecutions((prev) => ({ ...prev, ...newExecs }));
    } catch {
      msg.error('加载调度失败');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchTriggers = useCallback(async () => {
    try {
      const res = await api.get('/triggers');
      setTriggers(res.data ?? []);
    } catch {
      msg.error('加载触发规则失败');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchScenarios(), fetchSchedules(), fetchTriggers()]).finally(() =>
      setLoading(false),
    );
  }, [fetchScenarios, fetchSchedules, fetchTriggers]);

  const refresh = () => {
    setLoading(true);
    const fetchers =
      tab === 'schedules'
        ? [fetchScenarios(), fetchSchedules()]
        : [fetchScenarios(), fetchTriggers()];
    Promise.all(fetchers).finally(() => setLoading(false));
  };

  const handleCreateSchedule = () => {
    setEditingSchedule(null);
    setEditingTrigger(null);
    form.resetFields();
    form.setFieldsValue({ is_active: true });
    setModalOpen(true);
  };

  const handleEditSchedule = (s: Schedule) => {
    setEditingSchedule(s);
    setEditingTrigger(null);
    form.setFieldsValue({
      name: s.name,
      cron_expression: s.cron_expression,
      scenario_id: s.scenario_id,
      params: s.params ? JSON.stringify(s.params, null, 2) : '',
      is_active: s.is_active,
    });
    setModalOpen(true);
  };

  const handleScheduleSubmit = async (values: Record<string, unknown>) => {
    const payload = {
      ...values,
      params: values.params
        ? (() => {
            try {
              return JSON.parse(values.params as string);
            } catch {
              return {};
            }
          })()
        : {},
    };
    try {
      if (editingSchedule) {
        await api.patch(`/schedules/${editingSchedule.id}`, payload);
        msg.success('更新成功');
      } else {
        await api.post('/schedules', payload);
        msg.success('创建成功');
      }
      setModalOpen(false);
      form.resetFields();
      fetchSchedules();
    } catch {
      msg.error(editingSchedule ? '更新失败' : '创建失败');
    }
  };

  const handleToggleSchedule = async (id: string, active: boolean) => {
    try {
      await api.patch(`/schedules/${id}`, { is_active: active });
      setSchedules((prev) => prev.map((s) => (s.id === id ? { ...s, is_active: active } : s)));
    } catch {
      msg.error('操作失败');
    }
  };

  const handleDeleteSchedule = async (id: string) => {
    try {
      await api.delete(`/schedules/${id}`);
      msg.success('已删除');
      fetchSchedules();
    } catch {
      msg.error('删除失败');
    }
  };

  const handleCreateTrigger = () => {
    setEditingTrigger(null);
    setEditingSchedule(null);
    triggerForm.resetFields();
    triggerForm.setFieldsValue({ is_active: true });
    setModalOpen(true);
  };

  const handleEditTrigger = (t: TriggerRule) => {
    setEditingTrigger(t);
    setEditingSchedule(null);
    triggerForm.setFieldsValue({
      name: t.name,
      condition: JSON.stringify(t.condition, null, 2),
      scenario_id: t.scenario_id,
      frequency_limit: t.frequency_limit,
      time_window_start: t.time_window_start ? dayjs(t.time_window_start, 'HH:mm:ss') : null,
      time_window_end: t.time_window_end ? dayjs(t.time_window_end, 'HH:mm:ss') : null,
      is_active: t.is_active,
    });
    setModalOpen(true);
  };

  const handleTriggerSubmit = async (values: Record<string, unknown>) => {
    const payload = {
      ...values,
      condition: (() => {
        try {
          return JSON.parse(values.condition as string);
        } catch {
          return {};
        }
      })(),
      time_window_start: values.time_window_start
        ? dayjs(values.time_window_start as string).format('HH:mm:ss')
        : null,
      time_window_end: values.time_window_end
        ? dayjs(values.time_window_end as string).format('HH:mm:ss')
        : null,
    };
    try {
      if (editingTrigger) {
        await api.patch(`/triggers/${editingTrigger.id}`, payload);
        msg.success('更新成功');
      } else {
        await api.post('/triggers', payload);
        msg.success('创建成功');
      }
      setModalOpen(false);
      triggerForm.resetFields();
      fetchTriggers();
    } catch {
      msg.error(editingTrigger ? '更新失败' : '创建失败');
    }
  };

  const handleToggleTrigger = async (id: string, active: boolean) => {
    try {
      await api.patch(`/triggers/${id}`, { is_active: active });
      setTriggers((prev) => prev.map((t) => (t.id === id ? { ...t, is_active: active } : t)));
    } catch {
      msg.error('操作失败');
    }
  };

  const handleDeleteTrigger = async (id: string) => {
    try {
      await api.delete(`/triggers/${id}`);
      msg.success('已删除');
      fetchTriggers();
    } catch {
      msg.error('删除失败');
    }
  };

  const isEditing = !!editingSchedule || !!editingTrigger;
  const isScheduleForm = !editingTrigger;

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
        }}
      >
        <Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          自动化
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading}>
            刷新
          </Button>
          {tab === 'schedules' ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreateSchedule}>
              创建调度
            </Button>
          ) : (
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreateTrigger}>
              创建触发规则
            </Button>
          )}
        </Space>
      </div>

      <Tabs
        activeKey={tab}
        onChange={(k) => {
          setTab(k);
          setModalOpen(false);
        }}
        items={[
          {
            key: 'schedules',
            label: (
              <span>
                <ThunderboltOutlined style={{ marginRight: 6 }} />
                调度任务
              </span>
            ),
            children: (
              <Spin spinning={loading}>
                {schedules.length === 0 ? (
                  <Card style={{ borderRadius: 12, textAlign: 'center', padding: 40 }}>
                    <Empty description="暂无调度任务" />
                  </Card>
                ) : (
                  <Row gutter={[12, 12]}>
                    {schedules.map((s) => (
                      <Col key={s.id} xs={24} sm={12} md={8} lg={6}>
                        <ScheduleCard
                          schedule={s}
                          executions={executions[s.id] || []}
                          onToggle={handleToggleSchedule}
                          onEdit={handleEditSchedule}
                          onDelete={handleDeleteSchedule}
                          scenarios={scenarioMap}
                        />
                      </Col>
                    ))}
                  </Row>
                )}
              </Spin>
            ),
          },
          {
            key: 'triggers',
            label: (
              <span>
                <AlertOutlined style={{ marginRight: 6 }} />
                触发规则
              </span>
            ),
            children: (
              <Spin spinning={loading}>
                {triggers.length === 0 ? (
                  <Card style={{ borderRadius: 12, textAlign: 'center', padding: 40 }}>
                    <Empty description="暂无触发规则" />
                  </Card>
                ) : (
                  <Row gutter={[12, 12]}>
                    {triggers.map((t) => (
                      <Col key={t.id} xs={24} sm={12} md={8} lg={6}>
                        <TriggerRuleCard
                          trigger={t}
                          onToggle={handleToggleTrigger}
                          onEdit={handleEditTrigger}
                          onDelete={handleDeleteTrigger}
                          scenarios={scenarioMap}
                        />
                      </Col>
                    ))}
                  </Row>
                )}
              </Spin>
            ),
          },
        ]}
      />

      <Modal
        title={
          editingSchedule
            ? '编辑调度'
            : editingTrigger
              ? '编辑触发规则'
              : tab === 'schedules'
                ? '创建调度'
                : '创建触发规则'
        }
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
          triggerForm.resetFields();
        }}
        onOk={() => {
          if (editingTrigger || (!editingSchedule && tab === 'triggers' && !editingSchedule)) {
            triggerForm.submit();
          } else {
            form.submit();
          }
        }}
        okText={isEditing ? '保存' : '创建'}
        width={600}
        destroyOnHidden
      >
        {isScheduleForm && !editingTrigger ? (
          <Form form={form} layout="vertical" onFinish={handleScheduleSubmit}>
            <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
              <Input placeholder="调度名称" />
            </Form.Item>

            <Form.Item
              name="cron_expression"
              label="调度规则"
              rules={[{ required: true, message: '请设置调度规则' }]}
            >
              <CronBuilder />
            </Form.Item>

            <Form.Item
              name="scenario_id"
              label="场景"
              rules={[{ required: true, message: '请选择场景' }]}
            >
              <Select
                showSearch
                placeholder="选择执行场景"
                filterOption={(input, option) =>
                  ((option?.label as string) || '').toLowerCase().includes(input.toLowerCase())
                }
                options={scenarios.map((s) => ({ label: s.name, value: s.id }))}
              />
            </Form.Item>

            <Form.Item name="params" label="参数 (JSON)">
              <Input.TextArea rows={3} placeholder='{"key": "value"}' />
            </Form.Item>

            <Form.Item name="is_active" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Form>
        ) : (
          <Form form={triggerForm} layout="vertical" onFinish={handleTriggerSubmit}>
            <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
              <Input placeholder="触发规则名称" />
            </Form.Item>

            <Form.Item
              name="condition"
              label="触发条件 (JSON)"
              rules={[{ required: true, message: '请输入条件' }]}
            >
              <Input.TextArea
                rows={5}
                placeholder={
                  '{"type": "simple", "field": "severity", "op": "eq", "value": "critical"}'
                }
                style={{ fontFamily: 'monospace', fontSize: 12 }}
              />
            </Form.Item>

            <Form.Item
              name="scenario_id"
              label="场景"
              rules={[{ required: true, message: '请选择场景' }]}
            >
              <Select
                showSearch
                placeholder="选择场景"
                filterOption={(input, option) =>
                  ((option?.label as string) || '').toLowerCase().includes(input.toLowerCase())
                }
                options={scenarios.map((s) => ({ label: s.name, value: s.id }))}
              />
            </Form.Item>

            <Space size={12}>
              <Form.Item name="frequency_limit" label="频率限制 (次/小时)">
                <InputNumber min={1} placeholder="不限" style={{ width: 120 }} />
              </Form.Item>
            </Space>

            <Space size={12}>
              <Form.Item name="time_window_start" label="时间窗口起">
                <TimePicker format="HH:mm:ss" />
              </Form.Item>
              <Form.Item name="time_window_end" label="时间窗口止">
                <TimePicker format="HH:mm:ss" />
              </Form.Item>
            </Space>

            <Form.Item name="is_active" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Form>
        )}
      </Modal>
    </div>
  );
}
