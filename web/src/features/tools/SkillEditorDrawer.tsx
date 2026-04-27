import { useEffect, useState, useCallback } from 'react';
import {
  Drawer,
  Tree,
  Button,
  Space,
  Input,
  Typography,
  Spin,
  Empty,
  Alert,
  Tag,
  Tooltip,
  Popconfirm,
  App,
  theme,
  Upload,
  Modal,
} from 'antd';
import {
  FileOutlined,
  FolderOutlined,
  SaveOutlined,
  PlusOutlined,
  UploadOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import MDEditor from '@uiw/react-md-editor';
import api from '@/services/api';

// ── Types ──────────────────────────────────────────────────────────────────

interface ToolItem {
  id: string;
  name: string;
  type: string;
  description: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  is_approved: boolean;
}

interface SkillFileNode {
  name: string;
  type: string;
  path: string;
  children: SkillFileNode[] | null;
  content?: string;
}

interface ValidationResult {
  valid: boolean;
  errors: string[];
}

// ── Props ──────────────────────────────────────────────────────────────────

interface SkillEditorDrawerProps {
  tool: ToolItem;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function buildTreeData(nodes: SkillFileNode[]): DataNode[] {
  if (!nodes) return [];
  return nodes.map((node) => ({
    key: node.path,
    title: node.name,
    icon: node.type === 'directory' ? <FolderOutlined /> : <FileOutlined />,
    isLeaf: node.type === 'file',
    children: node.children ? buildTreeData(node.children) : undefined,
  }));
}

function findNode(type: string, path: string, tree: SkillFileNode | null): boolean {
  if (!tree) return false;
  if (tree.path === path && tree.type === type) return true;
  if (tree.children) {
    for (const child of tree.children) {
      if (findNode(type, path, child)) return true;
    }
  }
  return false;
}

// ── Component ──────────────────────────────────────────────────────────────

export default function SkillEditorDrawer({
  tool,
  open,
  onClose,
  onSaved,
}: SkillEditorDrawerProps) {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();

  // State
  const [loading, setLoading] = useState(true);
  const [fileTree, setFileTree] = useState<SkillFileNode | null>(null);
  const [selectedPath, setSelectedPath] = useState('SKILL.md');
  const [fileContent, setFileContent] = useState('');
  const [originalContent, setOriginalContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [isActive, setIsActive] = useState(tool.is_active);
  const [toggling, setToggling] = useState(false);

  // New file/dir modal
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createType, setCreateType] = useState<'file' | 'directory'>('file');
  const [createPath, setCreatePath] = useState('');
  const [creating, setCreating] = useState(false);

  // ── Load file tree ─────────────────────────────────────────────────────

  const loadTree = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`/tools/${tool.id}/files`);
      setFileTree(res.data);
      if (!selectedPath || selectedPath === 'SKILL.md') {
        setSelectedPath('SKILL.md');
      }
      setValidation(null);
    } catch {
      msg.error('加载文件树失败');
    } finally {
      setLoading(false);
    }
  }, [tool.id, msg, selectedPath]);

  useEffect(() => {
    if (open) {
      loadTree();
      setIsActive(tool.is_active);
    }
  }, [open, loadTree, tool.is_active]);

  // ── Load file content ──────────────────────────────────────────────────

  const loadContent = useCallback(
    async (path: string) => {
      try {
        const res = await api.get(`/tools/${tool.id}/files/content`, { params: { path } });
        const content = res.data.content ?? '';
        setFileContent(content);
        setOriginalContent(content);
      } catch {
        setFileContent('');
        setOriginalContent('');
      }
    },
    [tool.id],
  );

  useEffect(() => {
    if (selectedPath && fileTree) {
      loadContent(selectedPath);
    }
  }, [selectedPath, fileTree, loadContent]);

  // ── Save ───────────────────────────────────────────────────────────────

  const isDirty = fileContent !== originalContent;

  const handleSave = async () => {
    if (!selectedPath || !isDirty) return;
    setSaving(true);
    try {
      await api.put(`/tools/${tool.id}/files/content`, {
        path: selectedPath,
        content: fileContent,
      });
      setOriginalContent(fileContent);
      setValidation(null);
      msg.success('已保存');
      onSaved();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  // Ctrl+S to save
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        handleSave();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  });

  // ── Validate ───────────────────────────────────────────────────────────

  const handleValidate = async () => {
    setValidating(true);
    try {
      const res = await api.post(`/tools/${tool.id}/validate`);
      setValidation(res.data);
      if (res.data.valid) {
        msg.success('校验通过 — 可以启用此 Skill');
      }
    } catch {
      msg.error('校验失败');
    } finally {
      setValidating(false);
    }
  };

  // ── Toggle enable ──────────────────────────────────────────────────────

  const handleToggle = async () => {
    if (!isActive) {
      setValidating(true);
      try {
        const res = await api.post(`/tools/${tool.id}/validate`);
        setValidation(res.data);
        if (!res.data.valid) {
          msg.error('校验未通过，请修复错误后再启用');
          setValidating(false);
          return;
        }
      } catch {
        msg.error('校验失败');
        setValidating(false);
        return;
      }
      setValidating(false);
    }

    setToggling(true);
    try {
      await api.patch(`/tools/${tool.id}`, { is_active: !isActive });
      setIsActive(!isActive);
      msg.success(!isActive ? '已启用' : '已停用');
      onSaved();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '操作失败');
    } finally {
      setToggling(false);
    }
  };

  // ── File operations ────────────────────────────────────────────────────

  const handleCreate = async () => {
    if (!createPath.trim()) {
      msg.warning('请输入路径');
      return;
    }
    setCreating(true);
    try {
      if (createType === 'directory') {
        await api.post(`/tools/${tool.id}/files/directory`, { path: createPath.trim() });
      } else {
        await api.put(`/tools/${tool.id}/files/content`, {
          path: createPath.trim(),
          content: '',
        });
      }
      msg.success(`${createType === 'directory' ? '目录' : '文件'}已创建`);
      setCreateModalOpen(false);
      setCreatePath('');
      loadTree();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (path: string) => {
    try {
      await api.delete(`/tools/${tool.id}/files`, { params: { path } });
      msg.success('已删除');
      if (selectedPath === path) {
        setSelectedPath('SKILL.md');
      }
      loadTree();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      msg.error(detail || '删除失败');
    }
  };

  const handleUpload = async (file: File) => {
    const formData = new FormData();
    formData.append('files', file);
    try {
      await api.post(`/tools/${tool.id}/files/upload`, formData);
      msg.success(`已上传: ${file.name}`);
      loadTree();
    } catch {
      msg.error('上传失败');
    }
    return false;
  };

  // ── Tree selection ─────────────────────────────────────────────────────

  const handleTreeSelect = (selectedKeys: React.Key[]) => {
    if (selectedKeys.length > 0) {
      const key = String(selectedKeys[0]);
      const isDir = findNode('directory', key, fileTree);
      if (!isDir) {
        setSelectedPath(key);
      }
    }
  };

  // ── Build tree nodes ───────────────────────────────────────────────────

  const treeData: DataNode[] = fileTree ? buildTreeData(fileTree.children ?? []) : [];

  const renderTreeTitle = (node: DataNode): React.ReactNode => {
    const path = String(node.key);
    const isSkilMd = path === 'SKILL.md';
    return (
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          width: '100%',
        }}
      >
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {node.title as string}
        </span>
        {!node.isLeaf && (
          <Tooltip title="新建文件/目录">
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                setCreatePath(path ? `${path}/` : '');
                setCreateType('file');
                setCreateModalOpen(true);
              }}
            />
          </Tooltip>
        )}
        {node.isLeaf && !isSkilMd && (
          <Popconfirm
            title="确定删除?"
            onConfirm={(e) => {
              e?.stopPropagation();
              handleDelete(path);
            }}
          >
            <Button
              type="text"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={(e) => e.stopPropagation()}
            />
          </Popconfirm>
        )}
      </div>
    );
  };

  const addTitleRender = (nodes: DataNode[]): DataNode[] =>
    nodes.map((node) => ({
      ...node,
      title: renderTreeTitle(node),
      children: node.children ? addTitleRender(node.children) : undefined,
    }));

  const renderedTreeData = addTitleRender(treeData);

  // ── Is current file markdown? ──────────────────────────────────────────

  const isMarkdown = selectedPath.endsWith('.md');

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <Drawer
      title={
        <Space>
          <span>{tool.name}</span>
          <Tag color={isActive ? 'green' : 'default'} style={{ fontSize: 12 }}>
            {isActive ? '已启用' : '未启用'}
          </Tag>
        </Space>
      }
      open={open}
      onClose={onClose}
      width="92vw"
      destroyOnHidden
      styles={{ body: { padding: 0, display: 'flex', height: 'calc(100vh - 120px)' } }}
      extra={
        <Space>
          <Upload
            showUploadList={false}
            beforeUpload={(file) => {
              handleUpload(file);
              return false;
            }}
          >
            <Button icon={<UploadOutlined />}>上传文件</Button>
          </Upload>
          <Button
            icon={<ExclamationCircleOutlined />}
            onClick={handleValidate}
            loading={validating}
          >
            校验
          </Button>
          <Button
            icon={<SaveOutlined />}
            type="primary"
            onClick={handleSave}
            loading={saving}
            disabled={!isDirty}
          >
            保存
          </Button>
          <Button
            icon={isActive ? <ExclamationCircleOutlined /> : <CheckCircleOutlined />}
            type={isActive ? 'default' : 'primary'}
            danger={isActive}
            onClick={handleToggle}
            loading={toggling}
          >
            {isActive ? '停用' : '启用'}
          </Button>
        </Space>
      }
    >
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', flex: 1 }}>
          <Spin size="large" />
        </div>
      ) : (
        <>
          {/* Left: File Tree */}
          <div
            style={{
              width: 280,
              minWidth: 280,
              borderRight: `1px solid ${token.colorBorderSecondary}`,
              overflow: 'auto',
              padding: 12,
            }}
          >
            <div
              style={{
                marginBottom: 8,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <Typography.Text strong style={{ fontSize: 13 }}>
                文件列表
              </Typography.Text>
              <Space size={4}>
                <Tooltip title="新建文件">
                  <Button
                    type="text"
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={() => {
                      setCreateType('file');
                      setCreatePath('');
                      setCreateModalOpen(true);
                    }}
                  />
                </Tooltip>
                <Tooltip title="刷新">
                  <Button type="text" size="small" icon={<ReloadOutlined />} onClick={loadTree} />
                </Tooltip>
              </Space>
            </div>
            {renderedTreeData.length === 0 ? (
              <Empty description="暂无文件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <Tree
                showIcon
                defaultExpandedKeys={['', 'templates', 'examples', 'references', 'scripts']}
                selectedKeys={[selectedPath]}
                onSelect={handleTreeSelect}
                treeData={renderedTreeData}
                blockNode
              />
            )}
          </div>

          {/* Right: Editor */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            {/* File info bar */}
            <div
              style={{
                padding: '8px 16px',
                borderBottom: `1px solid ${token.colorBorderSecondary}`,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                background: token.colorBgLayout,
              }}
            >
              <Space>
                <FileOutlined />
                <Typography.Text style={{ fontSize: 13, fontFamily: 'monospace' }}>
                  {selectedPath}
                </Typography.Text>
                {isDirty && (
                  <Tag color="orange" style={{ fontSize: 11 }}>
                    已修改
                  </Tag>
                )}
              </Space>
              <Space size={4}>
                {validation && (
                  <Tag color={validation.valid ? 'green' : 'red'} style={{ fontSize: 11 }}>
                    {validation.valid ? '校验通过' : '校验失败'}
                  </Tag>
                )}
              </Space>
            </div>

            {/* Validation errors */}
            {validation && !validation.valid && (
              <Alert
                message="校验失败 — 保存后可以修复，但无法启用此 Skill"
                description={
                  <ul style={{ margin: 0, paddingLeft: 20 }}>
                    {validation.errors.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                }
                type="error"
                showIcon
                closable
                style={{ borderRadius: 0 }}
              />
            )}

            {/* Editor */}
            <div style={{ flex: 1, overflow: 'auto' }}>
              {isMarkdown ? (
                <div data-color-mode="dark" style={{ height: '100%' }}>
                  <MDEditor
                    value={fileContent}
                    onChange={(v) => setFileContent(v ?? '')}
                    height="100%"
                    visibleDragbar={false}
                  />
                </div>
              ) : (
                <Input.TextArea
                  value={fileContent}
                  onChange={(e) => setFileContent(e.target.value)}
                  style={{
                    height: '100%',
                    resize: 'none',
                    border: 'none',
                    borderRadius: 0,
                    fontFamily: "'Fira Code', 'Cascadia Code', 'Consolas', monospace",
                    fontSize: 13,
                    lineHeight: 1.6,
                    padding: 16,
                  }}
                />
              )}
            </div>

            {/* Bottom status bar */}
            <div
              style={{
                padding: '4px 16px',
                borderTop: `1px solid ${token.colorBorderSecondary}`,
                display: 'flex',
                justifyContent: 'space-between',
                background: token.colorBgLayout,
              }}
            >
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {isMarkdown ? 'Markdown 编辑器' : '纯文本编辑器'} · Ctrl+S 保存
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {fileContent.split('\n').length} 行 · {fileContent.length} 字符
              </Typography.Text>
            </div>
          </div>
        </>
      )}

      {/* Create File/Dir Modal */}
      <Modal
        title={createType === 'file' ? '新建文件' : '新建目录'}
        open={createModalOpen}
        onOk={handleCreate}
        onCancel={() => {
          setCreateModalOpen(false);
          setCreatePath('');
        }}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
        width={400}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Button
              type={createType === 'file' ? 'primary' : 'default'}
              size="small"
              onClick={() => setCreateType('file')}
            >
              文件
            </Button>
            <Button
              type={createType === 'directory' ? 'primary' : 'default'}
              size="small"
              onClick={() => setCreateType('directory')}
            >
              目录
            </Button>
          </Space>
          <Input
            placeholder={createType === 'file' ? '如 templates/readme.md' : '如 my-helpers'}
            value={createPath}
            onChange={(e) => setCreatePath(e.target.value)}
            onPressEnter={handleCreate}
          />
        </Space>
      </Modal>
    </Drawer>
  );
}
