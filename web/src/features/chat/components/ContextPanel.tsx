import { useEffect, useState, useCallback } from 'react';
import {
  Drawer,
  Tabs,
  Button,
  Typography,
  Modal,
  message,
  Empty,
  Dropdown,
  Input,
  Tree,
  Space,
  Spin,
} from 'antd';
import type { MenuProps } from 'antd';
import {
  UploadOutlined,
  DeleteOutlined,
  FileTextOutlined,
  FilePdfOutlined,
  FileExcelOutlined,
  FileWordOutlined,
  FilePptOutlined,
  FileImageOutlined,
  FileOutlined,
  InboxOutlined,
  LockOutlined,
  NodeIndexOutlined,
  FolderOutlined,
  FolderAddOutlined,
  MoreOutlined,
  EditOutlined,
  GlobalOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import api from '@/services/api';
import { useChatStore } from '@/stores/chatStore';

const { DirectoryTree } = Tree;

interface SessionFile {
  id: string;
  session_id: string;
  filename: string;
  file_size: number;
  mime_type: string | null;
  content_text: string | null;
  folder_path: string;
  created_at: string | null;
}

interface FileTreeNode {
  name: string;
  type: 'file' | 'folder';
  children?: FileTreeNode[];
  id?: string;
  mime_type?: string | null;
  file_size?: number;
  folder_path?: string;
}

interface AntTreeNode {
  key: string;
  title: string;
  isLeaf?: boolean;
  children?: AntTreeNode[];
  data?: FileTreeNode;
}

interface ReportSummary {
  id: string;
  title: string;
  visibility: string;
  created_at: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  sessionId: string | null;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function fileIcon(mime: string | null) {
  if (!mime) return <FileOutlined />;
  if (mime.includes('pdf')) return <FilePdfOutlined style={{ color: '#ef4444' }} />;
  if (mime.includes('word')) return <FileWordOutlined style={{ color: '#3b82f6' }} />;
  if (mime.includes('spreadsheet') || mime.includes('excel'))
    return <FileExcelOutlined style={{ color: '#22c55e' }} />;
  if (mime.includes('presentation') || mime.includes('powerpoint'))
    return <FilePptOutlined style={{ color: '#f97316' }} />;
  if (mime.includes('image')) return <FileImageOutlined style={{ color: '#a855f7' }} />;
  if (mime.includes('text') || mime.includes('markdown')) return <FileTextOutlined />;
  return <FileOutlined />;
}

const PERSISTED_SESSION_KEY = 'aiops_persisted_session_id';

function ensureSessionId(sessionId: string | null): string {
  if (sessionId) {
    localStorage.setItem(PERSISTED_SESSION_KEY, sessionId);
    return sessionId;
  }
  const stored = localStorage.getItem(PERSISTED_SESSION_KEY);
  if (stored) {
    useChatStore.getState().setSessionId(stored);
    return stored;
  }
  const sid = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  localStorage.setItem(PERSISTED_SESSION_KEY, sid);
  useChatStore.getState().setSessionId(sid);
  return sid;
}

function convertTreeData(nodes: FileTreeNode[]): AntTreeNode[] {
  return nodes.map((node) => {
    if (node.type === 'folder') {
      return {
        key: `folder:${node.name}`,
        title: node.name,
        isLeaf: false,
        children: node.children ? convertTreeData(node.children) : [],
        data: node,
      };
    }
    return {
      key: `file:${node.id}`,
      title: node.name,
      isLeaf: true,
      children: undefined,
      data: node,
    };
  });
}

export default function ContextPanel({ open, onClose, sessionId }: Props) {
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [currentFolder, setCurrentFolder] = useState('/');
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [renamingFile, setRenamingFile] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [pendingFolders, setPendingFolders] = useState<Set<string>>(new Set());

  const sid = ensureSessionId(sessionId);

  const fetchTree = useCallback(async () => {
    if (!sid) return;
    setLoading(true);
    try {
      const res = await api.get(`/sessions/${sid}/files/tree`);
      const tree = res.data;
      const flatFiles: SessionFile[] = [];
      function walk(nodes: FileTreeNode[]) {
        for (const n of nodes) {
          if (n.type === 'file') {
            flatFiles.push({
              id: n.id!,
              session_id: sid,
              filename: n.name,
              file_size: n.file_size ?? 0,
              mime_type: n.mime_type ?? null,
              content_text: null,
              folder_path: n.folder_path ?? '/',
              created_at: null,
            });
          }
          if (n.children) walk(n.children);
        }
      }
      walk(tree.children ?? []);
      setFiles(flatFiles);
    } catch (err) {
      console.error('ContextPanel fetchTree failed:', err);
    } finally {
      setLoading(false);
    }
  }, [sid]);

  useEffect(() => {
    if (open) fetchTree();
  }, [open, fetchTree]);

  const fetchReports = useCallback(async () => {
    setReportsLoading(true);
    try {
      const res = await api.get('/reports?limit=50');
      setReports(res.data ?? []);
    } catch {
      // silently fail
    } finally {
      setReportsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) fetchReports();
  }, [open, fetchReports]);

  const handleUpload = async (file: File) => {
    console.log('[ContextPanel] handleUpload called:', file.name, 'sid=', sid);
    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await api.post(
        `/sessions/${sid}/files?folder_path=${encodeURIComponent(currentFolder)}`,
        formData,
      );
      console.log('[ContextPanel] upload response:', res.data);
      await fetchTree();
      message.success(`${file.name} 上传成功`);
    } catch (err: any) {
      console.error(
        '[ContextPanel] upload error:',
        err?.response?.status,
        err?.response?.data || err,
      );
      const detail = err?.response?.data?.detail || err?.message || '未知错误';
      if (err?.response?.status === 401) {
        message.error('登录已过期，请重新登录');
      } else if (err?.response?.status === 413) {
        message.error('文件过大，请选择小于 50MB 的文件');
      } else {
        message.error(`上传失败: ${detail}`);
      }
    } finally {
      setUploading(false);
    }
    return false;
  };

  const handleDelete = async (fileId: string, filename: string) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除 "${filename}" 吗？`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await api.delete(`/sessions/${sid}/files/${fileId}`);
          await fetchTree();
          message.success('删除成功');
        } catch {
          message.error('删除失败');
        }
      },
    });
  };

  const handleRename = async (fileId: string, newName: string) => {
    if (!newName.trim()) return;
    try {
      await api.patch(
        `/sessions/${sid}/files/${fileId}/rename?new_name=${encodeURIComponent(newName)}`,
      );
      await fetchTree();
      message.success('重命名成功');
    } catch {
      message.error('重命名失败');
    }
    setRenamingFile(null);
    setRenameValue('');
  };

  const handleDrop = async (info: any) => {
    const dragKey = info.dragNode?.key;
    const dropKey = info.node?.key;
    if (!dragKey || !dropKey || dragKey === dropKey) return;

    const targetNode = info.node?.data as FileTreeNode | undefined;
    let targetFolder = '/';
    if (targetNode?.type === 'folder') {
      targetFolder = `/${targetNode.name}`;
    } else if (targetNode?.folder_path) {
      targetFolder = targetNode.folder_path;
    }

    const fileId = (dragKey as string).replace('file:', '');
    if (!fileId || fileId === dragKey) return;

    try {
      await api.patch(
        `/sessions/${sid}/files/${fileId}/move?target_folder=${encodeURIComponent(targetFolder)}`,
      );
      await fetchTree();
      message.success('移动成功');
    } catch {
      message.error('移动失败');
    }
  };

  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) return;
    const folderPath =
      currentFolder === '/'
        ? `/${newFolderName.trim()}`
        : `${currentFolder}/${newFolderName.trim()}`;
    try {
      await api.post(`/sessions/${sid}/folders?folder_path=${encodeURIComponent(folderPath)}`);
      message.success('文件夹创建成功');
      setNewFolderName('');
      setShowNewFolder(false);
      setPendingFolders((prev) => new Set(prev).add(folderPath));
      await fetchTree();
    } catch {
      message.error('创建失败');
    }
  };

  const contextMenu = (fileId: string, filename: string): MenuProps => ({
    items: [
      {
        key: 'rename',
        icon: <EditOutlined />,
        label: '重命名',
        onClick: () => {
          setRenamingFile(fileId);
          setRenameValue(filename);
        },
      },
      { type: 'divider' },
      {
        key: 'delete',
        icon: <DeleteOutlined />,
        label: '删除',
        danger: true,
        onClick: () => handleDelete(fileId, filename),
      },
    ],
  });

  const treeData = (() => {
    const folderMap: Record<string, { children: FileTreeNode[] }> = {};
    for (const f of files) {
      const fp = f.folder_path || '/';
      if (!folderMap[fp]) folderMap[fp] = { children: [] };
      folderMap[fp].children.push({
        name: f.filename,
        type: 'file',
        id: f.id,
        mime_type: f.mime_type,
        file_size: f.file_size,
        folder_path: fp,
      });
    }

    const rootChildren: FileTreeNode[] = [];
    for (const [fp, folder] of Object.entries(folderMap)) {
      if (fp === '/') {
        rootChildren.push(...folder.children);
      } else {
        const parts = fp.replace(/^\//, '').split('/');
        let current = rootChildren;
        for (let i = 0; i < parts.length; i++) {
          const part = parts[i];
          let found = current.find((c) => c.type === 'folder' && c.name === part) as
            | FileTreeNode
            | undefined;
          if (!found) {
            found = { name: part, type: 'folder', children: [] };
            current.push(found);
          }
          current = found.children!;
          if (i === parts.length - 1) {
            current.push(...folder.children);
          }
        }
      }
    }

    // Ensure pending (empty) folders appear in the tree
    for (const fp of pendingFolders) {
      if (fp === '/') continue;
      const parts = fp.replace(/^\//, '').split('/');
      let current = rootChildren;
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        let found = current.find((c) => c.type === 'folder' && c.name === part) as
          | FileTreeNode
          | undefined;
        if (!found) {
          found = { name: part, type: 'folder', children: [] };
          current.push(found);
        }
        current = found.children!;
      }
    }

    return convertTreeData(rootChildren);
  })();

  return (
    <Drawer
      title="上下文"
      open={open}
      onClose={onClose}
      width={480}
      styles={{ body: { padding: 0 } }}
    >
      <Tabs
        style={{ padding: '0 16px' }}
        items={[
          {
            key: 'files',
            label: '文件',
            children: (
              <>
                <div style={{ padding: '8px 0', display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    type="file"
                    id="context-file-upload"
                    style={{ display: 'none' }}
                    multiple
                    onChange={(e) => {
                      const fileList = e.target.files;
                      if (fileList) {
                        for (let i = 0; i < fileList.length; i++) {
                          console.log('[ContextPanel] input onChange:', fileList[i].name);
                          handleUpload(fileList[i]);
                        }
                      }
                      e.target.value = '';
                    }}
                  />
                  <Button
                    type="primary"
                    size="small"
                    icon={<UploadOutlined />}
                    loading={uploading}
                    onClick={() => {
                      console.log('[ContextPanel] upload button clicked');
                      document.getElementById('context-file-upload')?.click();
                    }}
                  >
                    上传
                  </Button>
                  <Button
                    size="small"
                    icon={<FolderAddOutlined />}
                    onClick={() => setShowNewFolder(!showNewFolder)}
                  />
                  <Typography.Text
                    type="secondary"
                    style={{ fontSize: 11, flex: 1, textAlign: 'right' }}
                  >
                    {currentFolder}
                  </Typography.Text>
                </div>

                {showNewFolder && (
                  <div style={{ padding: '0 0 8px' }}>
                    <Space.Compact style={{ width: '100%' }}>
                      <Input
                        size="small"
                        placeholder="文件夹名称"
                        value={newFolderName}
                        onChange={(e) => setNewFolderName(e.target.value)}
                        onPressEnter={handleCreateFolder}
                      />
                      <Button size="small" type="primary" onClick={handleCreateFolder}>
                        确定
                      </Button>
                      <Button size="small" onClick={() => setShowNewFolder(false)}>
                        取消
                      </Button>
                    </Space.Compact>
                  </div>
                )}

                {renamingFile && (
                  <div style={{ padding: '0 0 8px' }}>
                    <Space.Compact style={{ width: '100%' }}>
                      <Input
                        size="small"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onPressEnter={() => handleRename(renamingFile, renameValue)}
                      />
                      <Button
                        size="small"
                        type="primary"
                        onClick={() => handleRename(renamingFile, renameValue)}
                      >
                        确定
                      </Button>
                      <Button
                        size="small"
                        onClick={() => {
                          setRenamingFile(null);
                          setRenameValue('');
                        }}
                      >
                        取消
                      </Button>
                    </Space.Compact>
                  </div>
                )}

                {loading && files.length === 0 ? null : files.length === 0 ? (
                  <div style={{ padding: 48, textAlign: 'center' }}>
                    <Empty description="暂无上下文文件">
                      <Button
                        icon={<InboxOutlined />}
                        onClick={() => document.getElementById('context-file-upload')?.click()}
                      >
                        点击上传
                      </Button>
                    </Empty>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      sid: {sid.slice(0, 8)}... / files: {files.length}
                    </Typography.Text>
                  </div>
                ) : (
                  <>
                    <Typography.Text
                      type="secondary"
                      style={{ fontSize: 11, padding: '4px 0', display: 'block' }}
                    >
                      sid: {sid.slice(0, 8)}... / files: {files.length}
                    </Typography.Text>
                    <DirectoryTree
                      key={files.map((f) => f.id).join(',')}
                      style={{ margin: '0 -16px' }}
                      showIcon
                      treeData={treeData}
                      expandedKeys={expandedKeys}
                      onExpand={(keys) => setExpandedKeys(keys as string[])}
                      draggable={{ icon: false }}
                      onDrop={handleDrop}
                      titleRender={(node) => {
                        const data = (node as any).data as FileTreeNode | undefined;
                        if (!data) return <span>{node.title as string}</span>;

                        if (data.type === 'folder') {
                          return (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <FolderOutlined style={{ color: '#faad14' }} />
                              <span>{node.title as string}</span>
                            </div>
                          );
                        }

                        return (
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 6,
                              padding: '2px 0',
                              width: '100%',
                            }}
                          >
                            {fileIcon(data.mime_type ?? null)}
                            <Typography.Text
                              ellipsis
                              style={{ flex: 1, minWidth: 0, fontSize: 13 }}
                            >
                              {node.title as string}
                            </Typography.Text>
                            <Typography.Text
                              type="secondary"
                              style={{ fontSize: 10, flexShrink: 0 }}
                            >
                              {formatSize(data.file_size ?? 0)}
                            </Typography.Text>
                            <Dropdown menu={contextMenu(data.id!, data.name)} trigger={['click']}>
                              <Button
                                type="text"
                                size="small"
                                icon={<MoreOutlined />}
                                onClick={(e) => e.stopPropagation()}
                                style={{ flexShrink: 0 }}
                              />
                            </Dropdown>
                          </div>
                        );
                      }}
                      onSelect={(_keys, info) => {
                        const data = (info.node as any).data as FileTreeNode | undefined;
                        if (data?.type === 'folder') {
                          setCurrentFolder(
                            currentFolder === '/'
                              ? `/${data.name}`
                              : `${currentFolder}/${data.name}`,
                          );
                        } else if (data?.type === 'file') {
                          navigator.clipboard.writeText(data.name);
                          message.info(`已复制文件名: ${data.name}，在输入框中 @引用`);
                        }
                      }}
                    />
                  </>
                )}

                <Typography.Text
                  type="secondary"
                  style={{ fontSize: 11, display: 'block', marginTop: 8 }}
                >
                  点击文件夹设为上传目标，在输入框中使用{' '}
                  <Typography.Text code>@文件名</Typography.Text> 引用
                </Typography.Text>
              </>
            ),
          },
          {
            key: 'reports',
            label: '报告',
            children: (
              <div>
                {reportsLoading ? (
                  <div style={{ textAlign: 'center', paddingTop: 40 }}>
                    <Spin size="small" />
                  </div>
                ) : reports.length === 0 ? (
                  <Empty description="暂无报告" style={{ paddingTop: 40 }} />
                ) : (
                  <div style={{ paddingTop: 4 }}>
                    {reports.map((r) => {
                      const visIcon =
                        r.visibility === 'public' ? (
                          <GlobalOutlined style={{ color: '#22c55e', fontSize: 12 }} />
                        ) : r.visibility === 'space' ? (
                          <TeamOutlined style={{ color: '#3b82f6', fontSize: 12 }} />
                        ) : (
                          <LockOutlined style={{ color: '#ef4444', fontSize: 12 }} />
                        );
                      const visLabel =
                        r.visibility === 'public'
                          ? '公开'
                          : r.visibility === 'space'
                            ? '空间内'
                            : '不分享';
                      return (
                        <div
                          key={r.id}
                          onClick={() => window.open(`/pub/reports/${r.id}`, '_blank')}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 6,
                            padding: '8px 4px',
                            cursor: 'pointer',
                            borderBottom: '1px solid var(--border)',
                          }}
                        >
                          <FileTextOutlined style={{ fontSize: 14, flexShrink: 0 }} />
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <Typography.Text ellipsis style={{ fontSize: 13, display: 'block' }}>
                              {r.title}
                            </Typography.Text>
                            <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                              {new Date(r.created_at).toLocaleString('zh-CN')}
                            </Typography.Text>
                          </div>
                          <span
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 2,
                              fontSize: 10,
                              color: 'var(--fg-secondary)',
                              flexShrink: 0,
                            }}
                          >
                            {visIcon} {visLabel}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ),
          },
          {
            key: 'workflow',
            label: (
              <span style={{ color: '#bfbfbf' }}>
                <LockOutlined style={{ marginRight: 4 }} />
                流程
              </span>
            ),
            disabled: true,
            children: (
              <div style={{ padding: 48, textAlign: 'center' }}>
                <Empty
                  image={<NodeIndexOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />}
                  description={
                    <span>
                      <Typography.Text type="secondary">ITSM / OA / BPM 流程集成</Typography.Text>
                      <br />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        即将上线，敬请期待
                      </Typography.Text>
                    </span>
                  }
                />
              </div>
            ),
          },
        ]}
      />
    </Drawer>
  );
}
