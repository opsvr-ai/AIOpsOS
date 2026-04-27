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
  Tag,
  Popconfirm,
  Popover,
  App,
  Empty,
  Spin,
  theme,
  Row,
  Col,
  Checkbox,
  Tooltip,
  Badge,
  Alert,
  Drawer,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  ToolOutlined,
  ReloadOutlined,
  SearchOutlined,
  UploadOutlined,
  ApiOutlined,
  CodeOutlined,
  RobotOutlined,
  InboxOutlined,
  EditOutlined,
  StopOutlined,
  CheckCircleOutlined,
  ThunderboltOutlined,
  ClearOutlined,
  ExclamationCircleOutlined,
  HistoryOutlined,
  SyncOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
} from '@ant-design/icons';
import { Upload } from 'antd';
import type { UploadFile } from 'antd';
import MDEditor from '@uiw/react-md-editor';
import api from '@/services/api';
import SkillEditorDrawer from './SkillEditorDrawer';

// ── Types ──────────────────────────────────────────────────────────────────

interface ToolItem {
  id: string;
  name: string;
  type: string;
  description: string | null;
  category: string | null;
  source_path: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  is_approved: boolean;
  is_consistent: boolean | null;
  created_at: string;
  updated_at: string;
}

interface SyncDiffItem {
  name: string;
  category: string | null;
  type: string;
  status: string;
  db_id: string | null;
  db_description: string | null;
  fs_description: string | null;
  fs_category: string | null;
  source_path: string | null;
  is_active: boolean;
}

interface SyncScanOut {
  total_fs: number;
  total_db: number;
  only_in_db: SyncDiffItem[];
  only_in_fs: SyncDiffItem[];
  modified: SyncDiffItem[];
  consistent: number;
}

interface CategoryCount {
  category: string;
  count: number;
}

interface SkillUploadResult {
  filename: string;
  name: string | null;
  status: string;
  message: string;
}

interface ConsistencyInfo {
  tool_id: string;
  tool_name: string;
  is_consistent: boolean | null;
  db_hash: string | null;
  fs_hash: string | null;
}

interface SkillVersion {
  id: string;
  tool_id: string;
  name: string;
  description: string | null;
  config: Record<string, unknown>;
  created_at: string;
}

const SKILL_NAME_RE = /^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/;

function validateSkillName(name: string): string | null {
  if (!name || name.length > 64) return '名称需要 1-64 个字符';
  if (name.length > 2 && !SKILL_NAME_RE.test(name)) {
    return '仅限小写字母、数字和单连字符，不能以连字符开头或结尾';
  }
  if (name.includes('--')) return '不能包含连续连字符';
  return null;
}

const TYPE_CONFIG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  skill: { color: '#3B82F6', icon: <ToolOutlined />, label: 'Skill' },
  mcp: { color: '#8B5CF6', icon: <ApiOutlined />, label: 'MCP' },
  api: { color: '#22C55E', icon: <CodeOutlined />, label: 'API' },
};

// ── Component ──────────────────────────────────────────────────────────────

export default function ToolsPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();

  // Data
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters
  const [filterType, setFilterType] = useState<string>('all');
  const [filterName, setFilterName] = useState('');
  const [filterDesc, setFilterDesc] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');

  // Selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Modals
  const [createOpen, setCreateOpen] = useState(false);
  const [editTool, setEditTool] = useState<ToolItem | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);

  // Form
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  // Upload state
  const [uploadFileList, setUploadFileList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadResults, setUploadResults] = useState<SkillUploadResult[] | null>(null);

  // AI generate state
  const [aiName, setAiName] = useState('');
  const [aiDesc, setAiDesc] = useState('');
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiContent, setAiContent] = useState('');
  const [aiSaving, setAiSaving] = useState(false);

  // Consistency & Version
  const [consistencyMap, setConsistencyMap] = useState<Map<string, boolean | null>>(new Map());
  const [checkingConsistency, setCheckingConsistency] = useState(false);
  const [versionOpen, setVersionOpen] = useState(false);
  const [versionTool, setVersionTool] = useState<ToolItem | null>(null);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  // Auto-detect consistency
  const [inconsistentCount, setInconsistentCount] = useState(0);

  // Skill Editor Drawer
  const [skillEditorOpen, setSkillEditorOpen] = useState(false);
  const [skillEditorTool, setSkillEditorTool] = useState<ToolItem | null>(null);

  // Category filter
  const [filterCategory, setFilterCategory] = useState<string>('all');
  const [categories, setCategories] = useState<CategoryCount[]>([]);

  // Sync
  const [syncModalOpen, setSyncModalOpen] = useState(false);
  const [syncScanResult, setSyncScanResult] = useState<SyncScanOut | null>(null);
  const [syncScanning, setSyncScanning] = useState(false);
  const [syncExecuting, setSyncExecuting] = useState(false);
  const [selectedSyncNames, setSelectedSyncNames] = useState<Set<string>>(new Set());

  // ── Fetch ──────────────────────────────────────────────────────────────

  const fetch = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (filterType !== 'all') params.type = filterType;
      if (filterName.trim()) params.name = filterName.trim();
      if (filterDesc.trim()) params.description = filterDesc.trim();
      if (filterCategory !== 'all') params.category = filterCategory;
      if (filterStatus !== 'all') params.status = filterStatus;
      const res = await api.get('/tools', { params });
      setTools(res.data ?? []);
    } catch {
      msg.error('加载失败');
    } finally {
      setLoading(false);
    }
  }, [msg, filterType, filterName, filterDesc, filterCategory, filterStatus]);

  const fetchCategories = useCallback(async () => {
    try {
      const res = await api.get('/tools/categories');
      setCategories(res.data ?? []);
    } catch {
      // silent
    }
  }, []);

  const checkConsistency = useCallback(async () => {
    setCheckingConsistency(true);
    try {
      const res = await api.get('/tools/check-consistency');
      const data = res.data as { tools: ConsistencyInfo[]; inconsistent_count: number };
      const map = new Map<string, boolean | null>();
      for (const t of data.tools) {
        map.set(t.tool_id, t.is_consistent);
      }
      setConsistencyMap(map);
      setInconsistentCount(data.inconsistent_count ?? 0);
    } catch {
      // silent
    } finally {
      setCheckingConsistency(false);
    }
  }, []);

  useEffect(() => {
    fetch();
    fetchCategories();
    checkConsistency();
  }, [fetch, fetchCategories, checkConsistency]);

  // Auto-poll sync status every 30s
  useEffect(() => {
    const poll = async () => {
      try {
        const res = await api.post('/tools/sync/scan');
        const data = res.data as SyncScanOut;
        setInconsistentCount(
          data.only_in_db.length + data.only_in_fs.length + data.modified.length,
        );
      } catch {
        // silent
      }
    };
    poll();
    const timer = setInterval(poll, 30000);
    return () => clearInterval(timer);
  }, []);

  // ── Filtered data ──────────────────────────────────────────────────────

  const filteredTools = tools;

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
    if (selectedIds.size === filteredTools.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredTools.map((t) => t.id)));
    }
  };

  const clearSelection = () => setSelectedIds(new Set());

  // ── CRUD ───────────────────────────────────────────────────────────────

  const handleCreate = async (values: Record<string, unknown>) => {
    const isSkill = values.type === 'skill';
    if (isSkill) {
      const nameErr = validateSkillName(values.name as string);
      if (nameErr) {
        msg.error(nameErr);
        return;
      }
    }
    setSaving(true);
    try {
      const body: Record<string, unknown> = { ...values };
      if (typeof body.config === 'string' && body.config) {
        try {
          body.config = JSON.parse(body.config as string);
        } catch {
          /* keep string */
        }
      }
      if (editTool) {
        await api.patch(`/tools/${editTool.id}`, body);
        msg.success('更新成功');
      } else {
        await api.post('/tools', body);
        msg.success('创建成功');
      }
      setCreateOpen(false);
      setEditTool(null);
      form.resetFields();
      fetch();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '操作失败');
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (tool: ToolItem) => {
    // Validate before enabling a skill
    if (tool.type === 'skill' && !tool.is_active) {
      try {
        const res = await api.post(`/tools/${tool.id}/validate`);
        if (!res.data.valid) {
          Modal.error({
            title: '无法启用',
            content: (
              <ul style={{ paddingLeft: 20, margin: 0 }}>
                {res.data.errors.map((e: string, i: number) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            ),
          });
          return;
        }
      } catch {
        msg.error('校验失败');
        return;
      }
    }
    try {
      await api.patch(`/tools/${tool.id}`, { is_active: !tool.is_active });
      fetch();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '操作失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/tools/${id}`);
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
    try {
      await api.post('/tools/batch-status', {
        tool_ids: [...selectedIds],
        is_active: active,
      });
      msg.success(`已${active ? '启用' : '停用'} ${selectedIds.size} 个工具`);
      clearSelection();
      fetch();
    } catch {
      msg.error('批量操作失败');
    }
  };

  // ── Upload ─────────────────────────────────────────────────────────────

  const handleUpload = async () => {
    if (uploadFileList.length === 0) {
      msg.warning('请选择文件');
      return;
    }
    setUploading(true);
    setUploadResults(null);
    try {
      const formData = new FormData();
      uploadFileList.forEach((f) => {
        if (f.originFileObj) formData.append('files', f.originFileObj);
      });
      const res = await api.post('/tools/upload', formData);
      const data = res.data;
      setUploadResults(data.results ?? []);
      const ok = (data.created || 0) + (data.updated || 0);
      const err = data.errors || 0;
      if (ok > 0 && err === 0) msg.success(`成功处理 ${ok} 个 Skill`);
      else if (ok > 0) msg.warning(`成功 ${ok} 个，失败 ${err} 个`);
      else msg.error(`全部失败: ${err} 个`);
      if (ok > 0) fetch();
    } catch {
      msg.error('上传失败');
    } finally {
      setUploading(false);
    }
  };

  // ── AI Generate ────────────────────────────────────────────────────────

  const handleAiGenerate = async () => {
    if (!aiName.trim() || !aiDesc.trim()) {
      msg.warning('请输入名称和描述');
      return;
    }
    const nameErr = validateSkillName(aiName.trim());
    if (nameErr) {
      msg.error(nameErr);
      return;
    }
    setAiGenerating(true);
    setAiContent('');
    try {
      const res = await api.post('/tools/ai-generate', {
        name: aiName.trim(),
        description: aiDesc.trim(),
        language: 'zh',
      });
      setAiContent(res.data.content);
    } catch {
      msg.error('AI 生成失败');
    } finally {
      setAiGenerating(false);
    }
  };

  const handleAiSave = async () => {
    if (!aiContent) return;
    setAiSaving(true);
    try {
      const frontmatterMatch = aiContent.match(/^---\s*\n(.*?)\n---/s);
      let description = aiDesc.trim();
      const config: Record<string, unknown> = {};
      if (frontmatterMatch) {
        const fm = frontmatterMatch[1];
        const getFm = (key: string) => {
          const m = fm.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'));
          return m ? m[1].trim() : null;
        };
        description = getFm('description') || description;
        const v = getFm('version');
        if (v) config.version = v;
        const l = getFm('license');
        if (l) config.license = l;
        const c = getFm('compatibility');
        if (c) config.compatibility = c;
        const body = aiContent.split('---', 2)[1]?.replace(/^---/, '').trim() || '';
        if (body) config.skill_prompt = body;
      }
      await api.post('/tools', {
        name: aiName.trim(),
        type: 'skill',
        description,
        config,
        is_approved: true,
        is_active: true,
      });
      msg.success('Skill 创建成功');
      setAiOpen(false);
      setAiName('');
      setAiDesc('');
      setAiContent('');
      fetch();
    } catch {
      msg.error('保存失败');
    } finally {
      setAiSaving(false);
    }
  };

  // ── Open edit modal ────────────────────────────────────────────────────

  const openEdit = (tool: ToolItem) => {
    setEditTool(tool);
    const cfg = tool.config || {};
    form.setFieldsValue({
      name: tool.name,
      type: tool.type,
      description: tool.description || '',
      is_active: tool.is_active,
      version: cfg.version || '',
      license: cfg.license || '',
      compatibility: cfg.compatibility || '',
      metadata: cfg.metadata ? JSON.stringify(cfg.metadata, null, 2) : '',
      allowed_tools: Array.isArray(cfg.allowed_tools)
        ? (cfg.allowed_tools as string[]).join(', ')
        : '',
      skill_prompt: cfg.skill_prompt || '',
    });
    setCreateOpen(true);
  };

  const openCreate = () => {
    setEditTool(null);
    form.resetFields();
    form.setFieldsValue({ type: 'skill', is_active: true });
    setCreateOpen(true);
  };

  const handleSyncFromFs = async (toolId: string) => {
    try {
      await api.post(`/tools/${toolId}/sync-from-filesystem`);
      msg.success('已从文件系统同步');
      fetch();
      checkConsistency();
    } catch {
      msg.error('同步失败');
    }
  };

  const handleSyncToFs = async (toolId: string) => {
    try {
      await api.post(`/tools/${toolId}/sync-to-filesystem`);
      msg.success('已同步到文件系统');
      checkConsistency();
    } catch {
      msg.error('同步失败');
    }
  };

  // ── Full Sync ──────────────────────────────────────────────────────────

  const handleSyncScan = async () => {
    setSyncScanning(true);
    setSyncScanResult(null);
    setSelectedSyncNames(new Set());
    try {
      const res = await api.post('/tools/sync/scan');
      setSyncScanResult(res.data);
      setSyncModalOpen(true);
    } catch {
      msg.error('扫描失败');
    } finally {
      setSyncScanning(false);
    }
  };

  const handleSyncExecute = async () => {
    if (!syncScanResult) return;
    setSyncExecuting(true);
    try {
      const actions: { action: string; name: string }[] = [];
      const allItems = [
        ...syncScanResult.only_in_fs,
        ...syncScanResult.only_in_db,
        ...syncScanResult.modified,
      ];
      const selected = allItems.filter((item) => selectedSyncNames.has(item.name));

      if (selected.length === 0) {
        msg.warning('请选择要同步的项');
        return;
      }

      for (const item of selected) {
        if (item.status === 'only_in_fs') {
          actions.push({ action: 'register', name: item.name });
        } else if (item.status === 'only_in_db') {
          actions.push({ action: 'delete', name: item.name });
        } else if (item.status === 'modified') {
          actions.push({ action: 'update', name: item.name });
        }
      }

      const res = await api.post('/tools/sync/execute', { actions });
      const out = res.data;
      if (out.errors?.length) {
        msg.warning(
          `同步完成: 注册 ${out.registered}, 更新 ${out.updated}, 删除 ${out.deleted}, 错误 ${out.errors.length}`,
        );
      } else {
        msg.success(`同步完成: 注册 ${out.registered}, 更新 ${out.updated}, 删除 ${out.deleted}`);
      }
      setSyncModalOpen(false);
      fetch();
      checkConsistency();
    } catch {
      msg.error('同步执行失败');
    } finally {
      setSyncExecuting(false);
    }
  };

  const toggleSyncItem = (name: string) => {
    setSelectedSyncNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const toggleAllSync = (section: SyncDiffItem[]) => {
    setSelectedSyncNames((prev) => {
      const next = new Set(prev);
      const allSelected = section.every((i) => next.has(i.name));
      for (const i of section) {
        if (allSelected) next.delete(i.name);
        else next.add(i.name);
      }
      return next;
    });
  };

  // ── Versions ────────────────────────────────────────────────────────────

  const openVersions = async (tool: ToolItem) => {
    setVersionTool(tool);
    setVersionOpen(true);
    setVersionsLoading(true);
    try {
      const res = await api.get(`/tools/${tool.id}/versions`);
      setVersions(res.data ?? []);
    } catch {
      msg.error('加载版本历史失败');
    } finally {
      setVersionsLoading(false);
    }
  };

  const handleRollback = async (versionId: string) => {
    if (!versionTool) return;
    try {
      await api.post(`/tools/${versionTool.id}/rollback`, { version_id: versionId });
      msg.success('已回滚');
      setVersionOpen(false);
      fetch();
      checkConsistency();
    } catch {
      msg.error('回滚失败');
    }
  };

  // ── Helpers ────────────────────────────────────────────────────────────

  const typeIcon = (type: string) => TYPE_CONFIG[type]?.icon ?? <ToolOutlined />;
  const typeColor = (type: string) => TYPE_CONFIG[type]?.color ?? token.colorPrimary;
  const typeLabel = (type: string) => TYPE_CONFIG[type]?.label ?? type;

  const selectedCount = selectedIds.size;

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
        }}
      >
        <Typography.Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          工具注册
        </Typography.Title>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            onClick={async () => {
              try {
                await api.post('/tools/reload');
                msg.success('已重新加载');
                fetch();
              } catch {
                msg.error('重新加载失败');
              }
            }}
          >
            重新加载
          </Button>
          <Tooltip title="一键同步 — 扫描文件系统与数据库差异">
            <Badge count={inconsistentCount} overflowCount={99} size="small" offset={[-4, 0]}>
              <Button
                icon={<SyncOutlined spin={syncScanning} />}
                onClick={handleSyncScan}
                type={inconsistentCount > 0 ? 'default' : 'default'}
                style={
                  inconsistentCount > 0
                    ? { borderColor: token.colorWarning, color: token.colorWarning }
                    : undefined
                }
              >
                一键同步
              </Button>
            </Badge>
          </Tooltip>
          <Tooltip title="检查 Skill 文件一致性">
            <Button icon={<SyncOutlined spin={checkingConsistency} />} onClick={checkConsistency}>
              一致性检查
            </Button>
          </Tooltip>
          <Button
            icon={<UploadOutlined />}
            onClick={() => {
              setUploadOpen(true);
              setUploadFileList([]);
              setUploadResults(null);
            }}
          >
            上传 Skill
          </Button>
          <Button
            icon={<ThunderboltOutlined />}
            onClick={() => {
              setAiOpen(true);
              setAiName('');
              setAiDesc('');
              setAiContent('');
            }}
          >
            AI 创建
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            注册工具
          </Button>
        </Space>
      </div>

      {/* Search / Filter Bar */}
      <Card
        size="small"
        style={{
          marginBottom: 16,
          borderRadius: 12,
          background: token.colorBgElevated ?? token.colorBgContainer,
        }}
      >
        <Row gutter={[12, 8]} align="middle">
          <Col xs={24} sm={6} md={4}>
            <Select
              value={filterType}
              onChange={(v) => setFilterType(v)}
              style={{ width: '100%' }}
              options={[
                { value: 'all', label: '全部类型' },
                { value: 'skill', label: 'Skill' },
                { value: 'mcp', label: 'MCP' },
                { value: 'api', label: 'API' },
              ]}
            />
          </Col>
          <Col xs={24} sm={6} md={5}>
            <Input
              placeholder="搜索名称..."
              value={filterName}
              onChange={(e) => setFilterName(e.target.value)}
              prefix={<SearchOutlined style={{ color: token.colorTextTertiary }} />}
              allowClear
            />
          </Col>
          <Col xs={24} sm={6} md={5}>
            <Input
              placeholder="搜索描述..."
              value={filterDesc}
              onChange={(e) => setFilterDesc(e.target.value)}
              allowClear
            />
          </Col>
          <Col xs={24} sm={6} md={3}>
            <Select
              value={filterCategory}
              onChange={(v) => setFilterCategory(v)}
              style={{ width: '100%' }}
              placeholder="分类"
              options={[
                {
                  value: 'all',
                  label: `全部分类 (${categories.reduce((s, c) => s + c.count, 0)})`,
                },
                ...categories.map((c) => ({
                  value: c.category,
                  label: `${c.category} (${c.count})`,
                })),
              ]}
            />
          </Col>
          <Col xs={24} sm={6} md={3}>
            <Select
              value={filterStatus}
              onChange={(v) => setFilterStatus(v)}
              style={{ width: '100%' }}
              options={[
                { value: 'all', label: '全部状态' },
                { value: 'active', label: '启用' },
                { value: 'inactive', label: '停用' },
              ]}
            />
          </Col>
          <Col flex="auto" style={{ textAlign: 'right' }}>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              共 {filteredTools.length} 个工具
            </Typography.Text>
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
              已选择 {selectedCount} 个工具
            </Typography.Text>
            <Button
              size="small"
              icon={<CheckCircleOutlined />}
              onClick={() => handleBatchToggle(true)}
            >
              批量启用
            </Button>
            <Button size="small" icon={<StopOutlined />} onClick={() => handleBatchToggle(false)}>
              批量停用
            </Button>
            <Button size="small" icon={<ClearOutlined />} onClick={clearSelection}>
              取消选择
            </Button>
          </Space>
        </Card>
      )}

      {/* Card Grid */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      ) : filteredTools.length === 0 ? (
        <Card style={{ borderRadius: 12, textAlign: 'center', padding: 60 }}>
          <Empty description="暂无工具">
            <Space>
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                注册工具
              </Button>
              <Button icon={<UploadOutlined />} onClick={() => setUploadOpen(true)}>
                上传 Skill
              </Button>
            </Space>
          </Empty>
        </Card>
      ) : (
        <>
          <div style={{ marginBottom: 12, paddingLeft: 4 }}>
            <Checkbox
              checked={selectedCount === filteredTools.length && filteredTools.length > 0}
              indeterminate={selectedCount > 0 && selectedCount < filteredTools.length}
              onChange={toggleSelectAll}
            >
              全选
            </Checkbox>
          </div>

          <Row gutter={[16, 16]}>
            {filteredTools.map((tool) => {
              const isSelected = selectedIds.has(tool.id);
              const cfg = tool.config || {};
              const version = cfg.version as string | undefined;
              const skillPrompt = cfg.skill_prompt as string | undefined;

              return (
                <Col xs={24} sm={12} lg={8} xl={6} key={tool.id}>
                  <Badge.Ribbon
                    text={tool.is_active ? '启用' : '停用'}
                    color={tool.is_active ? token.colorSuccess : token.colorTextTertiary}
                  >
                    <Card
                      hoverable
                      size="small"
                      style={{
                        borderRadius: 12,
                        height: '100%',
                        borderColor: isSelected ? token.colorPrimary : undefined,
                        boxShadow: isSelected ? `0 0 0 2px ${token.colorPrimary}20` : undefined,
                      }}
                      onClick={(e) => {
                        const target = e.target as HTMLElement;
                        if (target.closest('button') || target.closest('.ant-tag')) return;
                        toggleSelect(tool.id);
                      }}
                      title={
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                          }}
                        >
                          <Space>
                            <Checkbox
                              checked={isSelected}
                              onChange={() => toggleSelect(tool.id)}
                              onClick={(e) => e.stopPropagation()}
                            />
                            <span style={{ color: typeColor(tool.type), fontSize: 16 }}>
                              {typeIcon(tool.type)}
                            </span>
                            <Typography.Text
                              strong
                              ellipsis={{ tooltip: tool.name }}
                              style={{ maxWidth: 100, fontSize: 14 }}
                            >
                              {tool.name}
                            </Typography.Text>
                          </Space>
                          <Space>
                            <Tag
                              color={typeColor(tool.type)}
                              style={{ borderRadius: 4, margin: 0, fontSize: 11 }}
                            >
                              {typeLabel(tool.type)}
                            </Tag>
                            {tool.type === 'skill' &&
                              consistencyMap.has(tool.id) &&
                              consistencyMap.get(tool.id) === false && (
                                <Popover
                                  title="文件不一致"
                                  content={
                                    <Space direction="vertical" size={4}>
                                      <Typography.Text style={{ fontSize: 12 }}>
                                        SKILL.md 文件与数据库不一致
                                      </Typography.Text>
                                      <Button
                                        size="small"
                                        type="link"
                                        icon={<ArrowDownOutlined />}
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          handleSyncFromFs(tool.id);
                                        }}
                                        style={{ padding: 0 }}
                                      >
                                        从文件同步到数据库
                                      </Button>
                                      <Button
                                        size="small"
                                        type="link"
                                        icon={<ArrowUpOutlined />}
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          handleSyncToFs(tool.id);
                                        }}
                                        style={{ padding: 0 }}
                                      >
                                        从数据库同步到文件
                                      </Button>
                                    </Space>
                                  }
                                  trigger="click"
                                >
                                  <Tag
                                    color="orange"
                                    style={{
                                      borderRadius: 4,
                                      margin: 0,
                                      fontSize: 11,
                                      cursor: 'pointer',
                                    }}
                                    icon={<ExclamationCircleOutlined />}
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    不一致
                                  </Tag>
                                </Popover>
                              )}
                          </Space>
                        </div>
                      }
                      actions={[
                        tool.type === 'skill' && (
                          <Tooltip title="高级编辑 (文件管理)" key="skillEditor">
                            <Button
                              type="text"
                              size="small"
                              style={{ color: '#3B82F6' }}
                              icon={<EditOutlined />}
                              onClick={(e) => {
                                e.stopPropagation();
                                setSkillEditorTool(tool);
                                setSkillEditorOpen(true);
                              }}
                            />
                          </Tooltip>
                        ),
                        tool.type === 'skill' && (
                          <Tooltip title="版本历史" key="versions">
                            <Button
                              type="text"
                              size="small"
                              icon={<HistoryOutlined />}
                              onClick={(e) => {
                                e.stopPropagation();
                                openVersions(tool);
                              }}
                            />
                          </Tooltip>
                        ),
                        <Tooltip title="编辑" key="edit">
                          <Button
                            type="text"
                            size="small"
                            icon={<EditOutlined />}
                            onClick={(e) => {
                              e.stopPropagation();
                              openEdit(tool);
                            }}
                          />
                        </Tooltip>,
                        <Tooltip title={tool.is_active ? '停用' : '启用'} key="toggle">
                          <Button
                            type="text"
                            size="small"
                            icon={tool.is_active ? <StopOutlined /> : <CheckCircleOutlined />}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleToggle(tool);
                            }}
                          />
                        </Tooltip>,
                        <Popconfirm
                          key="delete"
                          title="确定删除？"
                          onConfirm={(e) => {
                            e?.stopPropagation();
                            handleDelete(tool.id);
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
                        </Popconfirm>,
                      ]}
                    >
                      <div style={{ minHeight: 80 }}>
                        <Typography.Paragraph
                          type="secondary"
                          ellipsis={{ rows: 3 }}
                          style={{ marginBottom: 12, fontSize: 13, minHeight: 60 }}
                        >
                          {tool.description || '暂无描述'}
                        </Typography.Paragraph>

                        <Space wrap size={[4, 4]}>
                          {tool.category && (
                            <Tag
                              color="geekblue"
                              style={{ borderRadius: 4, fontSize: 11 }}
                              onClick={(e) => {
                                e.stopPropagation();
                                setFilterCategory(tool.category!);
                              }}
                            >
                              {tool.category}
                            </Tag>
                          )}
                          {version && (
                            <Tag color="blue" style={{ borderRadius: 4, fontSize: 11 }}>
                              v{version}
                            </Tag>
                          )}
                          {(cfg.license as string) && (
                            <Tag style={{ borderRadius: 4, fontSize: 11 }}>
                              {cfg.license as string}
                            </Tag>
                          )}
                          {(cfg.source_label as string) && (
                            <Tag
                              color={(cfg.source_label as string) === 'core' ? 'green' : 'orange'}
                              style={{ borderRadius: 4, fontSize: 11 }}
                            >
                              hermes/{cfg.source_label as string}
                            </Tag>
                          )}
                          {skillPrompt && (
                            <Tag
                              color="purple"
                              style={{ borderRadius: 4, fontSize: 11 }}
                              icon={<RobotOutlined />}
                            >
                              含提示词
                            </Tag>
                          )}
                          {Array.isArray(cfg.allowed_tools) &&
                            (cfg.allowed_tools as string[]).length > 0 && (
                              <Tag style={{ borderRadius: 4, fontSize: 11 }}>
                                {(cfg.allowed_tools as string[]).length} tools
                              </Tag>
                            )}
                        </Space>
                      </div>
                    </Card>
                  </Badge.Ribbon>
                </Col>
              );
            })}
          </Row>
        </>
      )}

      {/* Sync Modal */}
      <Modal
        title={
          <Space>
            <SyncOutlined />
            <span>一键同步</span>
            {syncScanResult && (
              <Tag style={{ borderRadius: 4, fontSize: 12 }}>
                FS {syncScanResult.total_fs} / DB {syncScanResult.total_db}
              </Tag>
            )}
          </Space>
        }
        open={syncModalOpen}
        onCancel={() => {
          setSyncModalOpen(false);
          setSyncScanResult(null);
        }}
        footer={[
          <Button
            key="cancel"
            onClick={() => {
              setSyncModalOpen(false);
              setSyncScanResult(null);
            }}
          >
            关闭
          </Button>,
          <Button
            key="selectAll"
            onClick={() => {
              if (!syncScanResult) return;
              const all = [
                ...syncScanResult.only_in_fs,
                ...syncScanResult.only_in_db,
                ...syncScanResult.modified,
              ];
              setSelectedSyncNames(new Set(all.map((i) => i.name)));
            }}
          >
            全选
          </Button>,
          <Button
            key="sync"
            type="primary"
            icon={<SyncOutlined />}
            onClick={handleSyncExecute}
            loading={syncExecuting}
            disabled={selectedSyncNames.size === 0}
          >
            同步选中 ({selectedSyncNames.size})
          </Button>,
        ]}
        width={720}
        destroyOnHidden
        styles={{ body: { maxHeight: '60vh', overflowY: 'auto', padding: '16px 24px' } }}
      >
        {!syncScanResult ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : syncScanResult.only_in_db.length === 0 &&
          syncScanResult.only_in_fs.length === 0 &&
          syncScanResult.modified.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <CheckCircleOutlined style={{ fontSize: 48, color: token.colorSuccess }} />
            <Typography.Title level={5} style={{ marginTop: 16 }}>
              全部一致
            </Typography.Title>
            <Typography.Text type="secondary">
              文件系统与数据库完全同步 ({syncScanResult.consistent} 个技能)
            </Typography.Text>
          </div>
        ) : (
          <div>
            {syncScanResult.only_in_fs.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    marginBottom: 8,
                  }}
                >
                  <Space>
                    <Badge
                      count={syncScanResult.only_in_fs.length}
                      size="small"
                      style={{ backgroundColor: token.colorSuccess }}
                    />
                    <Typography.Text strong style={{ fontSize: 14, color: token.colorSuccess }}>
                      仅在文件系统 (需注册)
                    </Typography.Text>
                  </Space>
                  <Button
                    size="small"
                    type="link"
                    onClick={() => toggleAllSync(syncScanResult.only_in_fs)}
                  >
                    全选
                  </Button>
                </div>
                {syncScanResult.only_in_fs.map((item) => (
                  <div
                    key={item.name}
                    style={{
                      padding: '8px 12px',
                      borderRadius: 8,
                      marginBottom: 4,
                      background: token.colorSuccessBg,
                      border: `1px solid ${token.colorSuccessBorder}`,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                    }}
                  >
                    <Checkbox
                      checked={selectedSyncNames.has(item.name)}
                      onChange={() => toggleSyncItem(item.name)}
                    />
                    <div style={{ flex: 1 }}>
                      <Typography.Text strong style={{ fontSize: 13 }}>
                        {item.name}
                      </Typography.Text>
                      {item.category && (
                        <Tag
                          color="geekblue"
                          style={{ borderRadius: 4, fontSize: 10, marginLeft: 8 }}
                        >
                          {item.category}
                        </Tag>
                      )}
                      <Typography.Paragraph
                        type="secondary"
                        style={{ fontSize: 11, margin: '2px 0 0' }}
                        ellipsis={{ rows: 1 }}
                      >
                        {item.fs_description}
                      </Typography.Paragraph>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {syncScanResult.only_in_db.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    marginBottom: 8,
                  }}
                >
                  <Space>
                    <Badge
                      count={syncScanResult.only_in_db.length}
                      size="small"
                      style={{ backgroundColor: token.colorError }}
                    />
                    <Typography.Text strong style={{ fontSize: 14, color: token.colorError }}>
                      仅在数据库 (可删除)
                    </Typography.Text>
                  </Space>
                  <Button
                    size="small"
                    type="link"
                    onClick={() => toggleAllSync(syncScanResult.only_in_db)}
                  >
                    全选
                  </Button>
                </div>
                {syncScanResult.only_in_db.map((item) => (
                  <div
                    key={item.name}
                    style={{
                      padding: '8px 12px',
                      borderRadius: 8,
                      marginBottom: 4,
                      background: token.colorErrorBg,
                      border: `1px solid ${token.colorErrorBorder}`,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                    }}
                  >
                    <Checkbox
                      checked={selectedSyncNames.has(item.name)}
                      onChange={() => toggleSyncItem(item.name)}
                    />
                    <div style={{ flex: 1 }}>
                      <Space size={4}>
                        <Typography.Text strong style={{ fontSize: 13 }}>
                          {item.name}
                        </Typography.Text>
                        {item.is_active && (
                          <Tag color="green" style={{ borderRadius: 4, fontSize: 10 }}>
                            已启用
                          </Tag>
                        )}
                      </Space>
                      <Typography.Paragraph
                        type="secondary"
                        style={{ fontSize: 11, margin: '2px 0 0' }}
                        ellipsis={{ rows: 1 }}
                      >
                        {item.db_description}
                      </Typography.Paragraph>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {syncScanResult.modified.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    marginBottom: 8,
                  }}
                >
                  <Space>
                    <Badge
                      count={syncScanResult.modified.length}
                      size="small"
                      style={{ backgroundColor: token.colorWarning }}
                    />
                    <Typography.Text strong style={{ fontSize: 14, color: token.colorWarning }}>
                      已修改 (需更新)
                    </Typography.Text>
                  </Space>
                  <Button
                    size="small"
                    type="link"
                    onClick={() => toggleAllSync(syncScanResult.modified)}
                  >
                    全选
                  </Button>
                </div>
                {syncScanResult.modified.map((item) => (
                  <div
                    key={item.name}
                    style={{
                      padding: '8px 12px',
                      borderRadius: 8,
                      marginBottom: 4,
                      background: token.colorWarningBg,
                      border: `1px solid ${token.colorWarningBorder}`,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                    }}
                  >
                    <Checkbox
                      checked={selectedSyncNames.has(item.name)}
                      onChange={() => toggleSyncItem(item.name)}
                    />
                    <div style={{ flex: 1 }}>
                      <Typography.Text strong style={{ fontSize: 13 }}>
                        {item.name}
                      </Typography.Text>
                      {item.fs_category && item.fs_category !== item.category && (
                        <Tag
                          color="orange"
                          style={{ borderRadius: 4, fontSize: 10, marginLeft: 8 }}
                        >
                          {item.category} → {item.fs_category}
                        </Tag>
                      )}
                      <Typography.Paragraph
                        type="secondary"
                        style={{ fontSize: 11, margin: '2px 0 0' }}
                        ellipsis={{ rows: 1 }}
                      >
                        {item.fs_description}
                      </Typography.Paragraph>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* Create / Edit Modal */}
      <Modal
        title={editTool ? '编辑工具' : '注册工具'}
        open={createOpen}
        onCancel={() => {
          setCreateOpen(false);
          setEditTool(null);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        confirmLoading={saving}
        okText={editTool ? '保存' : '注册'}
        cancelText="取消"
        width={800}
        destroyOnHidden
        styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreate}
          initialValues={{ type: 'skill', is_active: true }}
        >
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="name"
                label="名称"
                rules={[
                  { required: true, message: '请输入名称' },
                  {
                    validator: (_, v) => {
                      if (v && v.length > 64) return Promise.reject('名称不能超过 64 个字符');
                      return Promise.resolve();
                    },
                  },
                ]}
              >
                <Input placeholder="工具名称，如 my-skill" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="type" label="类型" rules={[{ required: true }]}>
                <Select
                  options={[
                    { value: 'skill', label: 'Skill' },
                    { value: 'mcp', label: 'MCP' },
                    { value: 'api', label: 'API' },
                  ]}
                />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            name="description"
            label="描述"
            rules={[
              {
                validator: (_, v) => {
                  if (v && v.length > 1024) return Promise.reject('描述不能超过 1024 个字符');
                  return Promise.resolve();
                },
              },
            ]}
          >
            <Input.TextArea rows={2} placeholder="工具描述" />
          </Form.Item>

          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.type !== cur.type}>
            {({ getFieldValue }) => {
              const isSkill = getFieldValue('type') === 'skill';
              if (!isSkill) {
                return (
                  <Form.Item name="config" label="配置 (JSON)">
                    <Input.TextArea rows={3} placeholder='{"params": {"key": "str"}}' />
                  </Form.Item>
                );
              }

              return (
                <>
                  <Typography.Text
                    strong
                    style={{ fontSize: 13, display: 'block', marginBottom: 8 }}
                  >
                    Skill 协议字段
                  </Typography.Text>
                  <Row gutter={16}>
                    <Col span={8}>
                      <Form.Item name="version" label="版本">
                        <Input placeholder="1.0.0" />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item name="license" label="许可证">
                        <Input placeholder="MIT" />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item
                        name="compatibility"
                        label="兼容性"
                        rules={[
                          {
                            validator: (_, v) => {
                              if (v && v.length > 500)
                                return Promise.reject('兼容性描述不能超过 500 个字符');
                              return Promise.resolve();
                            },
                          },
                        ]}
                      >
                        <Input placeholder="需 Claude Code 2.0+" />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Form.Item name="allowed_tools" label="允许的工具列表 (逗号分隔)">
                    <Input placeholder="bash, read, write" />
                  </Form.Item>
                  <Form.Item name="metadata" label="元数据 (JSON)">
                    <Input.TextArea
                      rows={2}
                      placeholder='{"author": "team-ops", "tags": "monitoring"}'
                    />
                  </Form.Item>
                  <Form.Item name="skill_prompt" label="Skill 提示词 (Markdown)">
                    <div data-color-mode="dark">
                      <MDEditor
                        value={form.getFieldValue('skill_prompt') || ''}
                        onChange={(v) => form.setFieldsValue({ skill_prompt: v ?? '' })}
                        height={250}
                        preview="edit"
                        visibleDragbar={false}
                      />
                    </div>
                  </Form.Item>
                </>
              );
            }}
          </Form.Item>

          <Form.Item name="is_active" label="状态">
            <Select
              options={[
                { value: true, label: '启用' },
                { value: false, label: '停用' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Upload Modal */}
      <Modal
        title="上传 Skill"
        open={uploadOpen}
        onCancel={() => {
          setUploadOpen(false);
          setUploadFileList([]);
          setUploadResults(null);
        }}
        onOk={handleUpload}
        confirmLoading={uploading}
        okText="上传"
        cancelText="取消"
        width={600}
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          <Upload.Dragger
            accept=".zip"
            multiple
            fileList={uploadFileList}
            beforeUpload={(file) => {
              if (!file.name.endsWith('.zip')) {
                msg.warning(`${file.name} 不是 zip 文件`);
                return Upload.LIST_IGNORE;
              }
              setUploadFileList((prev) => [...prev, file as UploadFile]);
              return false;
            }}
            onRemove={(file) => {
              setUploadFileList((prev) => prev.filter((f) => f.uid !== file.uid));
            }}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p style={{ fontWeight: 500 }}>点击或拖拽 Skill zip 包到此区域</p>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              每个 zip 必须包含以 skill 名称命名的目录，内含 SKILL.md 文件
            </Typography.Text>
          </Upload.Dragger>

          {uploadResults && (
            <div style={{ maxHeight: 200, overflowY: 'auto' }}>
              <Typography.Text strong style={{ fontSize: 13, marginBottom: 8, display: 'block' }}>
                上传结果：
              </Typography.Text>
              {uploadResults.map((r, i) => (
                <div
                  key={i}
                  style={{
                    padding: '6px 12px',
                    borderRadius: 6,
                    marginTop: 4,
                    background:
                      r.status === 'error'
                        ? token.colorErrorBg
                        : r.status === 'created'
                          ? token.colorSuccessBg
                          : token.colorWarningBg,
                    border: `1px solid ${
                      r.status === 'error'
                        ? token.colorErrorBorder
                        : r.status === 'created'
                          ? token.colorSuccessBorder
                          : token.colorWarningBorder
                    }`,
                  }}
                >
                  <Space>
                    <Tag
                      color={
                        r.status === 'created'
                          ? 'success'
                          : r.status === 'updated'
                            ? 'warning'
                            : 'error'
                      }
                      style={{ borderRadius: 4, fontSize: 11 }}
                    >
                      {r.status === 'created' ? '新建' : r.status === 'updated' ? '更新' : '失败'}
                    </Tag>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>{r.name || r.filename}</span>
                    {r.message && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        — {r.message}
                      </Typography.Text>
                    )}
                  </Space>
                </div>
              ))}
            </div>
          )}
        </Space>
      </Modal>

      {/* Skill Editor Drawer */}
      {skillEditorTool && (
        <SkillEditorDrawer
          tool={skillEditorTool}
          open={skillEditorOpen}
          onClose={() => {
            setSkillEditorOpen(false);
            setSkillEditorTool(null);
            fetch();
            checkConsistency();
          }}
          onSaved={() => {
            fetch();
            checkConsistency();
          }}
        />
      )}

      {/* Version History Drawer */}
      <Drawer
        title={versionTool ? `版本历史 — ${versionTool.name}` : '版本历史'}
        open={versionOpen}
        onClose={() => {
          setVersionOpen(false);
          setVersionTool(null);
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
                      background: isCurrent ? token.colorPrimary : token.colorBorderSecondary,
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
                          title="确定回滚到此版本？当前版本将被保存为历史记录"
                          onConfirm={() => handleRollback(v.id)}
                          okText="回滚"
                          cancelText="取消"
                        >
                          <Button size="small" type="link" danger style={{ fontSize: 12 }}>
                            回滚
                          </Button>
                        </Popconfirm>
                      )}
                    </div>
                    {v.description && (
                      <Typography.Paragraph
                        type="secondary"
                        ellipsis={{ rows: 2 }}
                        style={{ fontSize: 12, marginBottom: 4 }}
                      >
                        {v.description}
                      </Typography.Paragraph>
                    )}
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      {new Date(v.created_at).toLocaleString()}
                    </Typography.Text>
                    {v.config &&
                      Object.keys(v.config).length > 0 &&
                      (() => {
                        const cfg = v.config as Record<string, unknown>;
                        const ver = cfg.version as string | undefined;
                        const lic = cfg.license as string | undefined;
                        if (!ver && !lic) return null;
                        return (
                          <div style={{ marginTop: 6 }}>
                            <Space wrap size={[4, 4]}>
                              {ver && (
                                <Tag color="blue" style={{ borderRadius: 4, fontSize: 10 }}>
                                  v{ver}
                                </Tag>
                              )}
                              {lic && <Tag style={{ borderRadius: 4, fontSize: 10 }}>{lic}</Tag>}
                            </Space>
                          </div>
                        );
                      })()}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Drawer>

      {/* AI Generate Modal */}
      <Modal
        title="AI 创建 Skill"
        open={aiOpen}
        onCancel={() => {
          setAiOpen(false);
          setAiName('');
          setAiDesc('');
          setAiContent('');
        }}
        footer={
          aiContent
            ? [
                <Button
                  key="cancel"
                  onClick={() => {
                    setAiOpen(false);
                    setAiName('');
                    setAiDesc('');
                    setAiContent('');
                  }}
                >
                  关闭
                </Button>,
                <Button key="regenerate" onClick={handleAiGenerate} loading={aiGenerating}>
                  重新生成
                </Button>,
                <Button key="save" type="primary" onClick={handleAiSave} loading={aiSaving}>
                  保存 Skill
                </Button>,
              ]
            : [
                <Button
                  key="cancel"
                  onClick={() => {
                    setAiOpen(false);
                    setAiName('');
                    setAiDesc('');
                  }}
                >
                  取消
                </Button>,
                <Button
                  key="generate"
                  type="primary"
                  icon={<ThunderboltOutlined />}
                  onClick={handleAiGenerate}
                  loading={aiGenerating}
                >
                  生成 SKILL.md
                </Button>,
              ]
        }
        width={800}
        destroyOnHidden
      >
        {!aiContent ? (
          <Space direction="vertical" style={{ width: '100%' }} size={16}>
            <Alert
              message="AI 将根据你提供的名称和描述，自动生成符合 Skill 协议规范的 SKILL.md 文件"
              type="info"
              showIcon
              style={{ borderRadius: 8 }}
            />
            <div>
              <Typography.Text strong style={{ fontSize: 13, display: 'block', marginBottom: 4 }}>
                名称
              </Typography.Text>
              <Input
                placeholder="如 my-skill (小写字母、数字和连字符)"
                value={aiName}
                onChange={(e) => setAiName(e.target.value)}
              />
            </div>
            <div>
              <Typography.Text strong style={{ fontSize: 13, display: 'block', marginBottom: 4 }}>
                描述
              </Typography.Text>
              <Input.TextArea
                rows={4}
                placeholder="用自然语言描述这个 Skill 应该做什么，AI 会生成完整的 SKILL.md..."
                value={aiDesc}
                onChange={(e) => setAiDesc(e.target.value)}
              />
            </div>
          </Space>
        ) : (
          <div>
            <Alert
              message="以下是 AI 生成的 SKILL.md，你可以编辑后保存"
              type="success"
              showIcon
              style={{ borderRadius: 8, marginBottom: 16 }}
            />
            <div data-color-mode="dark">
              <MDEditor
                value={aiContent}
                onChange={(v) => setAiContent(v ?? '')}
                height={500}
                visibleDragbar={false}
              />
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
