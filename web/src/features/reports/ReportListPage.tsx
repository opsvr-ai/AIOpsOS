import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card,
  Typography,
  Empty,
  Segmented,
  Button,
  Space,
  App,
  Popconfirm,
  Input,
  Select,
  Row,
  Col,
  Statistic,
  Tag,
  Tooltip,
} from 'antd';
import {
  FileTextOutlined,
  GlobalOutlined,
  TeamOutlined,
  LockOutlined,
  CopyOutlined,
  MessageOutlined,
  DeleteOutlined,
  PlusOutlined,
  SearchOutlined,
  BarChartOutlined,
  SortAscendingOutlined,
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

const THEME_COLORS: Record<string, string> = {
  ink: '#64748b',
  ops: '#3b82f6',
  security: '#ef4444',
  performance: '#f59e0b',
  incident: '#8b5cf6',
  capacity: '#06b6d4',
  compliance: '#10b981',
};

const VIS_OPTIONS = [
  { value: 'private', label: '不分享' },
  { value: 'space', label: '空间内' },
  { value: 'public', label: '公开' },
];

const VIS_ICON: Record<string, React.ReactNode> = {
  public: <GlobalOutlined style={{ color: '#22c55e' }} />,
  space: <TeamOutlined style={{ color: '#3b82f6' }} />,
  private: <LockOutlined style={{ color: '#ef4444' }} />,
};

const SORT_OPTIONS = [
  { value: 'newest', label: '最新优先' },
  { value: 'oldest', label: '最早优先' },
  { value: 'title', label: '标题 A-Z' },
];

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins} 分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} 小时前`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days} 天前`;
  return new Date(iso).toLocaleDateString('zh-CN');
}

export default function ReportListPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [themeFilter, setThemeFilter] = useState<string | null>(null);
  const [visibilityFilter, setVisibilityFilter] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState('newest');

  const fetchReports = () => {
    setLoading(true);
    api
      .get('/reports?limit=200')
      .then((res) => setReports(res.data ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchReports();
  }, []);

  const themes = useMemo(() => {
    const set = new Set<string>();
    reports.forEach((r) => {
      if (r.theme) set.add(r.theme);
    });
    return Array.from(set).sort();
  }, [reports]);

  const filtered = useMemo(() => {
    let list = [...reports];
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (r) => r.title.toLowerCase().includes(q) || (r.description || '').toLowerCase().includes(q),
      );
    }
    if (themeFilter) list = list.filter((r) => r.theme === themeFilter);
    if (visibilityFilter) list = list.filter((r) => r.visibility === visibilityFilter);
    if (sortBy === 'newest')
      list.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    else if (sortBy === 'oldest')
      list.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    else if (sortBy === 'title') list.sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'));
    return list;
  }, [reports, search, themeFilter, visibilityFilter, sortBy]);

  const stats = useMemo(
    () => ({
      total: reports.length,
      public: reports.filter((r) => r.visibility === 'public').length,
      space: reports.filter((r) => r.visibility === 'space').length,
      private: reports.filter((r) => r.visibility === 'private').length,
    }),
    [reports],
  );

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
    <div style={{ padding: 24, maxWidth: 1040, margin: '0 auto' }}>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 24,
        }}
      >
        <Space align="center">
          <BarChartOutlined style={{ fontSize: 22, color: 'var(--accent)' }} />
          <Typography.Title level={4} style={{ margin: 0 }}>
            分析报告
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {stats.total} 份报告
          </Typography.Text>
        </Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/ops/chat')}>
          生成报告
        </Button>
      </div>

      {/* Stats */}
      <Row gutter={12} style={{ marginBottom: 20 }}>
        <Col xs={12} sm={6}>
          <Card size="small" style={{ borderRadius: 10, textAlign: 'center' }}>
            <Statistic title="总计" value={stats.total} valueStyle={{ fontSize: 24 }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" style={{ borderRadius: 10, textAlign: 'center' }}>
            <Statistic
              title="公开"
              value={stats.public}
              valueStyle={{ fontSize: 24, color: '#22c55e' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" style={{ borderRadius: 10, textAlign: 'center' }}>
            <Statistic
              title="空间内"
              value={stats.space}
              valueStyle={{ fontSize: 24, color: '#3b82f6' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" style={{ borderRadius: 10, textAlign: 'center' }}>
            <Statistic
              title="不分享"
              value={stats.private}
              valueStyle={{ fontSize: 24, color: '#ef4444' }}
            />
          </Card>
        </Col>
      </Row>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
        <Input
          placeholder="搜索报告标题或描述..."
          prefix={<SearchOutlined />}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          allowClear
          style={{ flex: 1, minWidth: 200 }}
        />
        <Select
          placeholder="主题"
          value={themeFilter}
          onChange={setThemeFilter}
          allowClear
          style={{ width: 120 }}
          options={themes.map((t) => ({ value: t, label: t }))}
        />
        <Select
          placeholder="可见性"
          value={visibilityFilter}
          onChange={setVisibilityFilter}
          allowClear
          style={{ width: 110 }}
          options={[
            { value: 'public', label: '公开' },
            { value: 'space', label: '空间内' },
            { value: 'private', label: '不分享' },
          ]}
        />
        <Select
          value={sortBy}
          onChange={setSortBy}
          style={{ width: 130 }}
          options={SORT_OPTIONS}
          suffixIcon={<SortAscendingOutlined />}
        />
      </div>

      {/* Report list */}
      {loading ? (
        <Card style={{ borderRadius: 10, textAlign: 'center', padding: 40 }}>
          <Typography.Text type="secondary">加载中...</Typography.Text>
        </Card>
      ) : filtered.length === 0 ? (
        <Card style={{ borderRadius: 10 }}>
          <Empty
            description={
              search || themeFilter || visibilityFilter ? '没有匹配的报告' : '暂无分析报告'
            }
          >
            {!search && !themeFilter && !visibilityFilter && (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/ops/chat')}>
                在对话中生成第一份报告
              </Button>
            )}
          </Empty>
        </Card>
      ) : (
        filtered.map((item) => (
          <Card
            key={item.id}
            hoverable
            size="small"
            style={{ marginBottom: 12, borderRadius: 10 }}
            onClick={() => navigate(`/ops/reports/${item.id}`)}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <FileTextOutlined style={{ fontSize: 20, color: 'var(--accent)', marginTop: 2 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <Typography.Text strong ellipsis style={{ flex: 1, fontSize: 15 }}>
                    {item.title}
                  </Typography.Text>
                  {item.theme && (
                    <Tag
                      color={THEME_COLORS[item.theme] || '#64748b'}
                      style={{ margin: 0, flexShrink: 0, borderRadius: 6 }}
                    >
                      {item.theme}
                    </Tag>
                  )}
                </div>
                {item.description && (
                  <Typography.Paragraph
                    type="secondary"
                    style={{ margin: '0 0 6px', fontSize: 13 }}
                    ellipsis={{ rows: 1 }}
                  >
                    {item.description}
                  </Typography.Paragraph>
                )}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                  <Tooltip title={new Date(item.created_at).toLocaleString('zh-CN')}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {relativeTime(item.created_at)}
                    </Typography.Text>
                  </Tooltip>
                  <span
                    style={{ fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}
                  >
                    {VIS_ICON[item.visibility]}
                    <Typography.Text type="secondary">
                      {item.visibility === 'public'
                        ? '公开'
                        : item.visibility === 'space'
                          ? '空间内'
                          : '不分享'}
                    </Typography.Text>
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
                <Space size={2}>
                  <Tooltip title="复制链接">
                    <Button
                      size="small"
                      type="text"
                      icon={<CopyOutlined />}
                      onClick={() => handleCopyUrl(item.id)}
                    />
                  </Tooltip>
                  {item.session_id && (
                    <Tooltip title="查看对话">
                      <Button
                        size="small"
                        type="text"
                        icon={<MessageOutlined />}
                        onClick={() => navigate(`/ops/chat?session=${item.session_id}`)}
                      />
                    </Tooltip>
                  )}
                  <Popconfirm
                    title="确定删除此报告？"
                    onConfirm={() => handleDelete(item.id)}
                    okText="删除"
                    cancelText="取消"
                  >
                    <Tooltip title="删除">
                      <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                    </Tooltip>
                  </Popconfirm>
                </Space>
              </div>
            </div>
          </Card>
        ))
      )}
    </div>
  );
}
