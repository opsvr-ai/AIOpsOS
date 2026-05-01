import { useEffect, useState, useMemo } from 'react';
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
  Input as SearchInput,
  Tooltip,
} from 'antd';
import {
  TeamOutlined,
  ToolOutlined,
  SendOutlined,
  SearchOutlined,
  LockOutlined,
} from '@ant-design/icons';
import MDEditor from '@uiw/react-md-editor';
import api from '@/services/api';
import { useThemeStore } from '@/stores/themeStore';
import { useAuthStore } from '@/stores/authStore';

interface Agent {
  id: string;
  name: string;
  type: string;
  system_prompt: string | null;
  user_prompt: string | null;
  model_name: string;
  agent_type: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  viewable_roles: string[];
  editable_roles: string[];
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

const TOOL_TYPE_META: Record<string, { label: string; color: string }> = {
  skill: { label: '技能', color: 'blue' },
  mcp: { label: 'MCP', color: 'purple' },
  builtin: { label: '内置', color: 'green' },
};

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
  const mode = useThemeStore((s) => s.mode);

  const [systemPrompt, setSystemPrompt] = useState('');
  const [userPrompt, setUserPrompt] = useState('');

  const [allTools, setAllTools] = useState<ToolRef[]>([]);
  const [allAgents, setAllAgents] = useState<AgentRef[]>([]);
  const [allChannels, setAllChannels] = useState<ChannelRef[]>([]);
  const [loadingAssociations, setLoadingAssociations] = useState(false);

  const [selectedToolIds, setSelectedToolIds] = useState<string[]>([]);
  const [selectedSubIds, setSelectedSubIds] = useState<string[]>([]);
  const [selectedChannelIds, setSelectedChannelIds] = useState<string[]>([]);

  const [activeTab, setActiveTab] = useState('basic');
  const [agentType, setAgentType] = useState<string>('sub');
  const [toolSearch, setToolSearch] = useState('');
  const [toolTypeFilter, setToolTypeFilter] = useState<string>('all');

  const isEdit = !!agent;
  const isMain = agentType === 'main';
  const userRoles = useAuthStore((s) => s.user?.roles ?? []);
  const isAdmin = userRoles.includes('admin');
  const canEditUserPrompt =
    isAdmin ||
    (agent?.editable_roles?.length ?? 0) === 0 ||
    agent?.editable_roles?.some((r) => userRoles.includes(r));

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
        setAgentType(agent.type);
        setSystemPrompt(agent.system_prompt || '');
        setUserPrompt(agent.user_prompt || '');
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
        setAgentType('sub');
        setSystemPrompt('');
        setUserPrompt('');
        setSelectedToolIds([]);
        setSelectedSubIds([]);
        setSelectedChannelIds([]);
      }
      setToolSearch('');
      setToolTypeFilter('all');
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
      setAllTools(toolsRes.data?.items ?? []);
      setAllAgents(agentsRes.data ?? []);
      setAllChannels(channelsRes.data ?? []);
    } catch {
      // silent
    } finally {
      setLoadingAssociations(false);
    }
  };

  const filteredTools = useMemo(() => {
    let tools = allTools;
    if (toolTypeFilter !== 'all') {
      tools = tools.filter((t) => t.type === toolTypeFilter);
    }
    if (toolSearch.trim()) {
      const q = toolSearch.trim().toLowerCase();
      tools = tools.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          (t.description && t.description.toLowerCase().includes(q)),
      );
    }
    return tools;
  }, [allTools, toolTypeFilter, toolSearch]);

  const toolTypeCounts = useMemo(() => {
    const counts: Record<string, number> = { all: allTools.length };
    for (const t of allTools) {
      counts[t.type] = (counts[t.type] || 0) + 1;
    }
    return counts;
  }, [allTools]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const values = await form.validateFields();
      const body: Record<string, unknown> = {
        ...values,
        system_prompt: systemPrompt || null,
        user_prompt: userPrompt || null,
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

  const toolTabItems = [
    { key: 'all', label: `全部 (${toolTypeCounts.all || 0})` },
    ...Object.entries(TOOL_TYPE_META).map(([key, meta]) => ({
      key,
      label: `${meta.label} (${toolTypeCounts[key] || 0})`,
    })),
  ];

  return (
    <Drawer
      title={isEdit ? `编辑智能体 — ${agent?.name}` : '创建智能体'}
      open={open}
      onClose={onClose}
      width={900}
      destroyOnHidden
      styles={{
        body: { padding: '0 24px 24px' },
      }}
      footer={
        <Space style={{ float: 'right' }}>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>
            {isEdit ? '保存' : '创建'}
          </Button>
        </Space>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        destroyInactiveTabPane={false}
        style={{ marginTop: -8 }}
        items={[
          {
            key: 'basic',
            label: '基本信息',
            children: (
              <div style={{ maxWidth: 500 }}>
                <Form form={form} layout="vertical">
                  <Form.Item
                    name="name"
                    label="名称"
                    rules={[{ required: true, message: '请输入名称' }]}
                  >
                    <Input placeholder="智能体名称" maxLength={256} />
                  </Form.Item>
                  <Form.Item name="type" label="类型" rules={[{ required: true }]}>
                    <Select options={TYPE_OPTIONS} onChange={(val) => setAgentType(val)} />
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
              </div>
            ),
          },
          {
            key: 'system_prompt',
            label: (
              <span>
                系统提示词
                {!isAdmin && (
                  <LockOutlined
                    style={{ marginLeft: 6, fontSize: 12, color: token.colorTextTertiary }}
                  />
                )}
              </span>
            ),
            children: (
              <div>
                {!isAdmin && (
                  <div
                    style={{
                      padding: '8px 14px',
                      borderRadius: 8,
                      marginBottom: 12,
                      background: token.colorWarningBg || '#FFFBE6',
                      border: `1px solid ${token.colorWarningBorder || '#FFE58F'}`,
                      fontSize: 13,
                      color: token.colorWarningText || '#AD6800',
                    }}
                  >
                    系统提示词仅限管理员编辑。如需修改请联系管理员。
                  </div>
                )}
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
                  定义智能体的角色、能力和行为规范。支持 Markdown 格式，右侧可实时预览。
                </Typography.Paragraph>
                <div data-color-mode={mode === 'dark' ? 'dark' : 'light'}>
                  <MDEditor
                    value={systemPrompt}
                    onChange={(val) => setSystemPrompt(val || '')}
                    height={420}
                    visibleDragbar={false}
                    preview={isAdmin ? 'live' : 'preview'}
                  />
                </div>
              </div>
            ),
          },
          {
            key: 'user_prompt',
            label: (
              <span>
                用户提示词
                {!canEditUserPrompt && (
                  <LockOutlined
                    style={{ marginLeft: 6, fontSize: 12, color: token.colorTextTertiary }}
                  />
                )}
              </span>
            ),
            children: (
              <div>
                {!canEditUserPrompt && (
                  <div
                    style={{
                      padding: '8px 14px',
                      borderRadius: 8,
                      marginBottom: 12,
                      background: token.colorWarningBg || '#FFFBE6',
                      border: `1px solid ${token.colorWarningBorder || '#FFE58F'}`,
                      fontSize: 13,
                      color: token.colorWarningText || '#AD6800',
                    }}
                  >
                    你无权编辑此智能体的用户提示词。如需修改请联系管理员。
                  </div>
                )}
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
                  设置用户消息的默认前缀，用于注入上下文、指令或格式要求。支持 Markdown 格式。
                </Typography.Paragraph>
                <div data-color-mode={mode === 'dark' ? 'dark' : 'light'}>
                  <MDEditor
                    value={userPrompt}
                    onChange={(val) => setUserPrompt(val || '')}
                    height={420}
                    visibleDragbar={false}
                    preview={canEditUserPrompt ? 'live' : 'preview'}
                  />
                </div>
              </div>
            ),
          },
          {
            key: 'tools',
            label: '工具关联',
            children: loadingAssociations ? (
              <div style={{ textAlign: 'center', padding: 40 }}>
                <Spin />
              </div>
            ) : (
              <div>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
                  选择此智能体可调用的工具。未选中的工具将无法被调用。
                </Typography.Paragraph>
                <Space style={{ marginBottom: 12, width: '100%' }} direction="vertical" size={8}>
                  <SearchInput
                    placeholder="搜索工具名称或描述..."
                    prefix={<SearchOutlined style={{ color: token.colorTextTertiary }} />}
                    value={toolSearch}
                    onChange={(e) => setToolSearch(e.target.value)}
                    allowClear
                    style={{ width: '100%' }}
                  />
                  <Tabs
                    activeKey={toolTypeFilter}
                    onChange={setToolTypeFilter}
                    size="small"
                    items={toolTabItems}
                    style={{ marginBottom: -16 }}
                  />
                </Space>
                <Checkbox.Group
                  value={selectedToolIds}
                  onChange={(vals) => setSelectedToolIds(vals as string[])}
                  style={{ width: '100%' }}
                >
                  <List
                    dataSource={filteredTools}
                    style={{ maxHeight: 340, overflow: 'auto' }}
                    renderItem={(tool) => {
                      const typeMeta = TOOL_TYPE_META[tool.type] || {
                        label: tool.type,
                        color: 'default',
                      };
                      const isSelected = selectedToolIds.includes(tool.id);
                      return (
                        <List.Item
                          style={{
                            padding: '10px 14px',
                            borderRadius: 8,
                            marginBottom: 4,
                            background: isSelected
                              ? token.colorPrimaryBg || `${token.colorPrimary}10`
                              : (token.colorBgElevated ?? token.colorBgContainer),
                            border: `1px solid ${
                              isSelected
                                ? token.colorPrimaryBorder || token.colorPrimary
                                : token.colorBorderSecondary
                            }`,
                            transition: 'all 0.2s',
                          }}
                        >
                          <Checkbox value={tool.id} style={{ width: '100%' }}>
                            <div
                              style={{
                                display: 'flex',
                                alignItems: 'flex-start',
                                justifyContent: 'space-between',
                                width: '100%',
                                gap: 12,
                              }}
                            >
                              <Space size={4}>
                                <ToolOutlined style={{ color: token.colorPrimary, fontSize: 13 }} />
                                <Typography.Text strong style={{ fontSize: 13 }}>
                                  {tool.name}
                                </Typography.Text>
                                <Tag
                                  color={typeMeta.color}
                                  style={{ borderRadius: 4, fontSize: 10, margin: 0 }}
                                >
                                  {typeMeta.label}
                                </Tag>
                              </Space>
                              {tool.description && (
                                <Tooltip
                                  title={tool.description}
                                  placement="left"
                                  overlayStyle={{ maxWidth: 360 }}
                                >
                                  <Typography.Text
                                    type="secondary"
                                    style={{
                                      fontSize: 12,
                                      flex: 1,
                                      textAlign: 'right',
                                      overflow: 'hidden',
                                      textOverflow: 'ellipsis',
                                      whiteSpace: 'nowrap',
                                      maxWidth: 300,
                                    }}
                                  >
                                    {tool.description}
                                  </Typography.Text>
                                </Tooltip>
                              )}
                            </div>
                          </Checkbox>
                        </List.Item>
                      );
                    }}
                    locale={{
                      emptyText: (
                        <Empty
                          description={
                            toolSearch.trim() || toolTypeFilter !== 'all'
                              ? '没有匹配的工具'
                              : '暂无可用工具'
                          }
                        />
                      ),
                    }}
                  />
                </Checkbox.Group>
              </div>
            ),
          },
          ...(isMain
            ? [
                {
                  key: 'subs' as const,
                  label: '子智能体',
                  children: loadingAssociations ? (
                    <div style={{ textAlign: 'center', padding: 40 }}>
                      <Spin />
                    </div>
                  ) : (
                    <>
                      <Typography.Paragraph
                        type="secondary"
                        style={{ fontSize: 12, marginBottom: 12 }}
                      >
                        选择主智能体可以委托的子智能体。仅子智能体（type=sub）可选。
                      </Typography.Paragraph>
                      <Checkbox.Group
                        value={selectedSubIds}
                        onChange={(vals) => setSelectedSubIds(vals as string[])}
                        style={{ width: '100%' }}
                      >
                        <List
                          dataSource={allAgents.filter(
                            (a) => a.id !== agent?.id && a.type !== 'main',
                          )}
                          style={{ maxHeight: 360, overflow: 'auto' }}
                          renderItem={(sub) => (
                            <List.Item
                              style={{
                                padding: '10px 14px',
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
                                transition: 'all 0.2s',
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
                  ),
                },
              ]
            : []),
          {
            key: 'channels',
            label: '通知渠道',
            children: loadingAssociations ? (
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
                    style={{ maxHeight: 360, overflow: 'auto' }}
                    renderItem={(ch) => (
                      <List.Item
                        style={{
                          padding: '10px 14px',
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
                          transition: 'all 0.2s',
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
            ),
          },
        ]}
      />
    </Drawer>
  );
}
