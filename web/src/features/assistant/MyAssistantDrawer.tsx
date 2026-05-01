import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Drawer,
  Transfer,
  Checkbox,
  Input,
  Switch,
  Button,
  Space,
  App,
  Typography,
  theme,
  Spin,
  Divider,
  Segmented,
  Badge,
  Tag,
} from 'antd';
import {
  SaveOutlined,
  UndoOutlined,
  RobotOutlined,
  ToolOutlined,
  ExperimentOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

const { Text, Title } = Typography;
const { TextArea } = Input;

interface AssistantConfig {
  id: string;
  user_id: string;
  enabled_sub_agents: string[];
  favorite_tools: string[];
  preferred_scenarios: string[];
  custom_prompt: string | null;
  notification_prefs: Record<string, boolean>;
}

interface SubAgentItem {
  key: string;
  title: string;
  description: string;
}

interface ToolItem {
  key: string;
  title: string;
  category: string;
}

const SCENARIO_OPTIONS = [
  { key: 'ops', title: '运维操作' },
  { key: 'alert', title: '告警处理' },
  { key: 'deploy', title: '部署发布' },
  { key: 'diag', title: '故障诊断' },
  { key: 'monitor', title: '监控巡检' },
  { key: 'knowledge', title: '知识整理' },
];

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function MyAssistantDrawer({ open, onClose }: Props) {
  const { token } = theme.useToken();
  const { message: msg } = App.useApp();

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState<AssistantConfig | null>(null);

  const [subAgents, setSubAgents] = useState<SubAgentItem[]>([]);
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [toolCategory, setToolCategory] = useState<string>('all');
  const [toolSearch, setToolSearch] = useState('');

  const [enabledSubAgents, setEnabledSubAgents] = useState<string[]>([]);
  const [favoriteTools, setFavoriteTools] = useState<string[]>([]);
  const [preferredScenarios, setPreferredScenarios] = useState<string[]>([]);
  const [customPrompt, setCustomPrompt] = useState('');
  const [notifPrefs, setNotifPrefs] = useState<Record<string, boolean>>({});

  const fetchData = useCallback(async () => {
    setLoading(true);

    // Fetch sequentially so the interceptor's token-refresh only fires once.
    // Concurrent 401s can overwhelm the refresh queue and cause spurious failures.
    try {
      const cfgRes = await api.get('/assistant/config');
      const cfg: AssistantConfig = cfgRes.data;
      setConfig(cfg);
      setEnabledSubAgents(cfg.enabled_sub_agents || []);
      setFavoriteTools(cfg.favorite_tools || []);
      setPreferredScenarios(cfg.preferred_scenarios || []);
      setCustomPrompt(cfg.custom_prompt || '');
      setNotifPrefs(cfg.notification_prefs || {});
    } catch (e: any) {
      if (e?.response?.status === 401) {
        msg.error('登录已过期，请重新登录');
      } else {
        msg.error('加载助理配置失败');
      }
    }

    try {
      const agentsRes = await api.get('/agents');
      const agents: any[] = agentsRes.data ?? [];
      const subOnly = agents
        .filter((a: any) => (a.agent_type || a.type) !== 'main')
        .map((a: any) => ({
          key: a.id,
          title: a.name,
          description: a.type || a.agent_type || '',
        }));
      setSubAgents(subOnly);
    } catch {
      msg.error('加载智能体列表失败');
    }

    try {
      const toolsRes = await api.get('/tools', { params: { page_size: 200 } });
      const toolList: any[] = toolsRes.data?.items ?? [];
      setTools(
        toolList.map((t: any) => ({
          key: t.id,
          title: t.name,
          category: t.category || 'other',
        })),
      );
    } catch {
      msg.error('加载工具列表失败');
    }

    setLoading(false);
  }, [msg]);

  useEffect(() => {
    if (open) fetchData();
  }, [open, fetchData]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.put('/assistant/config', {
        enabled_sub_agents: enabledSubAgents,
        favorite_tools: favoriteTools,
        preferred_scenarios: preferredScenarios,
        custom_prompt: customPrompt || null,
        notification_prefs: notifPrefs,
      });
      msg.success('已保存');
      onClose();
    } catch {
      msg.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (config) {
      setEnabledSubAgents(config.enabled_sub_agents || []);
      setFavoriteTools(config.favorite_tools || []);
      setPreferredScenarios(config.preferred_scenarios || []);
      setCustomPrompt(config.custom_prompt || '');
      setNotifPrefs(config.notification_prefs || {});
    }
  };

  const handleScenarioToggle = (key: string) => {
    setPreferredScenarios((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  };

  const { categoryOptions, filteredTools } = useMemo(() => {
    const byCat: Record<string, ToolItem[]> = {};
    for (const t of tools) {
      const cat = t.category || 'other';
      (byCat[cat] ??= []).push(t);
    }
    const cats = Object.keys(byCat).sort();
    const options = cats.map((c) => ({
      label: `${c} (${byCat[c].length})`,
      value: c,
    }));
    const activeTools = toolCategory === 'all' ? tools : (byCat[toolCategory] ?? []);
    const filtered = toolSearch
      ? activeTools.filter((t) => t.title.toLowerCase().includes(toolSearch.toLowerCase()))
      : activeTools;
    return { categoryOptions: options, filteredTools: filtered };
  }, [tools, toolCategory, toolSearch]);

  return (
    <Drawer
      title={
        <Space>
          <RobotOutlined style={{ color: token.colorPrimary }} />
          <span>我的助理</span>
        </Space>
      }
      open={open}
      onClose={onClose}
      width={560}
      destroyOnHidden
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <Button icon={<UndoOutlined />} onClick={handleReset}>
            重置
          </Button>
          <Button type="primary" icon={<SaveOutlined />} onClick={handleSave} loading={saving}>
            保存配置
          </Button>
        </div>
      }
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
        </div>
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size="large">
          <div>
            <Title level={5} style={{ marginBottom: 8 }}>
              <RobotOutlined style={{ marginRight: 8 }} />
              子智能体
            </Title>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
              选择要在对话中启用的子智能体
            </Text>
            {subAgents.length === 0 ? (
              <Text type="secondary">暂无可用子智能体</Text>
            ) : (
              <Transfer
                dataSource={subAgents}
                targetKeys={enabledSubAgents}
                onChange={(keys) => setEnabledSubAgents(keys as string[])}
                render={(item) => item.title}
                listStyle={{ width: '100%', height: 200 }}
                titles={['可用', '已启用']}
                showSearch
                filterOption={(inputValue, item) =>
                  item.title.toLowerCase().includes(inputValue.toLowerCase())
                }
              />
            )}
          </div>

          <Divider style={{ margin: '8px 0' }} />

          <div>
            <Title level={5} style={{ marginBottom: 8 }}>
              <ToolOutlined style={{ marginRight: 8 }} />
              工具收藏
              {favoriteTools.length > 0 && (
                <Badge
                  count={favoriteTools.length}
                  style={{ marginLeft: 8, backgroundColor: token.colorPrimary }}
                  title="已收藏数量"
                />
              )}
            </Title>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
              收藏的工具将在对话中优先展示
            </Text>
            {tools.length === 0 ? (
              <Text type="secondary">暂无工具</Text>
            ) : (
              <>
                <Segmented
                  value={toolCategory}
                  onChange={(val) => setToolCategory(val as string)}
                  options={[{ label: `全部 (${tools.length})`, value: 'all' }, ...categoryOptions]}
                  size="small"
                  block
                  style={{ marginBottom: 12 }}
                />
                <Input
                  placeholder="搜索工具..."
                  value={toolSearch}
                  onChange={(e) => setToolSearch(e.target.value)}
                  allowClear
                  size="small"
                  style={{ marginBottom: 8 }}
                />
                <div
                  style={{
                    maxHeight: 220,
                    overflowY: 'auto',
                    border: `1px solid ${token.colorBorderSecondary}`,
                    borderRadius: token.borderRadius,
                    padding: '8px 12px',
                  }}
                >
                  {filteredTools.length === 0 ? (
                    <Text
                      type="secondary"
                      style={{ display: 'block', textAlign: 'center', padding: 16 }}
                    >
                      无匹配工具
                    </Text>
                  ) : (
                    <Checkbox.Group
                      value={favoriteTools}
                      onChange={(vals) => setFavoriteTools(vals as string[])}
                      style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
                    >
                      {filteredTools.map((t) => (
                        <Checkbox key={t.key} value={t.key}>
                          {t.title}
                        </Checkbox>
                      ))}
                    </Checkbox.Group>
                  )}
                </div>
              </>
            )}
          </div>

          <Divider style={{ margin: '8px 0' }} />

          <div>
            <Title level={5} style={{ marginBottom: 8 }}>
              <ExperimentOutlined style={{ marginRight: 8 }} />
              场景快捷设置
            </Title>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
              选择常用场景，助理将据此调整回答策略
            </Text>
            <Space wrap>
              {SCENARIO_OPTIONS.map((s) => (
                <Tag.CheckableTag
                  key={s.key}
                  checked={preferredScenarios.includes(s.key)}
                  onChange={() => handleScenarioToggle(s.key)}
                  style={{
                    padding: '4px 12px',
                    borderRadius: 20,
                    cursor: 'pointer',
                  }}
                >
                  {s.title}
                </Tag.CheckableTag>
              ))}
            </Space>
          </div>

          <Divider style={{ margin: '8px 0' }} />

          <div>
            <Title level={5} style={{ marginBottom: 8 }}>
              自定义提示词
            </Title>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
              可使用的变量: {'{username}'}, {'{date}'}, {'{time}'}
            </Text>
            <TextArea
              value={customPrompt}
              onChange={(e) => setCustomPrompt(e.target.value)}
              rows={5}
              placeholder="你是一个专业的运维助手..."
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
          </div>

          <Divider style={{ margin: '8px 0' }} />

          <div>
            <Title level={5} style={{ marginBottom: 8 }}>
              通知偏好
            </Title>
            <Space direction="vertical" style={{ width: '100%' }}>
              {[
                { key: 'push', label: '推送通知' },
                { key: 'email', label: '邮件通知' },
                { key: 'dingtalk', label: '钉钉通知' },
                { key: 'wecom', label: '企业微信通知' },
              ].map((item) => (
                <div
                  key={item.key}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '4px 0',
                  }}
                >
                  <Text>{item.label}</Text>
                  <Switch
                    size="small"
                    checked={notifPrefs[item.key] ?? false}
                    onChange={(v) => setNotifPrefs((prev) => ({ ...prev, [item.key]: v }))}
                  />
                </div>
              ))}
            </Space>
          </div>
        </Space>
      )}
    </Drawer>
  );
}
