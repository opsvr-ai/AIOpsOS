import { useEffect, useState } from 'react';
import {
  Drawer,
  Tabs,
  Form,
  Input,
  Select,
  Switch,
  Button,
  Space,
  App,
  Spin,
  Checkbox,
  List,
  Typography,
  Tag,
  theme,
  Empty,
} from 'antd';
import { TeamOutlined, ToolOutlined, SendOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface Agent {
  id: string;
  name: string;
  type: string;
  system_prompt: string | null;
  model_name: string;
  agent_type: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  tools: ToolRef[];
  sub_agents: AgentRef[];
  channels: ChannelRef[];
}

interface ToolRef {
  id: string;
  name: string;
  type: string;
  description: string | null;
  is_active: boolean;
}

interface AgentRef {
  id: string;
  name: string;
  type: string;
}

interface ChannelRef {
  id: string;
  name: string;
}

const TYPE_OPTIONS = [
  { value: 'main', label: '主智能体' },
  { value: 'sub', label: '子智能体' },
];

const MODEL_OPTIONS = [
  { value: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
  { value: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { value: 'gpt-4o', label: 'GPT-4o' },
];

export default function AgentEditorDrawer({
  agent,
  open,
  onClose,
  onSaved,
}: {
  agent: Agent | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const [allTools, setAllTools] = useState<ToolRef[]>([]);
  const [allAgents, setAllAgents] = useState<AgentRef[]>([]);
  const [allChannels, setAllChannels] = useState<ChannelRef[]>([]);
  const [loadingAssociations, setLoadingAssociations] = useState(false);

  const [selectedToolIds, setSelectedToolIds] = useState<string[]>([]);
  const [selectedSubIds, setSelectedSubIds] = useState<string[]>([]);
  const [selectedChannelIds, setSelectedChannelIds] = useState<string[]>([]);

  const [activeTab, setActiveTab] = useState('basic');

  const isEdit = !!agent;
  const isMain = form.getFieldValue('type') === 'main' || agent?.type === 'main';

  useEffect(() => {
    if (open) {
      if (agent) {
        form.setFieldsValue({
          name: agent.name,
          type: agent.type,
          model_name: agent.model_name,
          agent_type: agent.agent_type || '',
          system_prompt: agent.system_prompt || '',
          is_active: agent.is_active,
        });
        setSelectedToolIds(agent.tools?.map((t) => t.id) || []);
        setSelectedSubIds(agent.sub_agents?.map((a) => a.id) || []);
        setSelectedChannelIds(agent.channels?.map((c) => c.id) || []);
      } else {
        form.resetFields();
        form.setFieldsValue({
          type: 'sub',
          model_name: 'deepseek-v4-flash',
          is_active: true,
          agent_type: 'deep_agent',
        });
        setSelectedToolIds([]);
        setSelectedSubIds([]);
        setSelectedChannelIds([]);
      }
      loadAssociations();
    }
  }, [open, agent, form]);

  const loadAssociations = async () => {
    setLoadingAssociations(true);
    try {
      const [toolsRes, agentsRes, channelsRes] = await Promise.all([
        api.get('/tools', { params: { page_size: 200 } }),
        api.get('/agents'),
        api.get('/channels'),
      ]);
      setAllTools(toolsRes.data ?? []);
      setAllAgents(agentsRes.data ?? []);
      setAllChannels(channelsRes.data ?? []);
    } catch {
      // silent
    } finally {
      setLoadingAssociations(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const values = await form.validateFields();
      const body: Record<string, unknown> = {
        ...values,
        tool_ids: selectedToolIds,
        sub_agent_ids: selectedSubIds,
        channel_ids: selectedChannelIds,
      };
      if (!values.agent_type) body.agent_type = null;

      if (isEdit) {
        await api.patch(`/agents/${agent!.id}`, body);
        msg.success('更新成功');
      } else {
        await api.post('/agents', body);
        msg.success('创建成功');
      }
      onSaved();
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer
      title={isEdit ? `编辑智能体 — ${agent?.name}` : '创建智能体'}
      open={open}
      onClose={onClose}
      width={640}
      destroyOnHidden
      footer={
        <Space style={{ float: 'right' }}>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>
            {isEdit ? '保存' : '创建'}
          </Button>
        </Space>
      }
    >
      <Tabs activeKey={activeTab} onChange={setActiveTab}>
        <Tabs.TabPane tab="基本信息" key="basic">
          <Form form={form} layout="vertical">
            <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
              <Input placeholder="智能体名称" maxLength={256} />
            </Form.Item>
            <Form.Item name="type" label="类型" rules={[{ required: true }]}>
              <Select options={TYPE_OPTIONS} />
            </Form.Item>
            <Form.Item name="model_name" label="模型" rules={[{ required: true }]}>
              <Select options={MODEL_OPTIONS} />
            </Form.Item>
            <Form.Item name="agent_type" label="子类型">
              <Input placeholder="如 deep_agent" />
            </Form.Item>
            <Form.Item name="is_active" label="状态" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
          </Form>
        </Tabs.TabPane>

        <Tabs.TabPane tab="系统提示词" key="prompt">
          <Form form={form} layout="vertical">
            <Form.Item name="system_prompt" style={{ marginBottom: 0 }}>
              <Input.TextArea
                rows={16}
                placeholder="输入系统提示词..."
                style={{ fontFamily: 'monospace', fontSize: 13 }}
              />
            </Form.Item>
          </Form>
        </Tabs.TabPane>

        <Tabs.TabPane tab="工具关联" key="tools">
          {loadingAssociations ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Spin />
            </div>
          ) : (
            <>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
                选择此智能体可调用的工具。未选中的工具将无法被调用。
              </Typography.Paragraph>
              <Checkbox.Group
                value={selectedToolIds}
                onChange={(vals) => setSelectedToolIds(vals as string[])}
                style={{ width: '100%' }}
              >
                <List
                  dataSource={allTools}
                  renderItem={(tool) => (
                    <List.Item
                      style={{
                        padding: '8px 12px',
                        borderRadius: 8,
                        marginBottom: 4,
                        background: selectedToolIds.includes(tool.id)
                          ? token.colorPrimaryBg || `${token.colorPrimary}10`
                          : (token.colorBgElevated ?? token.colorBgContainer),
                        border: `1px solid ${
                          selectedToolIds.includes(tool.id)
                            ? token.colorPrimaryBorder || token.colorPrimary
                            : token.colorBorderSecondary
                        }`,
                      }}
                    >
                      <Checkbox value={tool.id}>
                        <Space>
                          <ToolOutlined style={{ color: token.colorPrimary }} />
                          <Typography.Text strong style={{ fontSize: 13 }}>
                            {tool.name}
                          </Typography.Text>
                          <Tag
                            color={
                              tool.type === 'skill'
                                ? 'blue'
                                : tool.type === 'mcp'
                                  ? 'purple'
                                  : 'green'
                            }
                            style={{ borderRadius: 4, fontSize: 10 }}
                          >
                            {tool.type}
                          </Tag>
                          {tool.description && (
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              {tool.description.slice(0, 60)}
                              {tool.description.length > 60 ? '…' : ''}
                            </Typography.Text>
                          )}
                        </Space>
                      </Checkbox>
                    </List.Item>
                  )}
                  locale={{ emptyText: <Empty description="暂无可用工具" /> }}
                />
              </Checkbox.Group>
            </>
          )}
        </Tabs.TabPane>

        {isMain && (
          <Tabs.TabPane tab="子智能体" key="subs">
            {loadingAssociations ? (
              <div style={{ textAlign: 'center', padding: 40 }}>
                <Spin />
              </div>
            ) : (
              <>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
                  选择主智能体可以委托的子智能体。仅子智能体（type=sub）可选。
                </Typography.Paragraph>
                <Checkbox.Group
                  value={selectedSubIds}
                  onChange={(vals) => setSelectedSubIds(vals as string[])}
                  style={{ width: '100%' }}
                >
                  <List
                    dataSource={allAgents.filter((a) => a.id !== agent?.id && a.type !== 'main')}
                    renderItem={(sub) => (
                      <List.Item
                        style={{
                          padding: '8px 12px',
                          borderRadius: 8,
                          marginBottom: 4,
                          background: selectedSubIds.includes(sub.id)
                            ? token.colorPrimaryBg || `${token.colorPrimary}10`
                            : (token.colorBgElevated ?? token.colorBgContainer),
                          border: `1px solid ${
                            selectedSubIds.includes(sub.id)
                              ? token.colorPrimaryBorder || token.colorPrimary
                              : token.colorBorderSecondary
                          }`,
                        }}
                      >
                        <Checkbox value={sub.id}>
                          <Space>
                            <TeamOutlined style={{ color: token.colorSuccess }} />
                            <Typography.Text strong style={{ fontSize: 13 }}>
                              {sub.name}
                            </Typography.Text>
                          </Space>
                        </Checkbox>
                      </List.Item>
                    )}
                    locale={{ emptyText: <Empty description="暂无子智能体" /> }}
                  />
                </Checkbox.Group>
              </>
            )}
          </Tabs.TabPane>
        )}

        <Tabs.TabPane tab="通知渠道" key="channels">
          {loadingAssociations ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Spin />
            </div>
          ) : (
            <>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
                选择此智能体可用的通知渠道。
              </Typography.Paragraph>
              <Checkbox.Group
                value={selectedChannelIds}
                onChange={(vals) => setSelectedChannelIds(vals as string[])}
                style={{ width: '100%' }}
              >
                <List
                  dataSource={allChannels}
                  renderItem={(ch) => (
                    <List.Item
                      style={{
                        padding: '8px 12px',
                        borderRadius: 8,
                        marginBottom: 4,
                        background: selectedChannelIds.includes(ch.id)
                          ? token.colorPrimaryBg || `${token.colorPrimary}10`
                          : (token.colorBgElevated ?? token.colorBgContainer),
                        border: `1px solid ${
                          selectedChannelIds.includes(ch.id)
                            ? token.colorPrimaryBorder || token.colorPrimary
                            : token.colorBorderSecondary
                        }`,
                      }}
                    >
                      <Checkbox value={ch.id}>
                        <Space>
                          <SendOutlined style={{ color: token.colorWarning }} />
                          <Typography.Text strong style={{ fontSize: 13 }}>
                            {ch.name}
                          </Typography.Text>
                        </Space>
                      </Checkbox>
                    </List.Item>
                  )}
                  locale={{ emptyText: <Empty description="暂无通知渠道" /> }}
                />
              </Checkbox.Group>
            </>
          )}
        </Tabs.TabPane>
      </Tabs>
    </Drawer>
  );
}
