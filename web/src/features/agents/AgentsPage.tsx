import { useEffect, useState, useCallback, useMemo } from 'react';
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
  Row,
  Col,
  Checkbox,
  Tooltip,
  Drawer,
  Select,
  Input,
  Tabs,
  Segmented,
  Table,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  PlusOutlined,
  DeleteOutlined,
  ReloadOutlined,
  SearchOutlined,
  TeamOutlined,
  EditOutlined,
  StopOutlined,
  CheckCircleOutlined,
  HistoryOutlined,
  ThunderboltOutlined,
  ClearOutlined,
  ToolOutlined,
  SendOutlined,
  AppstoreOutlined,
  UnorderedListOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import api from '@/services/api';
import AgentEditorDrawer from './AgentEditorDrawer';
import AgentProfileList from './AgentProfileList';

// ── Helpers ──────────────────────────────────────────────────────────────────

function nameToGradient(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  const h = Math.abs(hash) % 360;
  return `linear-gradient(135deg, hsl(${h}, 65%, 55%), hsl(${(h + 35) % 360}, 65%, 45%))`;
}

function nameToInitial(name: string): string {
  const m = name.match(/[A-Za-z0-9]/);
  return (m ? m[0] : name.charAt(0)).toUpperCase();
}

// ── Types ────────────────────────────────────────────────────────────────────

interface AgentData {
  id: string;
  name: string;
  type: string;
  system_prompt: string | null;
  user_prompt: string | null;
  model_name: string;
  agent_type: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  is_builtin: boolean;
  viewable_roles: string[];
  editable_roles: string[];
  created_at: string;
  updated_at: string;
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

interface AgentVersion {
  id: string;
  agent_id: string;
  name: string;
  system_prompt: string | null;
  model_name: string;
  agent_type: string | null;
  config: Record<string, unknown>;
  created_at: string;
}

export default function AgentsPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();

  const [agents, setAgents] = useState<AgentData[]>([]);
  const [loading, setLoading] = useState(true);

  const [filterType, setFilterType] = useState<string>('all');
  const [filterName, setFilterName] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [viewMode, setViewMode] = useState<'card' | 'table'>('card');

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const [editorOpen, setEditorOpen] = useState(false);
  const [editAgent, setEditAgent] = useState<AgentData | null>(null);

  const [versionOpen, setVersionOpen] = useState(false);
  const [versionAgent, setVersionAgent] = useState<AgentData | null>(null);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('agents');

  const fetch = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/agents');
      let data: AgentData[] = res.data ?? [];
      if (filterType !== 'all') data = data.filter((a) => a.type === filterType);
      if (filterName.trim()) data = data.filter((a) => a.name.includes(filterName.trim()));
      if (filterStatus === 'active') data = data.filter((a) => a.is_active);
      if (filterStatus === 'inactive') data = data.filter((a) => !a.is_active);
      setAgents(data);
    } catch {
      msg.error('加载失败');
    } finally {
      setLoading(false);
    }
  }, [msg, filterType, filterName, filterStatus]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  // ── Stats ──────────────────────────────────────────────────────────────

  const stats = useMemo(() => {
    const active = agents.filter((a) => a.is_active).length;
    return {
      total: agents.length,
      active,
      inactive: agents.length - active,
      builtin: agents.filter((a) => a.is_builtin).length,
    };
  }, [agents]);

  // ── Selection ──────────────────────────────────────────────────────────

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === agents.length) setSelectedIds(new Set());
    else setSelectedIds(new Set(agents.map((a) => a.id)));
  };

  const clearSelection = () => setSelectedIds(new Set());

  const handleToggle = async (agent: AgentData) => {
    try {
      await api.patch(`/agents/${agent.id}`, { is_active: !agent.is_active });
      fetch();
    } catch {
      msg.error('操作失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/agents/${id}`);
      msg.success('已删除');
      setSelectedIds((prev) => {
        const n = new Set(prev);
        n.delete(id);
        return n;
      });
      fetch();
    } catch {
      msg.error('删除失败');
    }
  };

  const handleBatchToggle = async (active: boolean) => {
    let ok = 0;
    for (const id of selectedIds) {
      try {
        await api.patch(`/agents/${id}`, { is_active: active });
        ok++;
      } catch {
        /* skip */
      }
    }
    if (ok > 0) msg.success(`已${active ? '启用' : '停用'} ${ok} 个智能体`);
    clearSelection();
    fetch();
  };

  const handleReload = async () => {
    try {
      await api.post('/agents/reload');
      msg.success('已重新加载');
      fetch();
    } catch {
      msg.error('重新加载失败');
    }
  };

  const handleSeed = async () => {
    try {
      const res = await api.post('/agents/seed');
      msg.success(`已创建 ${res.data.created} 个，更新 ${res.data.updated} 个`);
      fetch();
    } catch {
      msg.error('种子数据注入失败');
    }
  };

  const openCreate = () => {
    setEditAgent(null);
    setEditorOpen(true);
  };
  const openEdit = (agent: AgentData) => {
    setEditAgent(agent);
    setEditorOpen(true);
  };

  const openVersions = async (agent: AgentData) => {
    setVersionAgent(agent);
    setVersionOpen(true);
    setVersionsLoading(true);
    try {
      const res = await api.get(`/agents/${agent.id}/versions`);
      setVersions(res.data ?? []);
    } catch {
      msg.error('加载版本历史失败');
    } finally {
      setVersionsLoading(false);
    }
  };

  const handleRollback = async (versionId: string) => {
    if (!versionAgent) return;
    try {
      await api.post(`/agents/${versionAgent.id}/rollback`, { version_id: versionId });
      msg.success('已回滚');
      setVersionOpen(false);
      fetch();
    } catch {
      msg.error('回滚失败');
    }
  };

  const selectedCount = selectedIds.size;

  // ── Table columns ──────────────────────────────────────────────────────

  const tableColumns: ColumnsType<AgentData> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: AgentData) => (
        <Space size={8}>
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 8,
              background: nameToGradient(name),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontSize: 12,
              fontWeight: 600,
              flexShrink: 0,
            }}
          >
            {nameToInitial(name)}
          </div>
          <Space size={4}>
            <Typography.Text strong style={{ fontSize: 13 }}>
              {name}
            </Typography.Text>
            {record.is_builtin && (
              <Tag
                icon={<SafetyCertificateOutlined />}
                color="gold"
                style={{ borderRadius: 4, fontSize: 10, margin: 0 }}
              >
                内置
              </Tag>
            )}
          </Space>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 90,
      render: (t: string) => (
        <Tag color={t === 'main' ? 'orange' : 'green'} style={{ borderRadius: 4, fontSize: 11 }}>
          {t === 'main' ? '主智能体' : '子智能体'}
        </Tag>
      ),
    },
    {
      title: '模型',
      dataIndex: 'model_name',
      key: 'model_name',
      width: 160,
      render: (m: string) => (
        <Typography.Text style={{ fontSize: 12, fontFamily: 'monospace' }}>{m}</Typography.Text>
      ),
    },
    {
      title: '工具',
      key: 'tools',
      width: 60,
      render: (_: unknown, r: AgentData) => r.tools?.length || 0,
    },
    {
      title: '子智能体',
      key: 'subs',
      width: 80,
      render: (_: unknown, r: AgentData) => r.sub_agents?.length || 0,
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 70,
      render: (active: boolean) => (
        <Tag color={active ? 'green' : 'default'} style={{ borderRadius: 4, fontSize: 11 }}>
          {active ? '启用' : '停用'}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_: unknown, r: AgentData) => (
        <Space size={2}>
          <Tooltip title="编辑">
            <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          </Tooltip>
          <Tooltip title="版本历史">
            <Button
              type="text"
              size="small"
              icon={<HistoryOutlined />}
              onClick={() => openVersions(r)}
            />
          </Tooltip>
          {!r.is_builtin && (
            <>
              <Tooltip title={r.is_active ? '停用' : '启用'}>
                <Button
                  type="text"
                  size="small"
                  icon={r.is_active ? <StopOutlined /> : <CheckCircleOutlined />}
                  onClick={() => handleToggle(r)}
                />
              </Tooltip>
              <Popconfirm
                title="确定删除？"
                onConfirm={() => handleDelete(r.id)}
                okText="删除"
                cancelText="取消"
              >
                <Button type="text" danger size="small" icon={<DeleteOutlined />} />
              </Popconfirm>
            </>
          )}
        </Space>
      ),
    },
  ];

  const renderStatsBar = () => (
    <Row gutter={[12, 12]} style={{ marginBottom: 20 }}>
      {(
        [
          { label: '智能体总数', value: stats.total, color: '#0f172a' },
          { label: '已启用', value: stats.active, color: token.colorSuccess },
          { label: '已停用', value: stats.inactive, color: token.colorError },
          { label: '内置', value: stats.builtin, color: token.colorWarning },
        ] as const
      ).map((s) => (
        <Col xs={12} sm={6} key={s.label}>
          <Card
            size="small"
            style={{
              borderRadius: 12,
              textAlign: 'center',
              border: `1px solid ${token.colorBorderSecondary}`,
              boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
            }}
          >
            <Typography.Text style={{ fontSize: 12, color: token.colorTextTertiary }}>
              {s.label}
            </Typography.Text>
            <div style={{ fontSize: 24, fontWeight: 700, color: s.color, lineHeight: 1.3 }}>
              {s.value}
            </div>
          </Card>
        </Col>
      ))}
    </Row>
  );

  const renderAgentCard = (agent: AgentData) => {
    const isSelected = selectedIds.has(agent.id);
    const toolCount = agent.tools?.length || 0;
    const subCount = agent.sub_agents?.length || 0;
    const channelCount = agent.channels?.length || 0;
    return (
      <Col xs={24} sm={12} lg={8} xl={6} key={agent.id}>
        <Card
          hoverable
          size="small"
          style={{
            borderRadius: 14,
            height: '100%',
            borderColor: isSelected ? token.colorPrimary : token.colorBorderSecondary,
            boxShadow: isSelected
              ? `0 0 0 2px ${token.colorPrimary}20, 0 2px 8px rgba(0,0,0,0.06)`
              : '0 2px 8px rgba(0,0,0,0.04)',
            overflow: 'hidden',
            transition: 'box-shadow 0.2s, border-color 0.2s',
          }}
          onClick={(e) => {
            const target = e.target as HTMLElement;
            if (target.closest('button') || target.closest('.ant-tag')) return;
            toggleSelect(agent.id);
          }}
        >
          {/* Header with avatar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <Checkbox
              checked={isSelected}
              onChange={() => toggleSelect(agent.id)}
              onClick={(e) => e.stopPropagation()}
            />
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: nameToGradient(agent.name),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#fff',
                fontSize: 16,
                fontWeight: 600,
                flexShrink: 0,
              }}
            >
              {nameToInitial(agent.name)}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span
                  style={{
                    color: agent.is_active ? token.colorSuccess : token.colorTextTertiary,
                    fontSize: 8,
                    flexShrink: 0,
                  }}
                >
                  ●
                </span>
                <Typography.Text
                  strong
                  ellipsis={{ tooltip: agent.name }}
                  style={{ fontSize: 14, flex: 1 }}
                >
                  {agent.name}
                </Typography.Text>
              </div>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {agent.model_name}
              </Typography.Text>
            </div>
          </div>

          {/* Tags */}
          <Space wrap size={[4, 4]} style={{ marginBottom: 10 }}>
            {agent.is_builtin && (
              <Tag
                icon={<SafetyCertificateOutlined />}
                color="gold"
                style={{ borderRadius: 6, fontSize: 11, margin: 0 }}
              >
                内置
              </Tag>
            )}
            <Tag
              color={agent.type === 'main' ? 'orange' : 'green'}
              style={{ borderRadius: 6, fontSize: 11, margin: 0 }}
            >
              {agent.type === 'main' ? '主智能体' : '子智能体'}
            </Tag>
            {agent.agent_type && (
              <Tag style={{ borderRadius: 6, fontSize: 11, margin: 0 }}>{agent.agent_type}</Tag>
            )}
            {toolCount > 0 && (
              <Tag
                color="blue"
                style={{ borderRadius: 6, fontSize: 11, margin: 0 }}
                icon={<ToolOutlined />}
              >
                {toolCount} 工具
              </Tag>
            )}
            {subCount > 0 && (
              <Tag
                color="green"
                style={{ borderRadius: 6, fontSize: 11, margin: 0 }}
                icon={<TeamOutlined />}
              >
                {subCount} 子智能体
              </Tag>
            )}
            {channelCount > 0 && (
              <Tag
                color="purple"
                style={{ borderRadius: 6, fontSize: 11, margin: 0 }}
                icon={<SendOutlined />}
              >
                {channelCount} 渠道
              </Tag>
            )}
          </Space>

          {/* System prompt preview */}
          <Typography.Paragraph
            type="secondary"
            ellipsis={{ rows: 2 }}
            style={{ marginBottom: 12, fontSize: 12, minHeight: 36, whiteSpace: 'pre-wrap' }}
          >
            {agent.system_prompt || '暂无系统提示词'}
          </Typography.Paragraph>

          {/* Actions */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 2,
              borderTop: `1px solid ${token.colorBorderSecondary}`,
              paddingTop: 8,
              marginTop: 4,
            }}
          >
            <Tooltip title="编辑">
              <Button
                type="text"
                size="small"
                icon={<EditOutlined />}
                onClick={(e) => {
                  e.stopPropagation();
                  openEdit(agent);
                }}
              />
            </Tooltip>
            <Tooltip title="版本历史">
              <Button
                type="text"
                size="small"
                icon={<HistoryOutlined />}
                onClick={(e) => {
                  e.stopPropagation();
                  openVersions(agent);
                }}
              />
            </Tooltip>
            {!agent.is_builtin && (
              <>
                <Tooltip title={agent.is_active ? '停用' : '启用'}>
                  <Button
                    type="text"
                    size="small"
                    icon={agent.is_active ? <StopOutlined /> : <CheckCircleOutlined />}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleToggle(agent);
                    }}
                  />
                </Tooltip>
                <Popconfirm
                  title="确定删除？"
                  onConfirm={(e) => {
                    e?.stopPropagation();
                    handleDelete(agent.id);
                  }}
                  onCancel={(e) => e?.stopPropagation()}
                >
                  <Button
                    type="text"
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={(e) => e.stopPropagation()}
                  />
                </Popconfirm>
              </>
            )}
          </div>
        </Card>
      </Col>
    );
  };

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
        }}
      >
        <Typography.Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          AI 中心
        </Typography.Title>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        type="card"
        items={[
          {
            key: 'agents',
            label: '智能体管理',
            children: (
              <>
                {/* Stats bar */}
                {!loading && renderStatsBar()}

                {/* Toolbar */}
                <Card
                  size="small"
                  style={{
                    marginBottom: 16,
                    borderRadius: 12,
                    background: token.colorBgElevated ?? token.colorBgContainer,
                  }}
                >
                  <Row gutter={[12, 8]} align="middle">
                    <Col xs={24} sm={5} md={3}>
                      <Select
                        value={filterType}
                        onChange={setFilterType}
                        style={{ width: '100%' }}
                        options={[
                          { value: 'all', label: '全部类型' },
                          { value: 'main', label: '主智能体' },
                          { value: 'sub', label: '子智能体' },
                        ]}
                      />
                    </Col>
                    <Col xs={24} sm={6} md={4}>
                      <Input
                        placeholder="搜索名称..."
                        value={filterName}
                        onChange={(e) => setFilterName(e.target.value)}
                        prefix={<SearchOutlined style={{ color: token.colorTextTertiary }} />}
                        allowClear
                      />
                    </Col>
                    <Col xs={24} sm={4} md={3}>
                      <Select
                        value={filterStatus}
                        onChange={setFilterStatus}
                        style={{ width: '100%' }}
                        options={[
                          { value: 'all', label: '全部状态' },
                          { value: 'active', label: '启用' },
                          { value: 'inactive', label: '停用' },
                        ]}
                      />
                    </Col>
                    <Col flex="auto" style={{ textAlign: 'right' }}>
                      <Space>
                        <Segmented
                          size="small"
                          value={viewMode}
                          onChange={(v) => setViewMode(v as 'card' | 'table')}
                          options={[
                            { value: 'card', icon: <AppstoreOutlined /> },
                            { value: 'table', icon: <UnorderedListOutlined /> },
                          ]}
                        />
                        <Button icon={<ReloadOutlined />} onClick={handleReload}>
                          重新加载
                        </Button>
                        <Button icon={<ThunderboltOutlined />} onClick={handleSeed}>
                          初始化种子
                        </Button>
                        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                          创建智能体
                        </Button>
                      </Space>
                    </Col>
                  </Row>
                </Card>

                {/* Batch action bar */}
                {selectedCount > 0 && (
                  <Card
                    size="small"
                    style={{
                      marginBottom: 16,
                      borderRadius: 12,
                      borderColor: token.colorPrimary,
                      background: token.colorPrimaryBg || token.colorBgElevated,
                    }}
                  >
                    <Space>
                      <Typography.Text strong style={{ color: token.colorPrimary }}>
                        已选择 {selectedCount} 个智能体
                      </Typography.Text>
                      <Button
                        size="small"
                        icon={<CheckCircleOutlined />}
                        onClick={() => handleBatchToggle(true)}
                      >
                        批量启用
                      </Button>
                      <Button
                        size="small"
                        icon={<StopOutlined />}
                        onClick={() => handleBatchToggle(false)}
                      >
                        批量停用
                      </Button>
                      <Button size="small" icon={<ClearOutlined />} onClick={clearSelection}>
                        取消选择
                      </Button>
                    </Space>
                  </Card>
                )}

                {loading ? (
                  <div style={{ textAlign: 'center', padding: 80 }}>
                    <Spin size="large" />
                  </div>
                ) : agents.length === 0 ? (
                  <Card style={{ borderRadius: 12, textAlign: 'center', padding: 60 }}>
                    <Empty description="暂无智能体">
                      <Space>
                        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                          创建智能体
                        </Button>
                        <Button icon={<ThunderboltOutlined />} onClick={handleSeed}>
                          初始化种子
                        </Button>
                      </Space>
                    </Empty>
                  </Card>
                ) : viewMode === 'table' ? (
                  <>
                    <div
                      style={{
                        marginBottom: 12,
                        paddingLeft: 4,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 16,
                      }}
                    >
                      <Checkbox
                        checked={selectedCount === agents.length && agents.length > 0}
                        indeterminate={selectedCount > 0 && selectedCount < agents.length}
                        onChange={toggleSelectAll}
                      >
                        全选
                      </Checkbox>
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        共 {agents.length} 个智能体
                      </Typography.Text>
                    </div>
                    <Table<AgentData>
                      rowKey="id"
                      dataSource={agents}
                      columns={tableColumns}
                      size="middle"
                      rowSelection={{
                        selectedRowKeys: [...selectedIds],
                        onChange: (keys) => setSelectedIds(new Set(keys as string[])),
                      }}
                      onRow={(record) => ({
                        style: { cursor: 'pointer' },
                        onClick: () => toggleSelect(record.id),
                      })}
                      pagination={false}
                      style={{ borderRadius: 12, overflow: 'hidden' }}
                    />
                  </>
                ) : (
                  <>
                    <div style={{ marginBottom: 12, paddingLeft: 4 }}>
                      <Checkbox
                        checked={selectedCount === agents.length && agents.length > 0}
                        indeterminate={selectedCount > 0 && selectedCount < agents.length}
                        onChange={toggleSelectAll}
                      >
                        全选
                      </Checkbox>
                    </div>
                    <Row gutter={[16, 16]}>{agents.map(renderAgentCard)}</Row>
                  </>
                )}

                <AgentEditorDrawer
                  agent={editAgent}
                  open={editorOpen}
                  onClose={() => {
                    setEditorOpen(false);
                    setEditAgent(null);
                  }}
                  onSaved={() => {
                    setEditorOpen(false);
                    setEditAgent(null);
                    fetch();
                  }}
                />

                <Drawer
                  title={versionAgent ? `版本历史 — ${versionAgent.name}` : '版本历史'}
                  open={versionOpen}
                  onClose={() => {
                    setVersionOpen(false);
                    setVersionAgent(null);
                    setVersions([]);
                  }}
                  width={480}
                  destroyOnHidden
                >
                  {versionsLoading ? (
                    <div style={{ textAlign: 'center', padding: 40 }}>
                      <Spin />
                    </div>
                  ) : versions.length === 0 ? (
                    <Empty description="暂无版本记录" />
                  ) : (
                    <div>
                      {versions.map((v, i) => {
                        const isCurrent = i === 0;
                        return (
                          <div
                            key={v.id}
                            style={{
                              position: 'relative',
                              paddingLeft: 24,
                              paddingBottom: i < versions.length - 1 ? 20 : 0,
                              borderLeft:
                                i < versions.length - 1
                                  ? `2px solid ${token.colorBorderSecondary}`
                                  : '2px solid transparent',
                            }}
                          >
                            <div
                              style={{
                                position: 'absolute',
                                left: -5,
                                top: 2,
                                width: 10,
                                height: 10,
                                borderRadius: '50%',
                                background: isCurrent
                                  ? token.colorPrimary
                                  : token.colorBorderSecondary,
                              }}
                            />
                            <div
                              style={{
                                padding: 12,
                                borderRadius: 10,
                                background: isCurrent
                                  ? token.colorPrimaryBg || `${token.colorPrimary}10`
                                  : (token.colorBgElevated ?? token.colorBgContainer),
                                border: isCurrent
                                  ? `1px solid ${token.colorPrimaryBorder || token.colorPrimary}30`
                                  : `1px solid ${token.colorBorderSecondary}`,
                              }}
                            >
                              <div
                                style={{
                                  display: 'flex',
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                  marginBottom: 4,
                                }}
                              >
                                <Space>
                                  <Typography.Text strong style={{ fontSize: 13 }}>
                                    {v.name}
                                  </Typography.Text>
                                  {isCurrent && (
                                    <Tag color="blue" style={{ borderRadius: 4, fontSize: 10 }}>
                                      当前
                                    </Tag>
                                  )}
                                </Space>
                                {!isCurrent && (
                                  <Popconfirm
                                    title="确定回滚到此版本？"
                                    onConfirm={() => handleRollback(v.id)}
                                    okText="回滚"
                                    cancelText="取消"
                                  >
                                    <Button
                                      size="small"
                                      type="link"
                                      danger
                                      style={{ fontSize: 12 }}
                                    >
                                      回滚
                                    </Button>
                                  </Popconfirm>
                                )}
                              </div>
                              {v.system_prompt && (
                                <Typography.Paragraph
                                  type="secondary"
                                  ellipsis={{ rows: 3 }}
                                  style={{ fontSize: 12, marginBottom: 4 }}
                                >
                                  {v.system_prompt}
                                </Typography.Paragraph>
                              )}
                              <Space wrap size={[4, 4]} style={{ marginBottom: 4 }}>
                                <Tag color="blue" style={{ borderRadius: 4, fontSize: 10 }}>
                                  {v.model_name}
                                </Tag>
                                {v.agent_type && (
                                  <Tag style={{ borderRadius: 4, fontSize: 10 }}>
                                    {v.agent_type}
                                  </Tag>
                                )}
                              </Space>
                              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                                {new Date(v.created_at).toLocaleString()}
                              </Typography.Text>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </Drawer>
              </>
            ),
          },
          {
            key: 'profiles',
            label: '客户端 Agent',
            children: <AgentProfileList />,
          },
        ]}
      />
    </div>
  );
}
