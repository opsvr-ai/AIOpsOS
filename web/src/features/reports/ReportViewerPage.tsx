import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Spin, Typography, Space, App, Segmented } from 'antd';
import { ArrowLeftOutlined, ShareAltOutlined, DownloadOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface Report {
  id: string;
  title: string;
  description?: string;
  html_content: string;
  theme: string;
  created_at: string;
  visibility: 'private' | 'space' | 'public';
}

export default function ReportViewerPage() {
  const { reportId } = useParams<{ reportId: string }>();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    api
      .get(`/reports/${reportId}`)
      .then((res) => setReport(res.data))
      .catch(() => message.error('Report not found'))
      .finally(() => setLoading(false));
  }, [reportId]);

  useEffect(() => {
    if (!report || !iframeRef.current) return;
    const timer = setTimeout(() => {
      try {
        const doc = iframeRef.current?.contentDocument;
        const height = doc?.body?.scrollHeight;
        if (height && iframeRef.current) iframeRef.current.style.height = `${height + 32}px`;
      } catch {
        /* cross-origin guard */
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [report]);

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

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 100 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!report) {
    return (
      <div style={{ textAlign: 'center', paddingTop: 100, color: 'var(--fg-secondary)' }}>
        Report not found
      </div>
    );
  }

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <div
        style={{
          padding: '8px 16px',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'var(--bg-elevated)',
          flexShrink: 0,
        }}
      >
        <Space>
          <Button icon={<ArrowLeftOutlined />} type="text" onClick={() => navigate(-1)} />
          <Typography.Text strong>{report.title}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {new Date(report.created_at).toLocaleString('zh-CN')}
          </Typography.Text>
          <Segmented
            value={report.visibility}
            onChange={async (val) => {
              await api.put(`/reports/${reportId}`, { visibility: val });
              setReport({ ...report, visibility: val as 'private' | 'space' | 'public' });
              const labels: Record<string, string> = {
                private: '已设为不分享',
                space: '已设为空间内可见',
                public: '已设为公开访问',
              };
              message.success(labels[val as string] || '已更新');
            }}
            options={[
              { value: 'private', label: '🔒 不分享' },
              { value: 'space', label: '👥 空间内' },
              { value: 'public', label: '🌐 公开' },
            ]}
          />
        </Space>
        <Space>
          <Button icon={<ShareAltOutlined />} onClick={handleCopyLink}>
            Share
          </Button>
          <Button icon={<DownloadOutlined />} onClick={handleDownload}>
            Download
          </Button>
        </Space>
      </div>

      <iframe
        ref={iframeRef}
        srcDoc={report.html_content}
        title={report.title}
        sandbox="allow-scripts"
        style={{
          flex: 1,
          width: '100%',
          border: 'none',
          minHeight: 'calc(100vh - 49px)',
        }}
      />
    </div>
  );
}
