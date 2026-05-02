import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, List, Typography, Empty, Segmented, Button, Space, App, Popconfirm } from 'antd';
import {
  FileTextOutlined,
  GlobalOutlined,
  TeamOutlined,
  LockOutlined,
  CopyOutlined,
  MessageOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

interface ReportItem {
  id: string;
  title: string;
  description?: string;
  theme: string;
  visibility: string;
  session_id: string | null;
  created_at: string;
}

const VIS_OPTIONS = [
  { value: 'private', label: '🔒 不分享' },
  { value: 'space', label: '👥 空间内' },
  { value: 'public', label: '🌐 公开' },
];

const VIS_ICON: Record<string, React.ReactNode> = {
  public: <GlobalOutlined style={{ color: '#22c55e' }} />,
  space: <TeamOutlined style={{ color: '#3b82f6' }} />,
  private: <LockOutlined style={{ color: '#ef4444' }} />,
};

const VIS_LABEL: Record<string, string> = {
  public: '公开',
  space: '空间内',
  private: '不分享',
};

export default function ReportListPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchReports = () => {
    setLoading(true);
    api
      .get('/reports?limit=100')
      .then((res) => setReports(res.data ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchReports();
  }, []);

  const handleVisibility = async (reportId: string, vis: string) => {
    setReports((prev) => prev.map((r) => (r.id === reportId ? { ...r, visibility: vis } : r)));
    try {
      await api.put(`/reports/${reportId}`, { visibility: vis });
      const labels: Record<string, string> = {
        private: '已设为不分享',
        space: '已设为空间内可见',
        public: '已设为公开访问',
      };
      message.success(labels[vis] || '已更新');
    } catch {
      fetchReports();
      message.error('更新失败');
    }
  };

  const handleCopyUrl = (reportId: string) => {
    navigator.clipboard.writeText(`${window.location.origin}/pub/reports/${reportId}`);
    message.success('链接已复制');
  };

  const handleDelete = async (reportId: string) => {
    try {
      await api.delete(`/reports/${reportId}`);
      setReports((prev) => prev.filter((r) => r.id !== reportId));
      message.success('已删除');
    } catch {
      message.error('删除失败');
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 960, margin: '0 auto' }}>
      <Typography.Title level={4} style={{ marginBottom: 24 }}>
        我的报告
      </Typography.Title>
      <List
        loading={loading}
        dataSource={reports}
        locale={{ emptyText: <Empty description="暂无报告，在对话中让智能体生成报告即可" /> }}
        renderItem={(item) => (
          <Card
            hoverable
            size="small"
            style={{ marginBottom: 12, borderRadius: 10 }}
            onClick={() => navigate(`/ops/reports/${item.id}`)}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <FileTextOutlined style={{ fontSize: 20, color: 'var(--accent)', marginTop: 2 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <Typography.Text strong ellipsis style={{ display: 'block' }}>
                  {item.title}
                </Typography.Text>
                {item.description && (
                  <Typography.Paragraph
                    type="secondary"
                    style={{ margin: '2px 0 8px', fontSize: 12 }}
                    ellipsis={{ rows: 1 }}
                  >
                    {item.description}
                  </Typography.Paragraph>
                )}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {new Date(item.created_at).toLocaleString('zh-CN')}
                  </Typography.Text>
                  <span
                    style={{ fontSize: 11, display: 'inline-flex', alignItems: 'center', gap: 2 }}
                  >
                    {VIS_ICON[item.visibility]} {VIS_LABEL[item.visibility]}
                  </span>
                </div>
              </div>
              <div
                style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}
                onClick={(e) => e.stopPropagation()}
              >
                <Segmented
                  size="small"
                  value={item.visibility}
                  options={VIS_OPTIONS}
                  onChange={(val) => handleVisibility(item.id, val as string)}
                />
                <Space size={4}>
                  <Button
                    size="small"
                    type="text"
                    icon={<CopyOutlined />}
                    onClick={() => handleCopyUrl(item.id)}
                  />
                  {item.session_id && (
                    <Button
                      size="small"
                      type="text"
                      icon={<MessageOutlined />}
                      onClick={() => navigate(`/ops/chat?session=${item.session_id}`)}
                    />
                  )}
                  <Popconfirm
                    title="确定删除此报告？"
                    onConfirm={() => handleDelete(item.id)}
                    okText="删除"
                    cancelText="取消"
                  >
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              </div>
            </div>
          </Card>
        )}
      />
    </div>
  );
}
