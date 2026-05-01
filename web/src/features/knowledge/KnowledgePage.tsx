import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Button,
  Modal,
  Input,
  Space,
  Typography,
  Tag,
  Popconfirm,
  App,
  Spin,
  Empty,
  theme,
  Upload,
  Checkbox,
  Badge,
  Tooltip,
  Divider,
  Row,
  Col,
  Tabs,
  Progress,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  SearchOutlined,
  UploadOutlined,
  FileTextOutlined,
  BookOutlined,
  EditOutlined,
  EyeOutlined,
  PictureOutlined,
  ThunderboltOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  MinusCircleOutlined,
  ExclamationCircleOutlined,
  WarningOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import MDEditor from '@uiw/react-md-editor';
import type { ICommand } from '@uiw/react-md-editor';
import api from '@/services/api';
import WikiBrowser from './WikiBrowser';

interface Document {
  id: string;
  title: string;
  content: string;
  source: string | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

interface SearchResult {
  content: string;
  score: number;
  document_id: string;
  title: string;
  source: string | null;
}

interface MonitorState {
  enabled: boolean;
  running: boolean;
  watched_files: number;
  last_check: string | null;
  poll_interval_seconds: number;
}

interface WatchedFileItem {
  path: string;
  status: 'unchanged' | 'changed' | 'new' | 'deleted';
  last_modified: string | null;
  size: number;
}

interface ProcessResultItem {
  file: string;
  status: 'processed' | 'skipped' | 'error';
  wiki_pages_updated: string[];
  message: string;
}

interface ProcessAllResultData {
  total: number;
  processed: number;
  skipped: number;
  errors: number;
  results: ProcessResultItem[];
}

interface LintIssue {
  check_id: string;
  severity: 'error' | 'warning' | 'info';
  page: string;
  message: string;
  fix_action: string;
  fix_description: string;
}

interface LintReportData {
  health_score: number;
  total_issues: number;
  errors: number;
  warnings: number;
  info: number;
  issues: LintIssue[];
  checked_at: string;
}

export default function KnowledgePage() {
  const { token } = theme.useToken();
  const { message: msg } = App.useApp();

  // ── Tab state ─────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState('docs');
  const [wikiInitialPage, setWikiInitialPage] = useState<string | undefined>(undefined);

  // Check URL for wiki page deep-link
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const page = params.get('page');
    if (page) {
      setActiveTab('wiki');
      setWikiInitialPage(page);
    }
  }, []);

  // ── Document state ────────────────────────────────────────
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [editDoc, setEditDoc] = useState<Document | null>(null);
  const [viewDoc, setViewDoc] = useState<Document | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [mdValue, setMdValue] = useState('');
  const [mdTitle, setMdTitle] = useState('');
  const [mdSource, setMdSource] = useState('');
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [convertMd, setConvertMd] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  // ── Monitor state ─────────────────────────────────────────
  const [monitorStatus, setMonitorStatus] = useState<MonitorState | null>(null);
  const [monitorFiles, setMonitorFiles] = useState<WatchedFileItem[]>([]);
  const [processingAll, setProcessingAll] = useState(false);
  const [processingFilePath, setProcessingFilePath] = useState<string | null>(null);
  const [processResult, setProcessResult] = useState<ProcessAllResultData | null>(null);
  const [processResultOpen, setProcessResultOpen] = useState(false);
  const [watchedPanelOpen, setWatchedPanelOpen] = useState(false);

  // ── Lint state ────────────────────────────────────────────
  const [lintReport, setLintReport] = useState<LintReportData | null>(null);
  const [lintLoading, setLintLoading] = useState(false);
  const [fixingIssue, setFixingIssue] = useState<string | null>(null);
  const [fixingAll, setFixingAll] = useState(false);

  // ── Fetch documents ───────────────────────────────────────

  const fetchDocs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/knowledge/documents');
      setDocs(res.data ?? []);
    } catch {
      msg.error('加载知识库失败');
    } finally {
      setLoading(false);
    }
  }, [msg]);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  // ── Monitor data ───────────────────────────────────────────

  const formatTime = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString('zh-CN') : '尚未检查';

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
  };

  const fetchMonitorData = useCallback(async () => {
    try {
      const [statusRes, filesRes] = await Promise.all([
        api.get('/knowledge/monitor/status'),
        api.get('/knowledge/monitor/files'),
      ]);
      setMonitorStatus(statusRes.data);
      setMonitorFiles(filesRes.data ?? []);
    } catch {
      setMonitorStatus(null);
      setMonitorFiles([]);
    }
  }, []);

  useEffect(() => {
    fetchMonitorData();
    const timer = setInterval(fetchMonitorData, 30000);
    return () => clearInterval(timer);
  }, [fetchMonitorData]);

  // ── Lint data ──────────────────────────────────────────────

  const fetchLintReport = useCallback(async () => {
    setLintLoading(true);
    try {
      const res = await api.post('/knowledge/lint');
      setLintReport(res.data);
    } catch {
      msg.error('加载 Lint 报告失败');
    } finally {
      setLintLoading(false);
    }
  }, [msg]);

  useEffect(() => {
    if (activeTab === 'monitor') {
      fetchLintReport();
    }
  }, [activeTab, fetchLintReport]);

  const handleFixIssue = async (issueId: string, page: string) => {
    setFixingIssue(issueId);
    try {
      await api.post(`/knowledge/lint/fix/${encodeURIComponent(issueId)}`, null, {
        params: { page },
      });
      msg.success('修复成功');
      fetchLintReport();
    } catch {
      msg.error('修复失败');
    } finally {
      setFixingIssue(null);
    }
  };

  const handleFixAll = async () => {
    setFixingAll(true);
    try {
      const res = await api.post('/knowledge/lint/fix-all');
      msg.success(
        `修复完成: ${res.data.fixed} 项, 健康评分 ${res.data.health_before} → ${res.data.health_after}`,
      );
      fetchLintReport();
    } catch {
      msg.error('批量修复失败');
    } finally {
      setFixingAll(false);
    }
  };

  // ── Process triggers ──────────────────────────────────────

  const handleTriggerProcessAll = async () => {
    setProcessingAll(true);
    try {
      const res = await api.post('/knowledge/monitor/process-all', null, { timeout: 120000 });
      setProcessResult(res.data);
      setProcessResultOpen(true);
      msg.success(`知识更新完成: 处理 ${res.data.processed}, 跳过 ${res.data.skipped}`);
      fetchMonitorData();
    } catch {
      msg.error('知识更新失败');
    } finally {
      setProcessingAll(false);
    }
  };

  const handleTriggerProcessFile = async (filepath: string) => {
    setProcessingFilePath(filepath);
    try {
      const res = await api.post('/knowledge/monitor/process-document', null, {
        params: { filepath },
      });
      if (res.data.status === 'processed') {
        msg.success(`${res.data.file} 处理完成`);
      } else if (res.data.status === 'skipped') {
        msg.info(`${res.data.file}: ${res.data.message || '已跳过'}`);
      } else {
        msg.error(`${res.data.file}: ${res.data.message || '处理失败'}`);
      }
      fetchMonitorData();
    } catch {
      msg.error('处理失败');
    } finally {
      setProcessingFilePath(null);
    }
  };

  const triggerProcessBestEffort = async () => {
    try {
      await api.post('/knowledge/monitor/process-all', null, { timeout: 120000 });
      fetchMonitorData();
    } catch {
      // best-effort, silent
    }
  };

  const changedCount = monitorFiles.filter(
    (f) => f.status === 'changed' || f.status === 'new',
  ).length;

  // ── Create / Edit ────────────────────────────────────────

  const openCreate = () => {
    setEditDoc(null);
    setMdTitle('');
    setMdSource('');
    setMdValue('');
    setCreateOpen(true);
  };

  const openEdit = (doc: Document) => {
    setEditDoc(doc);
    setMdTitle(doc.title);
    setMdSource(doc.source ?? '');
    setMdValue(doc.content);
    setCreateOpen(true);
  };

  const handleSaveMarkdown = async () => {
    if (!mdTitle.trim()) {
      msg.warning('请输入标题');
      return;
    }
    if (!mdValue.trim()) {
      msg.warning('请输入内容');
      return;
    }
    setSaving(true);
    try {
      if (editDoc) {
        await api.patch(`/knowledge/documents/${editDoc.id}`, null, {
          params: { title: mdTitle, content: mdValue, source: mdSource || undefined },
        });
        msg.success('更新成功');
      } else {
        await api.post('/knowledge/documents', {
          title: mdTitle,
          content: mdValue,
          source: mdSource || undefined,
        });
        msg.success('添加成功');
      }
      setCreateOpen(false);
      fetchDocs();
      triggerProcessBestEffort();
    } catch {
      msg.error(editDoc ? '更新失败' : '添加失败');
    } finally {
      setSaving(false);
    }
  };

  // ── Image upload for markdown editor ─────────────────────

  const handleImageUpload = async (): Promise<string | null> => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,image/jpeg,image/gif,image/webp,image/svg+xml';
    return new Promise((resolve) => {
      input.onchange = async () => {
        const file = input.files?.[0];
        if (!file) {
          resolve(null);
          return;
        }
        const formData = new FormData();
        formData.append('file', file);
        try {
          const res = await api.post('/knowledge/images/upload', formData);
          resolve(res.data.url);
        } catch {
          msg.error('图片上传失败');
          resolve(null);
        }
      };
      input.click();
    });
  };

  const imageCommand: ICommand = {
    name: 'image-upload',
    keyCommand: 'image-upload',
    buttonProps: { 'aria-label': '上传图片' },
    icon: <PictureOutlined />,
    execute: async (_state, api) => {
      const url = await handleImageUpload();
      if (url) {
        api?.replaceSelection(`![image](${url})`);
      }
    },
  };

  // ── File upload ──────────────────────────────────────────

  const handleFileUpload = async () => {
    if (!uploadFile) {
      msg.warning('请选择文件');
      return;
    }
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', uploadFile);
      formData.append('convert_to_markdown', String(convertMd));
      await api.post('/knowledge/upload', formData);
      msg.success('上传成功');
      setUploadOpen(false);
      setUploadFile(null);
      setConvertMd(false);
      fetchDocs();
      triggerProcessBestEffort();
    } catch {
      msg.error('上传失败');
    } finally {
      setUploading(false);
    }
  };

  // ── Delete ───────────────────────────────────────────────

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/knowledge/documents/${id}`);
      msg.success('已删除');
      fetchDocs();
    } catch {
      msg.error('删除失败');
    }
  };

  // ── Search ───────────────────────────────────────────────

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const res = await api.post('/knowledge/search', null, {
        params: { query: searchQuery, top_k: 5 },
      });
      setSearchResults(res.data?.results ?? []);
    } catch {
      msg.error('搜索失败');
    } finally {
      setSearching(false);
    }
  };

  // ── Table columns ────────────────────────────────────────

  const columns = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      render: (v: string) => (
        <Space>
          <FileTextOutlined style={{ color: token.colorPrimary }} />
          <span style={{ fontWeight: 500 }}>{v}</span>
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 120,
      render: (v: string | null) => v || '-',
    },
    {
      title: '分块',
      dataIndex: 'chunk_count',
      key: 'chunk_count',
      width: 80,
      render: (v: number) => <Tag style={{ borderRadius: 4 }}>{v}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      render: (_: unknown, record: Document) => (
        <Space>
          <Button
            type="text"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => setViewDoc(record)}
          />
          <Button
            type="text"
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(record)}
          />
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="text" danger icon={<DeleteOutlined />} size="small" />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── Lint columns ─────────────────────────────────────────

  const severityIcon = (s: string) => {
    switch (s) {
      case 'error':
        return <CloseCircleOutlined style={{ color: token.colorError }} />;
      case 'warning':
        return <WarningOutlined style={{ color: token.colorWarning }} />;
      case 'info':
        return <InfoCircleOutlined style={{ color: token.colorPrimary }} />;
      default:
        return null;
    }
  };

  const lintColumns = [
    {
      title: '级别',
      dataIndex: 'severity',
      key: 'severity',
      width: 70,
      render: (s: string) => (
        <Tag
          color={s === 'error' ? 'error' : s === 'warning' ? 'warning' : 'processing'}
          style={{ borderRadius: 4 }}
        >
          {severityIcon(s)} {s === 'error' ? '错误' : s === 'warning' ? '警告' : '提示'}
        </Tag>
      ),
    },
    {
      title: '检查项',
      dataIndex: 'check_id',
      key: 'check_id',
      width: 140,
      render: (v: string) => (
        <Typography.Text code style={{ fontSize: 12 }}>
          {v}
        </Typography.Text>
      ),
    },
    {
      title: '页面',
      dataIndex: 'page',
      key: 'page',
      width: 180,
      render: (v: string) =>
        v ? (
          <Typography.Text style={{ fontSize: 13 }}>{v}</Typography.Text>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            -
          </Typography.Text>
        ),
    },
    {
      title: '说明',
      dataIndex: 'message',
      key: 'message',
      render: (v: string) => <Typography.Text style={{ fontSize: 13 }}>{v}</Typography.Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: LintIssue) =>
        record.fix_action ? (
          <Button
            size="small"
            icon={<ToolOutlined />}
            loading={fixingIssue === record.check_id}
            onClick={() => handleFixIssue(record.check_id, record.page)}
          >
            修复
          </Button>
        ) : null,
    },
  ];

  // ── Header actions ───────────────────────────────────────

  const headerActions = (
    <Space>
      <Button icon={<SearchOutlined />} onClick={() => setSearchOpen(true)}>
        语义搜索
      </Button>
      <Button icon={<UploadOutlined />} onClick={() => setUploadOpen(true)}>
        上传文档
      </Button>
      <Button
        icon={<ThunderboltOutlined />}
        onClick={handleTriggerProcessAll}
        loading={processingAll}
      >
        触发知识更新
      </Button>
      <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
        添加文档
      </Button>
    </Space>
  );

  // ── Health score color ───────────────────────────────────

  const healthColor = !lintReport
    ? '#d9d9d9'
    : lintReport.health_score >= 80
      ? token.colorSuccess
      : lintReport.health_score >= 50
        ? token.colorWarning
        : token.colorError;

  // ── Tabs definition ──────────────────────────────────────

  const tabItems = [
    {
      key: 'docs',
      label: '文档管理',
      children: (
        <>
          {/* Monitor Status Bar */}
          <Card size="small" style={{ borderRadius: 12, marginBottom: 16 }}>
            <Row gutter={[16, 8]} align="middle">
              <Col>
                <Space size={4}>
                  <Badge status={monitorStatus?.running ? 'success' : 'default'} />
                  <Typography.Text style={{ fontSize: 13 }}>
                    {monitorStatus?.running ? '监控运行中' : '监控已停止'}
                  </Typography.Text>
                </Space>
              </Col>
              {monitorStatus && (
                <>
                  <Col>
                    <Divider type="vertical" />
                  </Col>
                  <Col>
                    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                      监视 {monitorStatus.watched_files} 个文件
                    </Typography.Text>
                  </Col>
                  <Col>
                    <Divider type="vertical" />
                  </Col>
                  <Col>
                    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                      上次检查: {formatTime(monitorStatus.last_check)}
                    </Typography.Text>
                  </Col>
                </>
              )}
            </Row>
          </Card>

          {/* Document list */}
          <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
            {loading ? (
              <div style={{ textAlign: 'center', padding: 60 }}>
                <Spin />
              </div>
            ) : docs.length === 0 ? (
              <Empty
                description="暂无知识文档"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                style={{ padding: 60 }}
              >
                <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                  添加文档
                </Button>
              </Empty>
            ) : (
              <Table
                dataSource={docs}
                columns={columns}
                rowKey="id"
                pagination={{ pageSize: 10, showSizeChanger: false }}
                size="middle"
              />
            )}
          </Card>
        </>
      ),
    },
    {
      key: 'wiki',
      label: 'Wiki 浏览',
      children:
        activeTab === 'wiki' ? (
          <WikiBrowser initialPage={wikiInitialPage} />
        ) : (
          <div style={{ minHeight: 500 }} />
        ),
    },
    {
      key: 'monitor',
      label: '编译监控',
      children:
        activeTab === 'monitor' ? (
          <div>
            {/* Monitor status bar */}
            <Card size="small" style={{ borderRadius: 12, marginBottom: 16 }}>
              <Row gutter={[16, 8]} align="middle">
                <Col>
                  <Space size={4}>
                    <Badge status={monitorStatus?.running ? 'success' : 'default'} />
                    <Typography.Text style={{ fontSize: 13 }}>
                      {monitorStatus?.running ? '监控运行中' : '监控已停止'}
                    </Typography.Text>
                  </Space>
                </Col>
                {monitorStatus && (
                  <>
                    <Col>
                      <Divider type="vertical" />
                    </Col>
                    <Col>
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        监视 {monitorStatus.watched_files} 个文件 · 轮询间隔{' '}
                        {monitorStatus.poll_interval_seconds}s
                      </Typography.Text>
                    </Col>
                    <Col>
                      <Divider type="vertical" />
                    </Col>
                    <Col>
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        上次检查: {formatTime(monitorStatus.last_check)}
                      </Typography.Text>
                    </Col>
                    <Col flex="auto" />
                    <Col>
                      <Space size={8}>
                        <Badge count={changedCount} size="small" offset={[4, 0]}>
                          <Button
                            size="small"
                            onClick={() => setWatchedPanelOpen(!watchedPanelOpen)}
                          >
                            {watchedPanelOpen ? '收起文件列表 ▲' : '展开文件列表 ▼'}
                          </Button>
                        </Badge>
                        <Button
                          type="primary"
                          size="small"
                          icon={<ThunderboltOutlined />}
                          loading={processingAll}
                          onClick={handleTriggerProcessAll}
                        >
                          全部处理
                        </Button>
                      </Space>
                    </Col>
                  </>
                )}
              </Row>
            </Card>

            {/* Watched Files Panel */}
            {watchedPanelOpen && monitorFiles.length > 0 && (
              <Card
                size="small"
                style={{ borderRadius: 12, marginBottom: 16 }}
                title={
                  <Space>
                    <Typography.Text strong style={{ fontSize: 14 }}>
                      监视文件列表
                    </Typography.Text>
                    {changedCount > 0 && (
                      <Tag color="orange" style={{ borderRadius: 4 }}>
                        {changedCount} 个变更
                      </Tag>
                    )}
                  </Space>
                }
              >
                <Table
                  dataSource={monitorFiles}
                  rowKey="path"
                  pagination={false}
                  size="small"
                  columns={[
                    {
                      title: '文件路径',
                      dataIndex: 'path',
                      render: (v: string) => (
                        <Tooltip title={v}>
                          <Space>
                            <FileTextOutlined style={{ color: token.colorPrimary }} />
                            <Typography.Text style={{ fontSize: 13, maxWidth: 320 }} ellipsis>
                              {v}
                            </Typography.Text>
                          </Space>
                        </Tooltip>
                      ),
                    },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 90,
                      render: (s: string) => {
                        const map: Record<string, { color: string; label: string }> = {
                          unchanged: { color: 'default', label: '未变更' },
                          changed: { color: 'orange', label: '已变更' },
                          new: { color: 'green', label: '新增' },
                          deleted: { color: 'red', label: '已删除' },
                        };
                        const cfg = map[s] || { color: 'default', label: s };
                        return (
                          <Tag color={cfg.color} style={{ borderRadius: 4, fontSize: 11 }}>
                            {cfg.label}
                          </Tag>
                        );
                      },
                    },
                    {
                      title: '大小',
                      dataIndex: 'size',
                      width: 80,
                      render: (v: number) => (
                        <Typography.Text style={{ fontSize: 12 }}>{formatSize(v)}</Typography.Text>
                      ),
                    },
                    {
                      title: '修改时间',
                      dataIndex: 'last_modified',
                      width: 160,
                      render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
                    },
                    {
                      title: '操作',
                      key: 'action',
                      width: 80,
                      render: (_: unknown, record: WatchedFileItem) =>
                        record.status === 'changed' || record.status === 'new' ? (
                          <Button
                            type="link"
                            size="small"
                            icon={<ThunderboltOutlined />}
                            loading={processingFilePath === record.path}
                            onClick={() => handleTriggerProcessFile(record.path)}
                          >
                            处理
                          </Button>
                        ) : null,
                    },
                  ]}
                />
              </Card>
            )}

            {/* Lint Report */}
            <Card
              size="small"
              style={{ borderRadius: 12 }}
              title={
                <Space>
                  <Typography.Text strong style={{ fontSize: 14 }}>
                    Lint 体检报告
                  </Typography.Text>
                  <Button
                    size="small"
                    icon={<ReloadOutlined />}
                    loading={lintLoading}
                    onClick={fetchLintReport}
                  >
                    刷新
                  </Button>
                  <Button
                    size="small"
                    type="primary"
                    icon={<ToolOutlined />}
                    loading={fixingAll}
                    onClick={handleFixAll}
                  >
                    全部修复
                  </Button>
                </Space>
              }
            >
              {lintLoading ? (
                <div style={{ textAlign: 'center', padding: 40 }}>
                  <Spin />
                </div>
              ) : !lintReport ? (
                <Empty
                  description="点击刷新加载 Lint 报告"
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  style={{ padding: 32 }}
                />
              ) : (
                <>
                  <Row gutter={16} style={{ marginBottom: 20 }}>
                    <Col span={6}>
                      <Card size="small" style={{ borderRadius: 10, textAlign: 'center' }}>
                        <Progress
                          type="circle"
                          percent={lintReport.health_score}
                          size={100}
                          strokeColor={healthColor}
                          format={(p) => (
                            <span style={{ fontSize: 24, fontWeight: 700, color: healthColor }}>
                              {p}
                            </span>
                          )}
                        />
                        <div style={{ marginTop: 8 }}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            健康评分
                          </Typography.Text>
                        </div>
                      </Card>
                    </Col>
                    <Col span={6}>
                      <Card
                        size="small"
                        style={{
                          borderRadius: 10,
                          textAlign: 'center',
                          borderColor: token.colorError,
                        }}
                      >
                        <Typography.Title level={3} style={{ margin: 0, color: token.colorError }}>
                          {lintReport.errors}
                        </Typography.Title>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          <ExclamationCircleOutlined style={{ marginRight: 4 }} />
                          错误
                        </Typography.Text>
                      </Card>
                    </Col>
                    <Col span={6}>
                      <Card
                        size="small"
                        style={{
                          borderRadius: 10,
                          textAlign: 'center',
                          borderColor: token.colorWarning,
                        }}
                      >
                        <Typography.Title
                          level={3}
                          style={{ margin: 0, color: token.colorWarning }}
                        >
                          {lintReport.warnings}
                        </Typography.Title>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          <WarningOutlined style={{ marginRight: 4 }} />
                          警告
                        </Typography.Text>
                      </Card>
                    </Col>
                    <Col span={6}>
                      <Card
                        size="small"
                        style={{
                          borderRadius: 10,
                          textAlign: 'center',
                          borderColor: token.colorPrimary,
                        }}
                      >
                        <Typography.Title
                          level={3}
                          style={{ margin: 0, color: token.colorPrimary }}
                        >
                          {lintReport.info}
                        </Typography.Title>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          <InfoCircleOutlined style={{ marginRight: 4 }} />
                          提示
                        </Typography.Text>
                      </Card>
                    </Col>
                  </Row>

                  {lintReport.checked_at && (
                    <Typography.Text
                      type="secondary"
                      style={{ fontSize: 12, marginBottom: 12, display: 'block' }}
                    >
                      检查时间: {new Date(lintReport.checked_at).toLocaleString('zh-CN')}
                    </Typography.Text>
                  )}

                  {lintReport.issues.length === 0 ? (
                    <Empty
                      description="所有检查通过"
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      style={{ padding: 24 }}
                    />
                  ) : (
                    <Table
                      dataSource={lintReport.issues}
                      columns={lintColumns}
                      rowKey="check_id"
                      pagination={{ pageSize: 15, showSizeChanger: false }}
                      size="small"
                    />
                  )}
                </>
              )}
            </Card>
          </div>
        ) : (
          <div style={{ minHeight: 500 }} />
        ),
    },
  ];

  // ── Render ───────────────────────────────────────────────

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
          知识库
        </Typography.Title>
        {headerActions}
      </div>

      {/* Tabs */}
      <Tabs
        activeKey={activeTab}
        onChange={(key) => {
          setActiveTab(key);
          if (key !== 'wiki') {
            setWikiInitialPage(undefined);
          }
        }}
        items={tabItems}
        style={{ minHeight: 500 }}
      />

      {/* ── Create / Edit modal (markdown editor) ─────── */}
      <Modal
        title={editDoc ? '编辑文档' : '添加知识文档'}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={handleSaveMarkdown}
        confirmLoading={saving}
        okText={editDoc ? '保存' : '添加'}
        cancelText="取消"
        width={960}
        destroyOnHidden
        styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Input
            placeholder="文档标题"
            value={mdTitle}
            onChange={(e) => setMdTitle(e.target.value)}
          />
          <Input
            placeholder="来源（可选）"
            value={mdSource}
            onChange={(e) => setMdSource(e.target.value)}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            支持 Markdown 语法，可使用工具栏中的图片按钮上传插入图片
          </Typography.Text>
          <div data-color-mode="light">
            <MDEditor
              value={mdValue}
              onChange={(v) => setMdValue(v ?? '')}
              height={400}
              commands={[imageCommand as ICommand]}
              extraCommands={[]}
            />
          </div>
        </Space>
      </Modal>

      {/* ── View document modal ───────────────────────── */}
      <Modal
        title={viewDoc?.title ?? '文档内容'}
        open={!!viewDoc}
        onCancel={() => setViewDoc(null)}
        footer={[
          <Button
            key="edit"
            type="primary"
            icon={<EditOutlined />}
            onClick={() => {
              const d = viewDoc;
              setViewDoc(null);
              if (d) openEdit(d);
            }}
          >
            编辑
          </Button>,
          <Button key="close" onClick={() => setViewDoc(null)}>
            关闭
          </Button>,
        ]}
        width={800}
        destroyOnHidden
      >
        {viewDoc && (
          <div>
            {viewDoc.source && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                来源: {viewDoc.source}
              </Typography.Text>
            )}
            <div style={{ marginTop: 12 }} data-color-mode="light">
              <MDEditor.Markdown source={viewDoc.content} />
            </div>
          </div>
        )}
      </Modal>

      {/* ── Upload modal ──────────────────────────────── */}
      <Modal
        title="上传文档"
        open={uploadOpen}
        onCancel={() => {
          setUploadOpen(false);
          setUploadFile(null);
          setConvertMd(false);
        }}
        onOk={handleFileUpload}
        confirmLoading={uploading}
        okText="上传"
        cancelText="取消"
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          <Upload.Dragger
            accept=".txt,.md,.pdf,.docx,.pptx,.xlsx,.html,.htm"
            showUploadList={false}
            beforeUpload={(file) => {
              setUploadFile(file);
              return false;
            }}
            onRemove={() => setUploadFile(null)}
          >
            {uploadFile ? (
              <div>
                <FileTextOutlined style={{ fontSize: 32, color: token.colorPrimary }} />
                <p style={{ marginTop: 8 }}>{uploadFile.name}</p>
              </div>
            ) : (
              <div>
                <UploadOutlined style={{ fontSize: 32, color: token.colorTextSecondary }} />
                <p style={{ marginTop: 8 }}>点击或拖拽文件到此区域上传</p>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  支持 txt, md, pdf, docx, pptx, xlsx, html 格式
                </Typography.Text>
              </div>
            )}
          </Upload.Dragger>

          <Checkbox checked={convertMd} onChange={(e) => setConvertMd(e.target.checked)}>
            转换为 Markdown 格式（使用 MarkItDown 引擎）
          </Checkbox>

          {convertMd && (
            <Typography.Text type="secondary" style={{ fontSize: 12, paddingLeft: 24 }}>
              将自动把文档转换为 Markdown 格式，保留标题、列表、表格等结构
            </Typography.Text>
          )}
        </Space>
      </Modal>

      {/* ── Search modal ──────────────────────────────── */}
      <Modal
        title="语义搜索"
        open={searchOpen}
        onCancel={() => {
          setSearchOpen(false);
          setSearchResults([]);
        }}
        footer={null}
        width={640}
        destroyOnHidden
      >
        <Space style={{ width: '100%', marginBottom: 16 }}>
          <Input.Search
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onSearch={handleSearch}
            placeholder="输入搜索内容..."
            enterButton="搜索"
            loading={searching}
            style={{ flex: 1 }}
          />
        </Space>

        {searchResults.length === 0 && !searching ? (
          <Empty
            description="输入关键词进行 LLM-WIKI 语义搜索"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          searchResults.map((r, i) => (
            <Card
              key={i}
              size="small"
              style={{ marginBottom: 8, borderRadius: 8 }}
              title={
                <Space>
                  <BookOutlined style={{ color: token.colorPrimary }} />
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{r.title}</span>
                  <Tag style={{ borderRadius: 4, fontSize: 11 }}>{(r.score * 100).toFixed(0)}%</Tag>
                </Space>
              }
            >
              <div style={{ fontSize: 13, margin: 0, color: token.colorTextSecondary }}>
                <MDEditor.Markdown source={r.content} />
              </div>
              {r.source && (
                <Typography.Text style={{ fontSize: 11, color: token.colorTextTertiary }}>
                  来源: {r.source}
                </Typography.Text>
              )}
            </Card>
          ))
        )}
      </Modal>

      {/* ── Processing Result modal ─────────────────────── */}
      <Modal
        title="知识更新结果"
        open={processResultOpen}
        onCancel={() => setProcessResultOpen(false)}
        footer={
          <Button key="close" onClick={() => setProcessResultOpen(false)}>
            关闭
          </Button>
        }
        width={640}
        destroyOnHidden
      >
        {processResult && (
          <>
            <Row gutter={12} style={{ marginBottom: 16 }}>
              <Col span={6}>
                <Card size="small" style={{ borderRadius: 8, textAlign: 'center' }}>
                  <Typography.Title level={3} style={{ margin: 0 }}>
                    {processResult.total}
                  </Typography.Title>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    总计
                  </Typography.Text>
                </Card>
              </Col>
              <Col span={6}>
                <Card
                  size="small"
                  style={{
                    borderRadius: 8,
                    textAlign: 'center',
                    borderColor: token.colorSuccess,
                  }}
                >
                  <Typography.Title level={3} style={{ margin: 0, color: token.colorSuccess }}>
                    {processResult.processed}
                  </Typography.Title>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    已处理
                  </Typography.Text>
                </Card>
              </Col>
              <Col span={6}>
                <Card
                  size="small"
                  style={{
                    borderRadius: 8,
                    textAlign: 'center',
                    borderColor: token.colorWarning,
                  }}
                >
                  <Typography.Title level={3} style={{ margin: 0, color: token.colorWarning }}>
                    {processResult.skipped}
                  </Typography.Title>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    跳过
                  </Typography.Text>
                </Card>
              </Col>
              <Col span={6}>
                <Card
                  size="small"
                  style={{
                    borderRadius: 8,
                    textAlign: 'center',
                    borderColor: token.colorError,
                  }}
                >
                  <Typography.Title level={3} style={{ margin: 0, color: token.colorError }}>
                    {processResult.errors}
                  </Typography.Title>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    错误
                  </Typography.Text>
                </Card>
              </Col>
            </Row>

            <Divider />

            <div style={{ maxHeight: 360, overflowY: 'auto' }}>
              {processResult.results.map((r) => {
                const icon =
                  r.status === 'processed' ? (
                    <CheckCircleOutlined style={{ color: token.colorSuccess }} />
                  ) : r.status === 'skipped' ? (
                    <MinusCircleOutlined style={{ color: token.colorWarning }} />
                  ) : (
                    <CloseCircleOutlined style={{ color: token.colorError }} />
                  );
                const statusLabel =
                  r.status === 'processed' ? '已处理' : r.status === 'skipped' ? '已跳过' : '错误';
                return (
                  <Card key={r.file} size="small" style={{ borderRadius: 8, marginBottom: 8 }}>
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Space>
                        {icon}
                        <Typography.Text strong style={{ fontSize: 13 }}>
                          {r.file}
                        </Typography.Text>
                        <Tag
                          color={
                            r.status === 'processed'
                              ? 'success'
                              : r.status === 'skipped'
                                ? 'warning'
                                : 'error'
                          }
                          style={{ borderRadius: 4, fontSize: 10 }}
                        >
                          {statusLabel}
                        </Tag>
                      </Space>
                      {r.wiki_pages_updated.length > 0 && (
                        <Space wrap size={[4, 4]}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            Wiki 页面:
                          </Typography.Text>
                          {r.wiki_pages_updated.map((p) => (
                            <Tag key={p} style={{ fontSize: 11, borderRadius: 4 }}>
                              {p}
                            </Tag>
                          ))}
                        </Space>
                      )}
                      {r.message && (
                        <Typography.Text
                          type={r.status === 'error' ? 'danger' : 'secondary'}
                          style={{ fontSize: 12 }}
                        >
                          {r.message}
                        </Typography.Text>
                      )}
                    </Space>
                  </Card>
                );
              })}
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}
