import { useEffect, useState, useCallback } from 'react';
import { Drawer, Tabs, Upload, Button, List, Typography, Modal, message, Empty } from 'antd';
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
} from '@ant-design/icons';
import api from '@/services/api';

interface SessionFile {
  id: string;
  session_id: string;
  filename: string;
  file_size: number;
  mime_type: string | null;
  content_text: string | null;
  created_at: string | null;
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
  if (mime.includes('text')) return <FileTextOutlined />;
  return <FileOutlined />;
}

export default function ContextPanel({ open, onClose, sessionId }: Props) {
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);

  const fetchFiles = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const res = await api.get(`/sessions/${sessionId}/files`);
      setFiles(res.data || []);
    } catch {
      // session might not exist yet
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (open && sessionId) fetchFiles();
  }, [open, sessionId, fetchFiles]);

  const handleUpload = async (file: File) => {
    if (!sessionId) return false;
    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    try {
      await api.post(`/sessions/${sessionId}/files`, formData);
      await fetchFiles();
      message.success(`${file.name} 上传成功`);
    } catch {
      message.error('上传失败');
    } finally {
      setUploading(false);
    }
    return false;
  };

  const handleDelete = async (file: SessionFile) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除 "${file.filename}" 吗？删除后对话将无法引用其内容。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await api.delete(`/sessions/${sessionId}/files/${file.id}`);
          await fetchFiles();
          message.success('删除成功');
        } catch {
          message.error('删除失败');
        }
      },
    });
  };

  return (
    <Drawer
      title="上下文"
      open={open}
      onClose={onClose}
      width={360}
      styles={{ body: { padding: 0 } }}
    >
      <Tabs
        style={{ padding: '0 24px' }}
        items={[
          {
            key: 'files',
            label: '文件',
            children: (
              <>
                <div style={{ padding: '12px 0' }}>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      marginBottom: 12,
                    }}
                  >
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      上传文档作为对话上下文，在输入框中使用{' '}
                      <Typography.Text code>@文件名</Typography.Text> 引用
                    </Typography.Text>
                    <Upload
                      showUploadList={false}
                      beforeUpload={(file) => {
                        handleUpload(file);
                        return false;
                      }}
                      multiple
                    >
                      <Button
                        type="primary"
                        size="small"
                        icon={<UploadOutlined />}
                        loading={uploading}
                      >
                        上传
                      </Button>
                    </Upload>
                  </div>
                </div>

                {files.length === 0 ? (
                  <div style={{ padding: 48, textAlign: 'center' }}>
                    <Empty description="暂无上下文文件">
                      <Upload
                        showUploadList={false}
                        beforeUpload={(file) => {
                          handleUpload(file);
                          return false;
                        }}
                      >
                        <Button icon={<InboxOutlined />}>点击上传</Button>
                      </Upload>
                    </Empty>
                  </div>
                ) : (
                  <List
                    loading={loading}
                    dataSource={files}
                    style={{ margin: '0 -24px' }}
                    renderItem={(item) => (
                      <List.Item
                        style={{ padding: '12px 24px' }}
                        actions={[
                          <Button
                            key="delete"
                            type="text"
                            size="small"
                            danger
                            icon={<DeleteOutlined />}
                            onClick={() => handleDelete(item)}
                          />,
                        ]}
                      >
                        <List.Item.Meta
                          avatar={fileIcon(item.mime_type)}
                          title={
                            <Typography.Text
                              style={{ maxWidth: 220 }}
                              ellipsis={{ tooltip: item.filename }}
                            >
                              {item.filename}
                            </Typography.Text>
                          }
                          description={
                            <span>
                              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                                {formatSize(item.file_size)}
                              </Typography.Text>
                              {item.content_text && (
                                <Typography.Text
                                  type="success"
                                  style={{ fontSize: 11, marginLeft: 8 }}
                                >
                                  已解析
                                </Typography.Text>
                              )}
                            </span>
                          }
                        />
                      </List.Item>
                    )}
                  />
                )}
              </>
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
