import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Spin, Typography, Space, App, Segmented, Tag, Tooltip, Divider } from 'antd';
import {
  ArrowLeftOutlined,
  ShareAltOutlined,
  DownloadOutlined,
  MessageOutlined,
  GlobalOutlined,
  TeamOutlined,
  LockOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

interface Report {
  id: string;
  title: string;
  description?: string;
  html_content: string;
  theme: string;
  created_at: string;
  visibility: 'private' | 'space' | 'public';
  session_id: string | null;
}

const THEME_COLORS: Record<string, string> = {
  ink: '#64748b',
  ops: '#3b82f6',
  security: '#ef4444',
  performance: '#f59e0b',
  incident: '#8b5cf6',
  capacity: '#06b6d4',
  compliance: '#10b981',
};

const VIS_ICON: Record<string, React.ReactNode> = {
  public: <GlobalOutlined />,
  space: <TeamOutlined />,
  private: <LockOutlined />,
};

const VIS_LABEL: Record<string, string> = {
  public: '公开',
  space: '空间内',
  private: '不分享',
};

const VIS_COLOR: Record<string, string> = {
  public: '#22c55e',
  space: '#3b82f6',
  private: '#ef4444',
};

export default function ReportViewerPage() {
  const { reportId } = useParams<{ reportId: string }>();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get(`/reports/${reportId}`)
      .then((res) => setReport(res.data))
      .catch(() => message.error('报告未找到'))
      .finally(() => setLoading(false));
  }, [reportId]);

  const handleCopyLink = () => {
    const publicUrl = `${window.location.origin}/pub/reports/${reportId}`;
    navigator.clipboard.writeText(publicUrl);
    message.success('分享链接已复制');
  };

  const handleDownload = () => {
    if (!report) return;
    const blob = new Blob([report.html_content], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${report.title}.html`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleVisibility = async (val: string) => {
    if (!report) return;
    await api.put(`/reports/${reportId}`, { visibility: val });
    setReport({ ...report, visibility: val as Report['visibility'] });
    const labels: Record<string, string> = {
      private: '已设为不分享',
      space: '已设为空间内可见',
      public: '已设为公开访问',
    };
    message.success(labels[val] || '已更新');
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 100 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!report) {
    return (
      <div style={{ textAlign: 'center', paddingTop: 100 }}>
        <Typography.Text type="secondary">报告未找到</Typography.Text>
        <br />
        <Button style={{ marginTop: 16 }} onClick={() => navigate('/ops/reports')}>
          返回列表
        </Button>
      </div>
    );
  }

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg-elevated)',
      }}
    >
      {/* Toolbar */}
      <div
        style={{
          padding: '10px 20px',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'var(--bg-base)',
          flexShrink: 0,
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        <Space wrap>
          <Button icon={<ArrowLeftOutlined />} type="text" onClick={() => navigate('/ops/reports')}>
            返回
          </Button>
          <Divider type="vertical" />
          <Typography.Text strong style={{ fontSize: 15, maxWidth: 360 }} ellipsis>
            {report.title}
          </Typography.Text>
          {report.theme && (
            <Tag color={THEME_COLORS[report.theme] || '#64748b'} style={{ borderRadius: 6 }}>
              {report.theme}
            </Tag>
          )}
          <Tooltip title={new Date(report.created_at).toLocaleString('zh-CN')}>
            <Space size={4} style={{ fontSize: 12, color: 'var(--fg-secondary)' }}>
              <ClockCircleOutlined />
              {new Date(report.created_at).toLocaleDateString('zh-CN')}
            </Space>
          </Tooltip>
          <span
            style={{
              fontSize: 12,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              color: VIS_COLOR[report.visibility],
            }}
          >
            {VIS_ICON[report.visibility]}
            {VIS_LABEL[report.visibility]}
          </span>
          <Segmented
            size="small"
            value={report.visibility}
            options={[
              { value: 'private', label: '不分享' },
              { value: 'space', label: '空间内' },
              { value: 'public', label: '公开' },
            ]}
            onChange={(val) => handleVisibility(val as string)}
          />
        </Space>

        <Space>
          {report.session_id && (
            <Button
              icon={<MessageOutlined />}
              onClick={() => navigate(`/ops/chat?session=${report.session_id}`)}
            >
              对话
            </Button>
          )}
          <Button icon={<ShareAltOutlined />} onClick={handleCopyLink}>
            分享
          </Button>
          <Button icon={<DownloadOutlined />} onClick={handleDownload}>
            下载
          </Button>
        </Space>
      </div>

      {/* Report body — sandboxed iframe for proper full-HTML rendering */}
      <iframe
        srcDoc={report.html_content}
        title={report.title}
        style={{
          flex: 1,
          width: '100%',
          border: 'none',
        }}
        sandbox="allow-scripts"
      />
    </div>
  );
}
