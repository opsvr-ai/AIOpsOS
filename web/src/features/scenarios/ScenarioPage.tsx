import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Button,
  Modal,
  Form,
  Input,
  Space,
  Typography,
  Tag,
  Popconfirm,
  App,
  Empty,
  theme,
  Select,
  Switch,
  Tabs,
  Drawer,
  Descriptions,
  Timeline,
  Progress,
  Row,
  Col,
  Statistic,
  Tooltip,
  Badge,
  Divider,
  InputNumber,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  HistoryOutlined,
  SettingOutlined,
  TeamOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  ClockCircleOutlined,
  EditOutlined,
  EyeOutlined,
  ThunderboltOutlined,
  MessageOutlined,
  RobotOutlined,
  ToolOutlined,
  BookOutlined,
  SendOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

interface Scenario {
  id: string;
  name: string;
  description: string | null;
  trigger_command: string;
  is_active: boolean;
  created_at: string;
  scenario_type?: 'command' | 'natural_language' | 'hybrid';
  nl_prompt?: string;
  execution_timeout?: number;
  enable_collaboration?: boolean;
  collaboration_config?: {
    auto_create_group?: boolean;
    group_name_template?: string;
    send_email?: boolean;
    email_recipients?: string[];
  };
  tools?: { id: string; name: string }[];
  agents?: { id: string; name: string }[];
}

interface ScenarioExecution {
  id: string;
  scenario_id: string;
  trigger_type: string;
  trigger_source: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'timeout';
  params: Record<string, unknown>;
  result: {
    output?: string;
    recommendations?: string[];
    metrics?: { duration_ms?: number; steps_completed?: number };
  };
  logs: { timestamp: string; level: string; message: string }[];
  started_at: string | null;
  completed_at: string | null;
  collaboration_session_id: string | null;
  created_at: string;
}

const scenarioTypeOptions = [
  { value: 'command', label: '命令式', description: '通过触发命令执行预定义操作' },
  { value: 'natural_language', label: '自然语言式', description: '通过自然语言描述执行智能操作' },
  { value: 'hybrid', label: '混合式', description: '结合命令和自然语言的混合模式' },
];

const statusConfig: Record<string, { color: string; icon: React.ReactNode; text: string }> = {
  pending: { color: 'default', icon: <ClockCircleOutlined />, text: '等待中' },
  running: { color: 'processing', icon: <SyncOutlined spin />, text: '执行中' },
  completed: { color: 'success', icon: <CheckCircleOutlined />, text: '已完成' },
  failed: { color: 'error', icon: <CloseCircleOutlined />, text: '失败' },
  timeout: { color: 'warning', icon: <ClockCircleOutlined />, text: '超时' },
};

export default function ScenarioPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();
  const [items, setItems] = useState<Scenario[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [editingScenario, setEditingScenario] = useState<Scenario | null>(null);
  const [detailDrawer, setDetailDrawer] = useState(false);
  const [selectedScenario, setSelectedScenario] = useState<Scenario | null>(null);
  const [executions, setExecutions] = useState<ScenarioExecution[]>([]);
  const [executionsLoading, setExecutionsLoading] = useState(false);
  const [form] = Form.useForm();
  const [scenarioType, setScenarioType] = useState<string>('command');
  const [enableCollab, setEnableCollab] = useState(false);

  // 统计数据
  const [stats, setStats] = useState({
    total: 0,
    active: 0,
    withCollab: 0,
    executionsToday: 0,
  });

  const fetch = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/scenarios');
      const data = res.data ?? [];
      setItems(data);
      // 计算统计
      setStats({
        total: data.length,
        active: data.filter((s: Scenario) => s.is_active).length,
        withCollab: data.filter((s: Scenario) => s.enable_collaboration).length,
        executionsToday: 0, // TODO: 从后端获取
      });
    } catch {
      msg.error('加载失败');
    } finally {
      setLoading(false);
    }
  }, [msg]);

  const fetchExecutions = useCallback(
    async (scenarioId: string) => {
      setExecutionsLoading(true);
      try {
        const res = await api.get(`/scenarios/${scenarioId}/executions`);
        setExecutions(res.data ?? []);
      } catch {
        // 如果接口不存在，使用空数组
        setExecutions([]);
      } finally {
        setExecutionsLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    fetch();
  }, [fetch]);

  const handleCreate = async (values: Record<string, unknown>) => {
    try {
      const payload = {
        ...values,
        scenario_type: values.scenario_type || 'command',
        enable_collaboration: values.enable_collaboration || false,
        collaboration_config: values.enable_collaboration
          ? {
              auto_create_group: values.auto_create_group,
              group_name_template: values.group_name_template,
              send_email: values.send_email,
              email_recipients: values.email_recipients
                ? String(values.email_recipients)
                    .split(',')
                    .map((e) => e.trim())
                : [],
            }
          : {},
      };
      if (editingScenario) {
        await api.put(`/scenarios/${editingScenario.id}`, payload);
        msg.success('更新成功');
      } else {
        await api.post('/scenarios', payload);
        msg.success('创建成功');
      }
      setOpen(false);
      setEditingScenario(null);
      form.resetFields();
      setScenarioType('command');
      setEnableCollab(false);
      fetch();
    } catch {
      msg.error(editingScenario ? '更新失败' : '创建失败');
    }
  };

  const handleEdit = (scenario: Scenario) => {
    setEditingScenario(scenario);
    setScenarioType(scenario.scenario_type || 'command');
    setEnableCollab(scenario.enable_collaboration || false);
    form.setFieldsValue({
      ...scenario,
      email_recipients: scenario.collaboration_config?.email_recipients?.join(', '),
      auto_create_group: scenario.collaboration_config?.auto_create_group,
      group_name_template: scenario.collaboration_config?.group_name_template,
      send_email: scenario.collaboration_config?.send_email,
    });
    setOpen(true);
  };

  const handleViewDetail = (scenario: Scenario) => {
    setSelectedScenario(scenario);
    setDetailDrawer(true);
    fetchExecutions(scenario.id);
  };

  const handleExecute = async (scenario: Scenario) => {
    try {
      await api.post(`/scenarios/${scenario.id}/execute`, {});
      msg.success('场景已触发执行');
      if (selectedScenario?.id === scenario.id) {
        fetchExecutions(scenario.id);
      }
    } catch {
      msg.error('执行失败');
    }
  };

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (v: string, r: Scenario) => (
        <Space>
          <ExperimentOutlined style={{ color: token.colorPrimary }} />
          <span style={{ fontWeight: 500 }}>{v}</span>
          {r.enable_collaboration && (
            <Tooltip title="已启用应急协同">
              <TeamOutlined style={{ color: token.colorSuccess }} />
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'scenario_type',
      key: 'scenario_type',
      width: 120,
      render: (v: string) => {
        const typeMap: Record<string, { color: string; text: string }> = {
          command: { color: 'blue', text: '命令式' },
          natural_language: { color: 'purple', text: '自然语言' },
          hybrid: { color: 'cyan', text: '混合式' },
        };
        const config = typeMap[v] || typeMap.command;
        return <Tag color={config.color}>{config.text}</Tag>;
      },
    },
    {
      title: '触发命令',
      dataIndex: 'trigger_command',
      key: 'trigger_command',
      width: 140,
      render: (v: string) => <Tag style={{ borderRadius: 4, fontFamily: 'monospace' }}>{v}</Tag>,
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: '关联资源',
      key: 'resources',
      width: 160,
      render: (_: unknown, r: Scenario) => (
        <Space size={4}>
          {r.tools && r.tools.length > 0 && (
            <Tooltip title={`${r.tools.length} 个工具`}>
              <Tag icon={<ToolOutlined />}>{r.tools.length}</Tag>
            </Tooltip>
          )}
          {r.agents && r.agents.length > 0 && (
            <Tooltip title={`${r.agents.length} 个智能体`}>
              <Tag icon={<RobotOutlined />}>{r.agents.length}</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (v: boolean) => (
        <Badge status={v ? 'success' : 'default'} text={v ? '启用' : '停用'} />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_: unknown, r: Scenario) => (
        <Space size={4}>
          <Tooltip title="执行">
            <Button
              type="text"
              icon={<PlayCircleOutlined />}
              size="small"
              onClick={() => handleExecute(r)}
            />
          </Tooltip>
          <Tooltip title="详情">
            <Button
              type="text"
              icon={<EyeOutlined />}
              size="small"
              onClick={() => handleViewDetail(r)}
            />
          </Tooltip>
          <Tooltip title="编辑">
            <Button type="text" icon={<EditOutlined />} size="small" onClick={() => handleEdit(r)} />
          </Tooltip>
          <Popconfirm
            title="确定删除？"
            onConfirm={async () => {
              try {
                await api.delete(`/scenarios/${r.id}`);
                fetch();
                msg.success('已删除');
              } catch {
                msg.error('删除失败');
              }
            }}
          >
            <Button type="text" danger icon={<DeleteOutlined />} size="small" />
          </Popconfirm>
        </Space>
      ),
    },
  ];

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
        <Typography.Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          场景运维
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          创建场景
        </Button>
      </div>

      {/* 统计卡片 */}
      <Row gutter={16} style={{ marginBottom: 20 }}>
        <Col span={6}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic
              title="场景总数"
              value={stats.total}
              prefix={<ExperimentOutlined style={{ color: token.colorPrimary }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic
              title="已启用"
              value={stats.active}
              prefix={<CheckCircleOutlined style={{ color: token.colorSuccess }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic
              title="应急协同"
              value={stats.withCollab}
              prefix={<TeamOutlined style={{ color: token.colorWarning }} />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" style={{ borderRadius: 8 }}>
            <Statistic
              title="今日执行"
              value={stats.executionsToday}
              prefix={<ThunderboltOutlined style={{ color: token.colorInfo }} />}
            />
          </Card>
        </Col>
      </Row>

      <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={items}
          columns={columns}
          rowKey="id"
          pagination={{ pageSize: 10 }}
          size="middle"
          loading={loading}
          locale={{ emptyText: <Empty description="暂无场景" /> }}
        />
      </Card>

      {/* 创建/编辑弹窗 */}
      <Modal
        title={editingScenario ? '编辑场景' : '创建场景'}
        open={open}
        width={640}
        onCancel={() => {
          setOpen(false);
          setEditingScenario(null);
          form.resetFields();
          setScenarioType('command');
          setEnableCollab(false);
        }}
        onOk={() => form.submit()}
        okText={editingScenario ? '保存' : '创建'}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="name" label="名称" rules={[{ required: true }]}>
                <Input placeholder="场景名称" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="trigger_command" label="触发命令" rules={[{ required: true }]}>
                <Input placeholder="/command" />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="scenario_type" label="场景类型" initialValue="command">
                <Select
                  options={scenarioTypeOptions}
                  onChange={(v) => setScenarioType(v)}
                  optionRender={(option) => (
                    <Space direction="vertical" size={0}>
                      <span>{option.data.label}</span>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {option.data.description}
                      </Typography.Text>
                    </Space>
                  )}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="execution_timeout" label="执行超时(秒)" initialValue={300}>
                <InputNumber min={30} max={3600} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>

          {(scenarioType === 'natural_language' || scenarioType === 'hybrid') && (
            <Form.Item name="nl_prompt" label="自然语言提示词">
              <Input.TextArea rows={3} placeholder="描述场景执行的目标和上下文..." />
            </Form.Item>
          )}

          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="场景描述" />
          </Form.Item>

          <Divider style={{ margin: '16px 0' }}>
            <Space>
              <TeamOutlined />
              应急协同配置
            </Space>
          </Divider>

          <Form.Item
            name="enable_collaboration"
            label="启用应急协同"
            valuePropName="checked"
            initialValue={false}
          >
            <Switch onChange={(v) => setEnableCollab(v)} />
          </Form.Item>

          {enableCollab && (
            <>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item
                    name="auto_create_group"
                    label="自动创建群聊"
                    valuePropName="checked"
                    initialValue={true}
                  >
                    <Switch />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item
                    name="send_email"
                    label="发送邮件通知"
                    valuePropName="checked"
                    initialValue={true}
                  >
                    <Switch />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item
                name="group_name_template"
                label="群聊名称模板"
                initialValue="[应急] {scenario_name} - {timestamp}"
              >
                <Input placeholder="[应急] {scenario_name} - {timestamp}" />
              </Form.Item>
              <Form.Item name="email_recipients" label="邮件接收人">
                <Input placeholder="多个邮箱用逗号分隔" />
              </Form.Item>
            </>
          )}

          <Form.Item name="is_active" label="启用状态" valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      {/* 详情抽屉 */}
      <Drawer
        title={
          <Space>
            <ExperimentOutlined />
            {selectedScenario?.name}
          </Space>
        }
        open={detailDrawer}
        onClose={() => {
          setDetailDrawer(false);
          setSelectedScenario(null);
          setExecutions([]);
        }}
        width={720}
        extra={
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={() => selectedScenario && handleExecute(selectedScenario)}
          >
            执行
          </Button>
        }
      >
        {selectedScenario && (
          <Tabs
            items={[
              {
                key: 'info',
                label: (
                  <span>
                    <SettingOutlined /> 基本信息
                  </span>
                ),
                children: (
                  <div>
                    <Descriptions column={2} bordered size="small">
                      <Descriptions.Item label="场景类型">
                        {scenarioTypeOptions.find((o) => o.value === selectedScenario.scenario_type)
                          ?.label || '命令式'}
                      </Descriptions.Item>
                      <Descriptions.Item label="触发命令">
                        <Tag style={{ fontFamily: 'monospace' }}>
                          {selectedScenario.trigger_command}
                        </Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="执行超时">
                        {selectedScenario.execution_timeout || 300} 秒
                      </Descriptions.Item>
                      <Descriptions.Item label="状态">
                        <Badge
                          status={selectedScenario.is_active ? 'success' : 'default'}
                          text={selectedScenario.is_active ? '启用' : '停用'}
                        />
                      </Descriptions.Item>
                      <Descriptions.Item label="应急协同" span={2}>
                        {selectedScenario.enable_collaboration ? (
                          <Space>
                            <Tag color="success" icon={<TeamOutlined />}>
                              已启用
                            </Tag>
                            {selectedScenario.collaboration_config?.auto_create_group && (
                              <Tag icon={<MessageOutlined />}>自动建群</Tag>
                            )}
                            {selectedScenario.collaboration_config?.send_email && (
                              <Tag icon={<SendOutlined />}>邮件通知</Tag>
                            )}
                          </Space>
                        ) : (
                          <Tag>未启用</Tag>
                        )}
                      </Descriptions.Item>
                      <Descriptions.Item label="描述" span={2}>
                        {selectedScenario.description || '-'}
                      </Descriptions.Item>
                      {selectedScenario.nl_prompt && (
                        <Descriptions.Item label="自然语言提示" span={2}>
                          {selectedScenario.nl_prompt}
                        </Descriptions.Item>
                      )}
                    </Descriptions>

                    <Divider orientation="left" style={{ marginTop: 24 }}>
                      关联资源
                    </Divider>
                    <Space wrap>
                      {selectedScenario.tools?.map((t) => (
                        <Tag key={t.id} icon={<ToolOutlined />}>
                          {t.name}
                        </Tag>
                      ))}
                      {selectedScenario.agents?.map((a) => (
                        <Tag key={a.id} icon={<RobotOutlined />} color="purple">
                          {a.name}
                        </Tag>
                      ))}
                      {(!selectedScenario.tools || selectedScenario.tools.length === 0) &&
                        (!selectedScenario.agents || selectedScenario.agents.length === 0) && (
                          <Typography.Text type="secondary">暂无关联资源</Typography.Text>
                        )}
                    </Space>
                  </div>
                ),
              },
              {
                key: 'executions',
                label: (
                  <span>
                    <HistoryOutlined /> 执行记录
                  </span>
                ),
                children: (
                  <div>
                    {executionsLoading ? (
                      <div style={{ textAlign: 'center', padding: 40 }}>
                        <SyncOutlined spin style={{ fontSize: 24 }} />
                      </div>
                    ) : executions.length === 0 ? (
                      <Empty description="暂无执行记录" />
                    ) : (
                      <Timeline
                        items={executions.slice(0, 10).map((exec) => {
                          const config = statusConfig[exec.status] || statusConfig.pending;
                          return {
                            color: config.color,
                            dot: config.icon,
                            children: (
                              <div>
                                <Space>
                                  <Tag color={config.color}>{config.text}</Tag>
                                  <Typography.Text type="secondary">
                                    {new Date(exec.created_at).toLocaleString('zh-CN')}
                                  </Typography.Text>
                                </Space>
                                <div style={{ marginTop: 8 }}>
                                  <Typography.Text>
                                    触发方式:{' '}
                                    {exec.trigger_type === 'manual'
                                      ? '手动'
                                      : exec.trigger_type === 'schedule'
                                        ? '定时'
                                        : '规则触发'}
                                  </Typography.Text>
                                  {exec.result?.metrics?.duration_ms && (
                                    <Typography.Text style={{ marginLeft: 16 }}>
                                      耗时: {(exec.result.metrics.duration_ms / 1000).toFixed(2)}s
                                    </Typography.Text>
                                  )}
                                </div>
                                {exec.collaboration_session_id && (
                                  <Tag
                                    color="blue"
                                    icon={<TeamOutlined />}
                                    style={{ marginTop: 8 }}
                                  >
                                    已创建协同会话
                                  </Tag>
                                )}
                              </div>
                            ),
                          };
                        })}
                      />
                    )}
                  </div>
                ),
              },
            ]}
          />
        )}
      </Drawer>
    </div>
  );
}
