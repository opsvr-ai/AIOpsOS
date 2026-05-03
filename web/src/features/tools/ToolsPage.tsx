import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
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
  Skeleton,
  theme,
  Row,
  Col,
  Checkbox,
  Tooltip,
  Badge,
  Alert,
  Drawer,
  Tabs,
  Segmented,
  Table,
  Switch,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
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
  CloseCircleOutlined,
  AppstoreOutlined,
  UnorderedListOutlined,
  SafetyCertificateOutlined,
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
  is_builtin: boolean;
  is_consistent: boolean | null;
  is_valid: boolean | null;
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
  db_version: string | null;
  fs_description: string | null;
  fs_version: string | null;
  fs_category: string | null;
  source_path: string | null;
  source_label: string | null;
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

interface MCPServer {
  id: string;
  name: string;
  transport: string;
  command: string | null;
  args: string[];
  url: string | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
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
  builtin: { color: '#F59E0B', icon: <ThunderboltOutlined />, label: '内置' },
  skill: { color: '#3B82F6', icon: <ToolOutlined />, label: 'Skill' },
  plugin: { color: '#EC4899', icon: <RobotOutlined />, label: 'Plugin' },
  mcp: { color: '#8B5CF6', icon: <ApiOutlined />, label: 'MCP' },
  api: { color: '#22C55E', icon: <CodeOutlined />, label: 'API' },
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function nameToGradient(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  const h = Math.abs(hash) % 360;
  return `linear-gradient(135deg, hsl(${h}, 60%, 50%), hsl(${(h + 35) % 360}, 60%, 40%))`;
}

// ── Component ──────────────────────────────────────────────────────────────

export default function ToolsPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();

  // Data
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(24);

  // Filters
  const [filterType, setFilterType] = useState<string>('all');
  const [filterName, setFilterName] = useState('');
  const [filterDesc, setFilterDesc] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [debouncedName, setDebouncedName] = useState('');
  const [debouncedDesc, setDebouncedDesc] = useState('');

  // View mode
  const [viewMode, setViewMode] = useState<'card' | 'table'>('card');

  // Debounce search inputs (300ms)
  const nameTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const descTimerRef = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => {
    nameTimerRef.current = setTimeout(() => setDebouncedName(filterName.trim()), 300);
    return () => clearTimeout(nameTimerRef.current);
  }, [filterName]);
  useEffect(() => {
    descTimerRef.current = setTimeout(() => setDebouncedDesc(filterDesc.trim()), 300);
    return () => clearTimeout(descTimerRef.current);
  }, [filterDesc]);

  // Selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Preview drawer
  const [previewTool, setPreviewTool] = useState<ToolItem | null>(null);

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

  // MCP server state
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [mcpServersLoading, setMcpServersLoading] = useState(false);
  const [mcpServerModalOpen, setMcpServerModalOpen] = useState(false);
  const [editingMcpServer, setEditingMcpServer] = useState<MCPServer | null>(null);
  const [mcpServerForm] = Form.useForm();

  // Health filter for invalid skills
  const [filterHealth, setFilterHealth] = useState<string>('all');

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
      const params: Record<string, string | number> = {};
      if (filterType !== 'all') params.type = filterType;
      if (debouncedName) params.name = debouncedName;
      if (debouncedDesc) params.description = debouncedDesc;
      if (filterCategory !== 'all') params.category = filterCategory;
      if (filterStatus !== 'all') params.status = filterStatus;
      if (filterHealth === 'invalid') params.health = 'invalid';
      params.page = page;
      params.page_size = pageSize;
      const res = await api.get('/tools', { params });
      setTools(res.data?.items ?? []);
      setTotal(res.data?.total ?? 0);
    } catch {
      msg.error('加载失败');
    } finally {
      setLoading(false);
    }
  }, [
    msg,
    filterType,
    debouncedName,
    debouncedDesc,
    filterCategory,
    filterStatus,
    filterHealth,
    page,
    pageSize,
  ]);

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
  }, [fetch, fetchCategories]);

  // ── Stats ──────────────────────────────────────────────────────────────

  const stats = useMemo(() => {
    const active = tools.filter((t) => t.is_active).length;
    return {
      total: tools.length,
      active,
      inactive: tools.length - active,
      builtin: tools.filter((t) => t.is_builtin).length,
    };
  }, [tools]);

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
      const res = await api.post('/tools/batch-status', {
        tool_ids: [...selectedIds],
        is_active: active,
      });
      const skipped = res.data?.skipped as string[] | undefined;
      if (skipped?.length) {
        msg.warning(
          `已${active ? '启用' : '停用'} ${res.data?.count ?? 0} 个，跳过 ${skipped.length} 个无效 skill: ${skipped.join(', ')}`,
        );
      } else {
        msg.success(`已${active ? '启用' : '停用'} ${selectedIds.size} 个工具`);
      }
      clearSelection();
      fetch();
    } catch {
      msg.error('批量操作失败');
    }
  };

  const handleBatchDelete = async () => {
    try {
      await api.post('/tools/batch-delete', {
        tool_ids: [...selectedIds],
      });
      msg.success(`已删除 ${selectedIds.size} 个工具`);
      clearSelection();
      fetch();
    } catch {
      msg.error('批量删除失败');
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

  // ── MCP Server handlers ──────────────────────────────────────────

  const fetchMcpServers = useCallback(async () => {
    setMcpServersLoading(true);
    try {
      const res = await api.get('/tools/mcp-servers');
      setMcpServers(res.data ?? []);
    } catch {
      /* ignore */
    } finally {
      setMcpServersLoading(false);
    }
  }, []);

  useEffect(() => {
    if (filterType === 'mcp') fetchMcpServers();
  }, [filterType, fetchMcpServers]);

  const handleMcpServerCreate = () => {
    setEditingMcpServer(null);
    mcpServerForm.resetFields();
    mcpServerForm.setFieldsValue({ transport: 'stdio', is_active: true });
    setMcpServerModalOpen(true);
  };

  const handleMcpServerEdit = (s: MCPServer) => {
    setEditingMcpServer(s);
    mcpServerForm.setFieldsValue({
      name: s.name,
      transport: s.transport,
      command: s.command,
      args: s.args?.join(' ') ?? '',
      url: s.url,
      is_active: s.is_active,
    });
    setMcpServerModalOpen(true);
  };

  const handleMcpServerDelete = async (id: string) => {
    try {
      await api.delete(`/tools/mcp-servers/${id}`);
      msg.success('已删除');
      fetchMcpServers();
    } catch {
      msg.error('删除失败');
    }
  };

  const handleMcpServerSubmit = async (values: Record<string, unknown>) => {
    const payload = {
      ...values,
      args:
        typeof values.args === 'string'
          ? (values.args as string).split(/\s+/).filter(Boolean)
          : (values.args ?? []),
    };
    try {
      if (editingMcpServer) {
        await api.put(`/tools/mcp-servers/${editingMcpServer.id}`, payload);
        msg.success('更新成功');
      } else {
        await api.post('/tools/mcp-servers', payload);
        msg.success('创建成功');
      }
      setMcpServerModalOpen(false);
      mcpServerForm.resetFields();
      fetchMcpServers();
    } catch {
      msg.error(editingMcpServer ? '更新失败' : '创建失败');
    }
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

  // ── Table columns ──────────────────────────────────────────────────────

  const tableColumns: ColumnsType<ToolItem> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: ToolItem) => (
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
              fontSize: 14,
              flexShrink: 0,
            }}
          >
            {typeIcon(record.type)}
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
        <Tag color={typeColor(t)} style={{ borderRadius: 4, fontSize: 11 }}>
          {typeLabel(t)}
        </Tag>
      ),
    },
    {
      title: '分类',
      dataIndex: 'category',
      key: 'category',
      width: 100,
      render: (c: string | null) =>
        c ? (
          <Tag color="geekblue" style={{ borderRadius: 4, fontSize: 11 }}>
            {c}
          </Tag>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            -
          </Typography.Text>
        ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (d: string | null) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {d || '暂无描述'}
        </Typography.Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (active: boolean) => (
        <Tag color={active ? 'green' : 'default'} style={{ borderRadius: 4, fontSize: 11 }}>
          {active ? '启用' : '停用'}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_: unknown, r: ToolItem) => (
        <Space size={2}>
          {r.type === 'skill' && (
            <Tooltip title="高级编辑">
              <Button
                type="text"
                size="small"
                style={{ color: '#3B82F6' }}
                icon={<EditOutlined />}
                onClick={() => {
                  setSkillEditorTool(r);
                  setSkillEditorOpen(true);
                }}
              />
            </Tooltip>
          )}
          {r.type === 'skill' && (
            <Tooltip title="版本历史">
              <Button
                type="text"
                size="small"
                icon={<HistoryOutlined />}
                onClick={() => openVersions(r)}
              />
            </Tooltip>
          )}
          {!r.is_builtin && (
            <Tooltip title="编辑">
              <Button
                type="text"
                size="small"
                icon={<EditOutlined />}
                onClick={() => openEdit(r)}
              />
            </Tooltip>
          )}
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

  // ── Stats bar ──────────────────────────────────────────────────────────

  const renderStatsBar = () => (
    <Row gutter={[12, 12]} style={{ marginBottom: 20 }}>
      {(
        [
          { label: '工具总数', value: stats.total, color: '#0f172a' },
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

  // ── Render tool card ───────────────────────────────────────────────────

  const renderToolCard = (tool: ToolItem) => {
    const isSelected = selectedIds.has(tool.id);
    const cfg = tool.config || {};
    const version = cfg.version as string | undefined;
    const skillPrompt = cfg.skill_prompt as string | undefined;
    const isInconsistent =
      tool.type === 'skill' && consistencyMap.has(tool.id) && consistencyMap.get(tool.id) === false;
    const isInvalid = tool.type === 'skill' && tool.is_valid === false;

    return (
      <Col xs={24} sm={12} lg={8} xl={6} key={tool.id}>
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
            cursor: 'default',
          }}
          onClick={(e) => {
            const target = e.target as HTMLElement;
            if (target.closest('button') || target.closest('.ant-tag')) return;
            setPreviewTool(tool);
          }}
        >
          {/* Header with icon */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <Checkbox
              checked={isSelected}
              onChange={() => toggleSelect(tool.id)}
              onClick={(e) => e.stopPropagation()}
            />
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: nameToGradient(tool.name),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#fff',
                fontSize: 16,
                flexShrink: 0,
              }}
            >
              {typeIcon(tool.type)}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span
                  style={{
                    color: tool.is_active ? token.colorSuccess : token.colorTextTertiary,
                    fontSize: 8,
                    flexShrink: 0,
                  }}
                >
                  ●
                </span>
                <Typography.Text
                  strong
                  ellipsis={{ tooltip: tool.name }}
                  style={{ fontSize: 14, flex: 1 }}
                >
                  {tool.name}
                </Typography.Text>
              </div>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {typeLabel(tool.type)}
                {version ? ` · v${version}` : ''}
              </Typography.Text>
            </div>
          </div>

          {/* Tags */}
          <Space wrap size={[4, 4]} style={{ marginBottom: 10 }}>
            {tool.is_builtin && (
              <Tag
                icon={<SafetyCertificateOutlined />}
                color="gold"
                style={{ borderRadius: 6, fontSize: 11, margin: 0 }}
              >
                内置
              </Tag>
            )}
            <Tag color={typeColor(tool.type)} style={{ borderRadius: 6, fontSize: 11, margin: 0 }}>
              {typeLabel(tool.type)}
            </Tag>
            {tool.category && (
              <Tag
                color="geekblue"
                style={{ borderRadius: 6, fontSize: 11, margin: 0 }}
                onClick={(e) => {
                  e.stopPropagation();
                  setFilterCategory(tool.category!);
                }}
              >
                {tool.category}
              </Tag>
            )}
            {isInvalid && (
              <Tooltip title="Skill 目录或 SKILL.md 文件缺失，无法启用">
                <Tag
                  color="error"
                  style={{ borderRadius: 6, fontSize: 11, margin: 0, cursor: 'help' }}
                  icon={<CloseCircleOutlined />}
                >
                  无效
                </Tag>
              </Tooltip>
            )}
            {isInconsistent && (
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
                  style={{ borderRadius: 6, fontSize: 11, margin: 0, cursor: 'pointer' }}
                  icon={<ExclamationCircleOutlined />}
                  onClick={(e) => e.stopPropagation()}
                >
                  不一致
                </Tag>
              </Popover>
            )}
            {(cfg.source_label as string) && (
              <Tag
                color={(cfg.source_label as string) === 'standard' ? 'purple' : 'cyan'}
                style={{ borderRadius: 6, fontSize: 11, margin: 0 }}
              >
                {(cfg.source_label as string) === 'standard' ? '标准' : '扩展'}
              </Tag>
            )}
            {skillPrompt && (
              <Tag
                color="purple"
                style={{ borderRadius: 6, fontSize: 11, margin: 0 }}
                icon={<RobotOutlined />}
              >
                含提示词
              </Tag>
            )}
            {(cfg.license as string) && (
              <Tag style={{ borderRadius: 6, fontSize: 11, margin: 0 }}>
                {cfg.license as string}
              </Tag>
            )}
            {Array.isArray(cfg.allowed_tools) && (cfg.allowed_tools as string[]).length > 0 && (
              <Tag style={{ borderRadius: 6, fontSize: 11, margin: 0 }}>
                {(cfg.allowed_tools as string[]).length} tools
              </Tag>
            )}
          </Space>

          {/* Description */}
          <Typography.Paragraph
            type="secondary"
            ellipsis={{ rows: 2 }}
            style={{ marginBottom: 12, fontSize: 12, minHeight: 36 }}
          >
            {tool.description || '暂无描述'}
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
            {tool.type === 'skill' && (
              <Tooltip title="高级编辑 (文件管理)">
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
            )}
            {tool.type === 'skill' && (
              <Tooltip title="版本历史">
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
            )}
            {!tool.is_builtin && (
              <Tooltip title="编辑">
                <Button
                  type="text"
                  size="small"
                  icon={<EditOutlined />}
                  onClick={(e) => {
                    e.stopPropagation();
                    openEdit(tool);
                  }}
                />
              </Tooltip>
            )}
            {!tool.is_builtin && (
              <>
                <Tooltip title={tool.is_active ? '停用' : '启用'}>
                  <Button
                    type="text"
                    size="small"
                    icon={tool.is_active ? <StopOutlined /> : <CheckCircleOutlined />}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleToggle(tool);
                    }}
                  />
                </Tooltip>
                <Popconfirm
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
                </Popconfirm>
              </>
            )}
          </div>
        </Card>
      </Col>
    );
  };

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
          工具市场
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
          <Col xs={24} sm={6} md={4}>
            <Select
              value={filterType}
              onChange={(v) => {
                setFilterType(v);
                setPage(1);
              }}
              style={{ width: '100%' }}
              options={[
                { value: 'all', label: '全部类型' },
                { value: 'builtin', label: '内置工具' },
                { value: 'skill', label: 'Skill' },
                { value: 'plugin', label: 'Plugin' },
                { value: 'mcp', label: 'MCP' },
                { value: 'api', label: 'API' },
              ]}
            />
          </Col>
          <Col xs={24} sm={6} md={4}>
            <Input
              placeholder="搜索名称..."
              value={filterName}
              onChange={(e) => {
                setFilterName(e.target.value);
                setPage(1);
              }}
              prefix={<SearchOutlined style={{ color: token.colorTextTertiary }} />}
              allowClear
            />
          </Col>
          <Col xs={24} sm={6} md={4}>
            <Input
              placeholder="搜索描述..."
              value={filterDesc}
              onChange={(e) => {
                setFilterDesc(e.target.value);
                setPage(1);
              }}
              allowClear
            />
          </Col>
          <Col xs={24} sm={6} md={3}>
            <Select
              value={filterCategory}
              onChange={(v) => {
                setFilterCategory(v);
                setPage(1);
              }}
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
              value={filterHealth === 'invalid' ? 'invalid' : filterStatus}
              onChange={(v) => {
                if (v === 'invalid') {
                  setFilterHealth('invalid');
                  setFilterStatus('all');
                } else {
                  setFilterHealth('all');
                  setFilterStatus(v);
                }
                setPage(1);
              }}
              style={{ width: '100%' }}
              options={[
                { value: 'all', label: '全部状态' },
                { value: 'active', label: '启用' },
                { value: 'inactive', label: '停用' },
                { value: 'invalid', label: '无效 Skill' },
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
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                共 {total} 个
              </Typography.Text>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* Type tabs */}
      <Tabs
        activeKey={filterType}
        onChange={(v) => {
          setFilterType(v);
          setPage(1);
        }}
        style={{ marginBottom: 8 }}
        items={[
          { key: 'all', label: '全部' },
          { key: 'builtin', label: '内置工具' },
          { key: 'skill', label: '技能市场' },
          { key: 'plugin', label: '插件市场' },
          { key: 'mcp', label: 'MCP 市场' },
        ]}
      />

      {/* MCP Server management */}
      {filterType === 'mcp' && (
        <Card
          size="small"
          title={
            <Space>
              <ApiOutlined />
              <span>MCP 服务器</span>
              {mcpServers.length > 0 && <Tag style={{ marginLeft: 4 }}>{mcpServers.length}</Tag>}
            </Space>
          }
          extra={
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={handleMcpServerCreate}
            >
              添加服务器
            </Button>
          }
          style={{ borderRadius: 12, marginBottom: 16 }}
        >
          {mcpServersLoading ? (
            <Skeleton active title={false} paragraph={{ rows: 2 }} />
          ) : mcpServers.length === 0 ? (
            <Empty
              description="暂无 MCP 服务器"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              style={{ margin: '12px 0' }}
            />
          ) : (
            mcpServers.map((s) => (
              <div
                key={s.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '8px 12px',
                  borderRadius: 8,
                  background: token.colorFillQuaternary,
                  marginBottom: 6,
                }}
              >
                <Space size={8}>
                  <Badge status={s.is_active ? 'success' : 'default'} />
                  <Typography.Text strong style={{ fontSize: 13 }}>
                    {s.name}
                  </Typography.Text>
                  <Tag
                    color={s.transport === 'stdio' ? 'blue' : 'green'}
                    style={{ borderRadius: 4, fontSize: 10 }}
                  >
                    {s.transport}
                  </Tag>
                  {s.url && (
                    <Typography.Text type="secondary" style={{ fontSize: 11 }} ellipsis>
                      {s.url}
                    </Typography.Text>
                  )}
                </Space>
                <Space size={4}>
                  <Button
                    type="text"
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => handleMcpServerEdit(s)}
                  />
                  <Popconfirm
                    title="确定删除此 MCP 服务器？"
                    onConfirm={() => handleMcpServerDelete(s.id)}
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                  >
                    <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              </div>
            ))
          )}
        </Card>
      )}

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
            <Popconfirm
              title={`确定删除选中的 ${selectedCount} 个工具？`}
              onConfirm={handleBatchDelete}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button size="small" danger icon={<DeleteOutlined />}>
                批量删除
              </Button>
            </Popconfirm>
            <Button size="small" icon={<ClearOutlined />} onClick={clearSelection}>
              取消选择
            </Button>
          </Space>
        </Card>
      )}

      {/* Content */}
      {loading ? (
        <Row gutter={[16, 16]}>
          {Array.from({ length: 8 }).map((_, i) => (
            <Col xs={24} sm={12} lg={8} xl={6} key={i}>
              <Card size="small" style={{ borderRadius: 12 }}>
                <Skeleton active title paragraph={{ rows: 3 }} />
              </Card>
            </Col>
          ))}
        </Row>
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
              checked={selectedCount === filteredTools.length && filteredTools.length > 0}
              indeterminate={selectedCount > 0 && selectedCount < filteredTools.length}
              onChange={toggleSelectAll}
            >
              全选
            </Checkbox>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              共 {total} 个工具，当前显示 {filteredTools.length} 个
            </Typography.Text>
          </div>
          <Table<ToolItem>
            rowKey="id"
            dataSource={filteredTools}
            columns={tableColumns}
            size="middle"
            rowSelection={{
              selectedRowKeys: [...selectedIds],
              onChange: (keys) => setSelectedIds(new Set(keys as string[])),
            }}
            onRow={(record) => ({
              style: { cursor: 'pointer' },
              onClick: () => {
                setPreviewTool(record);
              },
            })}
            pagination={false}
            style={{ borderRadius: 12, overflow: 'hidden' }}
          />
        </>
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

          <Row gutter={[16, 16]}>{filteredTools.map(renderToolCard)}</Row>

          {/* Pagination */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginTop: 24,
              padding: '0 4px',
            }}
          >
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              共 {total} 个工具，第 {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, total)}{' '}
              个
            </Typography.Text>
            {total > pageSize && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Select
                  value={pageSize}
                  onChange={(v) => {
                    setPageSize(v);
                    setPage(1);
                  }}
                  size="small"
                  style={{ width: 90 }}
                  options={[
                    { value: 12, label: '12 / 页' },
                    { value: 24, label: '24 / 页' },
                    { value: 48, label: '48 / 页' },
                    { value: 96, label: '96 / 页' },
                  ]}
                />
                <Button
                  size="small"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  上一页
                </Button>
                <Typography.Text style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                  {page} / {Math.ceil(total / pageSize)}
                </Typography.Text>
                <Button
                  size="small"
                  disabled={page >= Math.ceil(total / pageSize)}
                  onClick={() => setPage((p) => p + 1)}
                >
                  下一页
                </Button>
              </div>
            )}
          </div>
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
                      {item.source_label && (
                        <Tag
                          color={item.source_label === 'standard' ? 'purple' : 'cyan'}
                          style={{ borderRadius: 4, fontSize: 10, marginLeft: 8 }}
                        >
                          {item.source_label === 'standard' ? '标准' : '扩展'}
                        </Tag>
                      )}
                      {item.fs_version && (
                        <Tag color="blue" style={{ borderRadius: 4, fontSize: 10, marginLeft: 4 }}>
                          v{item.fs_version}
                        </Tag>
                      )}
                      {item.category && (
                        <Tag
                          color="geekblue"
                          style={{ borderRadius: 4, fontSize: 10, marginLeft: 4 }}
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
                        {item.db_version && (
                          <Tag color="blue" style={{ borderRadius: 4, fontSize: 10 }}>
                            v{item.db_version}
                          </Tag>
                        )}
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
                      <Space size={4}>
                        <Typography.Text strong style={{ fontSize: 13 }}>
                          {item.name}
                        </Typography.Text>
                        {item.source_label && (
                          <Tag
                            color={item.source_label === 'standard' ? 'purple' : 'cyan'}
                            style={{ borderRadius: 4, fontSize: 10 }}
                          >
                            {item.source_label === 'standard' ? '标准' : '扩展'}
                          </Tag>
                        )}
                        <Tag color="orange" style={{ borderRadius: 4, fontSize: 10 }}>
                          v{item.db_version || '?'} → v{item.fs_version || '?'}
                        </Tag>
                      </Space>
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
                    { value: 'plugin', label: 'Plugin' },
                    { value: 'mcp', label: 'MCP' },
                    { value: 'api', label: 'API' },
                    { value: 'builtin', label: '内置工具' },
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

      {/* Skill Preview Drawer */}
      <Drawer
        title={null}
        open={!!previewTool}
        onClose={() => setPreviewTool(null)}
        width={520}
        styles={{ body: { padding: 0 } }}
        extra={
          previewTool && (
            <Space>
              {!previewTool.is_builtin && (
                <>
                  <Button
                    icon={<EditOutlined />}
                    onClick={() => {
                      setPreviewTool(null);
                      openEdit(previewTool);
                    }}
                  >
                    编辑
                  </Button>
                  <Button
                    icon={previewTool.is_active ? <StopOutlined /> : <CheckCircleOutlined />}
                    onClick={() => {
                      handleToggle(previewTool);
                      setPreviewTool(null);
                    }}
                  >
                    {previewTool.is_active ? '停用' : '启用'}
                  </Button>
                </>
              )}
              <Button icon={<CloseCircleOutlined />} onClick={() => setPreviewTool(null)} />
            </Space>
          )
        }
      >
        {previewTool && (
          <div style={{ padding: '20px 24px' }}>
            {/* Header */}
            <div style={{ marginBottom: 24 }}>
              <Space align="center" style={{ marginBottom: 8 }}>
                <div
                  style={{
                    width: 32,
                    height: 32,
                    borderRadius: 8,
                    background: nameToGradient(previewTool.name),
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#fff',
                    fontSize: 16,
                  }}
                >
                  {typeIcon(previewTool.type)}
                </div>
                <Typography.Title level={5} style={{ margin: 0 }}>
                  {previewTool.name}
                </Typography.Title>
              </Space>
              <Space size={4} style={{ marginBottom: 12 }}>
                {previewTool.is_builtin && (
                  <Tag
                    icon={<SafetyCertificateOutlined />}
                    color="gold"
                    style={{ borderRadius: 4, fontSize: 11 }}
                  >
                    内置
                  </Tag>
                )}
                <Tag color={typeColor(previewTool.type)} style={{ borderRadius: 4, fontSize: 11 }}>
                  {typeLabel(previewTool.type)}
                </Tag>
                <Tag
                  color={previewTool.is_active ? 'green' : 'default'}
                  style={{ borderRadius: 4, fontSize: 11 }}
                >
                  {previewTool.is_active ? '启用' : '停用'}
                </Tag>
                {previewTool.category && (
                  <Tag color="geekblue" style={{ borderRadius: 4, fontSize: 11 }}>
                    {previewTool.category}
                  </Tag>
                )}
                {(previewTool.config?.version as string) && (
                  <Tag color="blue" style={{ borderRadius: 4, fontSize: 11 }}>
                    v{previewTool.config.version as string}
                  </Tag>
                )}
                {(previewTool.config?.source_label as string) && (
                  <Tag
                    color={
                      (previewTool.config.source_label as string) === 'standard' ? 'purple' : 'cyan'
                    }
                    style={{ borderRadius: 4, fontSize: 11 }}
                  >
                    {(previewTool.config.source_label as string) === 'standard' ? '标准' : '扩展'}
                  </Tag>
                )}
              </Space>
            </div>

            {/* Description */}
            <div style={{ marginBottom: 20 }}>
              <Typography.Text
                type="secondary"
                style={{ fontSize: 12, marginBottom: 4, display: 'block' }}
              >
                描述
              </Typography.Text>
              <Typography.Paragraph style={{ fontSize: 14, margin: 0 }}>
                {previewTool.description || '暂无描述'}
              </Typography.Paragraph>
            </div>

            {/* SKILL.md content */}
            {previewTool.type === 'skill' && (previewTool.config?.skill_prompt as string) && (
              <div style={{ marginBottom: 20 }}>
                <Typography.Text
                  type="secondary"
                  style={{ fontSize: 12, marginBottom: 8, display: 'block' }}
                >
                  SKILL.md 内容
                </Typography.Text>
                <div
                  style={{
                    background: token.colorFillQuaternary,
                    borderRadius: 8,
                    padding: 12,
                    maxHeight: 300,
                    overflow: 'auto',
                  }}
                  data-color-mode="light"
                >
                  <MDEditor.Markdown source={previewTool.config.skill_prompt as string} />
                </div>
              </div>
            )}

            {/* Metadata */}
            {previewTool.type === 'skill' && (
              <div style={{ marginBottom: 20 }}>
                <Typography.Text
                  type="secondary"
                  style={{ fontSize: 12, marginBottom: 8, display: 'block' }}
                >
                  元数据
                </Typography.Text>
                <div
                  style={{
                    background: token.colorFillQuaternary,
                    borderRadius: 8,
                    padding: 12,
                    fontSize: 12,
                    fontFamily: 'monospace',
                    maxHeight: 200,
                    overflow: 'auto',
                    whiteSpace: 'pre-wrap',
                  }}
                >
                  {JSON.stringify(
                    {
                      version: previewTool.config?.version,
                      license: previewTool.config?.license,
                      metadata: previewTool.config?.metadata,
                      allowed_tools: previewTool.config?.allowed_tools,
                    },
                    null,
                    2,
                  )}
                </div>
              </div>
            )}

            {/* Info */}
            <div>
              <Typography.Text
                type="secondary"
                style={{ fontSize: 12, marginBottom: 8, display: 'block' }}
              >
                信息
              </Typography.Text>
              <Space direction="vertical" size={4}>
                <Typography.Text style={{ fontSize: 13 }}>ID: {previewTool.id}</Typography.Text>
                {previewTool.source_path && (
                  <Typography.Text style={{ fontSize: 13 }} type="secondary">
                    路径: {previewTool.source_path}
                  </Typography.Text>
                )}
                <Typography.Text style={{ fontSize: 13 }} type="secondary">
                  创建: {previewTool.created_at}
                </Typography.Text>
                <Typography.Text style={{ fontSize: 13 }} type="secondary">
                  更新: {previewTool.updated_at}
                </Typography.Text>
              </Space>
            </div>
          </div>
        )}
      </Drawer>

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

      {/* MCP Server create/edit modal */}
      <Modal
        title={editingMcpServer ? '编辑 MCP 服务器' : '添加 MCP 服务器'}
        open={mcpServerModalOpen}
        onCancel={() => {
          setMcpServerModalOpen(false);
          mcpServerForm.resetFields();
        }}
        onOk={() => mcpServerForm.submit()}
        okText={editingMcpServer ? '保存' : '添加'}
        cancelText="取消"
        width={520}
        destroyOnHidden
      >
        <Form form={mcpServerForm} layout="vertical" onFinish={handleMcpServerSubmit}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如 my-mcp-server" />
          </Form.Item>

          <Form.Item name="transport" label="传输协议">
            <Select
              options={[
                { value: 'stdio', label: 'stdio (命令行)' },
                { value: 'url', label: 'URL (远程)' },
              ]}
            />
          </Form.Item>

          <Form.Item name="command" label="命令">
            <Input placeholder="如 npx 或 uvx" />
          </Form.Item>

          <Form.Item
            name="args"
            label="参数"
            extra="空格分隔，如: -y @modelcontextprotocol/server-filesystem /tmp"
          >
            <Input placeholder="参数..." />
          </Form.Item>

          <Form.Item name="url" label="URL">
            <Input placeholder="如 http://localhost:3000/mcp" />
          </Form.Item>

          <Form.Item name="is_active" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
