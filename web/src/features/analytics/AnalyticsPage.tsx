import { useCallback, useEffect, useState } from 'react';
import {
  Card,
  Col,
  Row,
  Statistic,
  Table,
  Spin,
  theme,
  Segmented,
  Button,
  message,
  DatePicker,
  Input,
  Divider,
  Popconfirm,
} from 'antd';
import {
  UserOutlined,
  MessageOutlined,
  BugOutlined,
  AppstoreAddOutlined,
  FilePdfOutlined,
  ThunderboltOutlined,
  HistoryOutlined,
  EyeOutlined,
  DeleteOutlined,
  SendOutlined,
} from '@ant-design/icons';
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import dayjs from 'dayjs';
import api from '@/services/api';

const { RangePicker } = DatePicker;
const { TextArea } = Input;

// ── Types ──

interface OverviewData {
  users: { total: number; active: number; pending: number; invited: number };
  sessions: { total: number; active: number; today: number };
  messages: { total: number };
  spaces: { total: number };
  feedback: { bugs: number; features: number; open_bugs: number };
}

interface TrendPoint {
  day: string;
  registrations: number;
  sessions: number;
  messages: number;
  feedback_bugs: number;
  feedback_features: number;
}

interface TopUser {
  id: string;
  username: string;
  display_name: string;
  total_turns: number;
  session_count: number;
}

interface TopSpace {
  id: string;
  name: string;
  session_count: number;
  last_active: string | null;
}

interface SpaceStat {
  id: string;
  name: string;
  created_at: string | null;
  member_count: number;
  admin_count: number;
  session_count: number;
  last_active: string | null;
}

interface HistoryItem {
  id: string;
  title: string;
  date_range_start: string | null;
  date_range_end: string | null;
  created_at: string;
}

interface GenerateResponse {
  report_id: string;
  html_content: string;
  title: string;
}

// ── Component ──

export default function AnalyticsPage() {
  const { token } = theme.useToken();

  // Analytics state
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [trends, setTrends] = useState<TrendPoint[]>([]);
  const [topUsers, setTopUsers] = useState<TopUser[]>([]);
  const [topSpaces, setTopSpaces] = useState<TopSpace[]>([]);
  const [spaces, setSpaces] = useState<SpaceStat[]>([]);
  const [loading, setLoading] = useState(true);
  const [trendDays, setTrendDays] = useState(30);

  // Report generation state
  const [reportDates, setReportDates] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);
  const [reportHtml, setReportHtml] = useState('');
  const [reportTitle, setReportTitle] = useState('');
  const [feedback, setFeedback] = useState('');
  const [generating, setGenerating] = useState(false);
  const [refining, setRefining] = useState(false);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // ── Analytics data fetching ──

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.get('/admin/analytics/overview'),
      api.get('/admin/analytics/trends', { params: { days: trendDays } }),
      api.get('/admin/analytics/spaces'),
    ])
      .then(([ov, tr, sp]) => {
        setOverview(ov.data);
        setTrends(tr.data.trends || []);
        setTopUsers(tr.data.top_users || []);
        setTopSpaces(tr.data.top_spaces || []);
        setSpaces(sp.data.spaces || []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [trendDays]);

  // ── Report history ──

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await api.get('/admin/analytics/report/history');
      setHistory(res.data || []);
    } catch {
      // silently ignore
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  // ── Report actions ──

  const handleGenerate = async () => {
    if (!reportDates) return;
    setGenerating(true);
    try {
      const res = await api.post<GenerateResponse>('/admin/analytics/report/generate', {
        start_date: reportDates[0].format('YYYY-MM-DD'),
        end_date: reportDates[1].format('YYYY-MM-DD'),
      });
      setReportId(res.data.report_id);
      setReportHtml(res.data.html_content);
      setReportTitle(res.data.title);
      setFeedback('');
      message.success('报告已生成，请在下方预览');
      loadHistory();
    } catch {
      message.error('报告生成失败');
    } finally {
      setGenerating(false);
    }
  };

  const handleRefine = async () => {
    if (!reportId || !feedback.trim()) return;
    setRefining(true);
    try {
      const res = await api.post<GenerateResponse>(`/admin/analytics/report/${reportId}/refine`, {
        feedback: feedback.trim(),
      });
      setReportId(res.data.report_id);
      setReportHtml(res.data.html_content);
      setReportTitle(res.data.title);
      setFeedback('');
      message.success('报告已根据反馈调整');
      loadHistory();
    } catch {
      message.error('调整失败');
    } finally {
      setRefining(false);
    }
  };

  const handleDownloadPdf = async (id?: string) => {
    const targetId = id || reportId;
    if (!targetId) return;
    try {
      const res = await api.post(`/admin/analytics/report/${targetId}/pdf`, null, {
        responseType: 'blob',
      });
      const url = URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `AIOpsOS-analytics-report-${new Date().toISOString().slice(0, 10)}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      message.success('PDF 下载中');
    } catch {
      message.error('PDF 生成失败');
    }
  };

  const handleViewHistory = async (item: HistoryItem) => {
    try {
      const res = await api.get(`/reports/${item.id}`);
      setReportId(item.id);
      setReportHtml(res.data.html_content);
      setReportTitle(res.data.title);
      setReportDates(null);
      setFeedback('');
      message.info(`已加载历史报告: ${item.title}`);
    } catch {
      message.error('加载报告失败');
    }
  };

  const handleDeleteHistory = async (id: string) => {
    try {
      await api.delete(`/reports/${id}`);
      message.success('已删除');
      loadHistory();
      // Clear preview if deleted report was active
      if (id === reportId) {
        setReportId(null);
        setReportHtml('');
        setReportTitle('');
      }
    } catch {
      message.error('删除失败');
    }
  };

  // ── Chart colors ──

  const chartColors = {
    blue: token.colorPrimary,
    green: '#22C55E',
    orange: '#F59E0B',
    red: '#DC2626',
    purple: '#8B5CF6',
  };

  // ── Table columns ──

  const topUserColumns = [
    {
      title: '用户',
      dataIndex: 'display_name',
      key: 'name',
      render: (_: string, r: TopUser) => r.display_name || r.username,
    },
    { title: '总轮次', dataIndex: 'total_turns', key: 'turns' },
    { title: '会话数', dataIndex: 'session_count', key: 'sessions' },
  ];

  const topSpaceColumns = [
    { title: '空间', dataIndex: 'name', key: 'name' },
    { title: '会话数', dataIndex: 'session_count', key: 'sessions' },
    {
      title: '最近活跃',
      dataIndex: 'last_active',
      key: 'active',
      render: (v: string | null) => (v ? new Date(v).toLocaleDateString() : '-'),
    },
  ];

  const spaceColumns = [
    { title: '空间名称', dataIndex: 'name', key: 'name' },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created',
      render: (v: string | null) => (v ? new Date(v).toLocaleDateString() : '-'),
    },
    { title: '成员数', dataIndex: 'member_count', key: 'members' },
    { title: '管理员数', dataIndex: 'admin_count', key: 'admins' },
    { title: '会话数', dataIndex: 'session_count', key: 'sessions' },
    {
      title: '最近活跃',
      dataIndex: 'last_active',
      key: 'active',
      render: (v: string | null) => (v ? new Date(v).toLocaleDateString() : '-'),
    },
  ];

  const historyColumns = [
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: '时间范围',
      key: 'range',
      width: 200,
      render: (_: unknown, r: HistoryItem) =>
        r.date_range_start && r.date_range_end
          ? `${r.date_range_start} ~ ${r.date_range_end}`
          : '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created',
      width: 170,
      render: (v: string) => (v ? new Date(v).toLocaleString() : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: unknown, r: HistoryItem) => (
        <div style={{ display: 'flex', gap: 4 }}>
          <Button size="small" icon={<EyeOutlined />} onClick={() => handleViewHistory(r)}>
            查看
          </Button>
          <Button size="small" icon={<FilePdfOutlined />} onClick={() => handleDownloadPdf(r.id)}>
            PDF
          </Button>
          <Popconfirm title="确定删除此报告？" onConfirm={() => handleDeleteHistory(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </div>
      ),
    },
  ];

  // ── Loading ──

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 100 }}>
        <Spin size="large" />
      </div>
    );
  }

  // ── Render ──

  return (
    <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, fontWeight: 600, margin: '0 0 24px' }}>运营分析</h1>

      {/* ====== Overview Cards ====== */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="用户总数"
              value={overview?.users.total || 0}
              prefix={<UserOutlined />}
            />
            <div style={{ marginTop: 12, fontSize: 12, color: token.colorTextSecondary }}>
              活跃 {overview?.users.active || 0} · 待激活 {overview?.users.pending || 0} · 邀请{' '}
              {overview?.users.invited || 0}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="会话总数"
              value={overview?.sessions.total || 0}
              prefix={<MessageOutlined />}
            />
            <div style={{ marginTop: 12, fontSize: 12, color: token.colorTextSecondary }}>
              活跃 {overview?.sessions.active || 0} · 今日 {overview?.sessions.today || 0}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="空间数量"
              value={overview?.spaces.total || 0}
              prefix={<AppstoreAddOutlined />}
            />
            <div style={{ marginTop: 12, fontSize: 12, color: token.colorTextSecondary }}>
              消息总数 {overview?.messages.total || 0}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="反馈 (Bug)"
              value={overview?.feedback.bugs || 0}
              prefix={<BugOutlined />}
              suffix={
                overview?.feedback.features ? (
                  <span style={{ fontSize: 14, color: token.colorTextSecondary }}>
                    +{overview.feedback.features} 需求
                  </span>
                ) : undefined
              }
            />
            <div style={{ marginTop: 12, fontSize: 12, color: token.colorTextSecondary }}>
              未关闭 Bug {overview?.feedback.open_bugs || 0}
            </div>
          </Card>
        </Col>
      </Row>

      {/* ====== Trend Controls ====== */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 16,
        }}
      >
        <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>趋势数据</h2>
        <Segmented
          options={[
            { label: '7天', value: 7 },
            { label: '30天', value: 30 },
            { label: '90天', value: 90 },
            { label: '365天', value: 365 },
          ]}
          value={trendDays}
          onChange={(v) => setTrendDays(v as number)}
        />
      </div>

      {/* ====== Charts ====== */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={12}>
          <Card title="用户注册 & 会话创建" size="small">
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={trends}>
                <CartesianGrid strokeDasharray="3 3" stroke={token.colorBorderSecondary} />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="registrations"
                  stroke={chartColors.blue}
                  name="注册"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="sessions"
                  stroke={chartColors.green}
                  name="会话"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="消息量趋势" size="small">
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={trends}>
                <CartesianGrid strokeDasharray="3 3" stroke={token.colorBorderSecondary} />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Area
                  type="monotone"
                  dataKey="messages"
                  stroke={chartColors.purple}
                  fill={chartColors.purple}
                  fillOpacity={0.15}
                  name="消息"
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24}>
          <Card title="反馈趋势 (Bug vs Feature)" size="small">
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={trends}>
                <CartesianGrid strokeDasharray="3 3" stroke={token.colorBorderSecondary} />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip />
                <Legend />
                <Bar
                  dataKey="feedback_bugs"
                  fill={chartColors.red}
                  name="Bug"
                  radius={[2, 2, 0, 0]}
                />
                <Bar
                  dataKey="feedback_features"
                  fill={chartColors.orange}
                  name="Feature"
                  radius={[2, 2, 0, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      {/* ====== Ranking Tables ====== */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={12}>
          <Card title="最活跃用户 Top 10" size="small">
            <Table
              dataSource={topUsers}
              columns={topUserColumns}
              rowKey="id"
              size="small"
              pagination={false}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="最活跃空间 Top 10" size="small">
            <Table
              dataSource={topSpaces}
              columns={topSpaceColumns}
              rowKey="id"
              size="small"
              pagination={false}
            />
          </Card>
        </Col>
      </Row>

      <Card title="空间详情" size="small">
        <Table
          dataSource={spaces}
          columns={spaceColumns}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 15, showSizeChanger: false }}
        />
      </Card>

      {/* ====== Report Generation Panel ====== */}
      <Divider style={{ marginTop: 32, marginBottom: 24 }} />
      <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 16px' }}>
        <ThunderboltOutlined style={{ marginRight: 8 }} />
        生成分析报告
      </h2>

      <Card style={{ marginBottom: 24 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            marginBottom: 16,
            flexWrap: 'wrap',
          }}
        >
          <RangePicker
            value={reportDates}
            onChange={(v) => setReportDates(v as [dayjs.Dayjs, dayjs.Dayjs] | null)}
            placeholder={['开始日期', '结束日期']}
            allowClear={false}
          />
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={generating}
            disabled={!reportDates}
            onClick={handleGenerate}
          >
            生成报告
          </Button>
          {reportId && (
            <Button icon={<FilePdfOutlined />} onClick={() => handleDownloadPdf()}>
              下载 PDF
            </Button>
          )}
        </div>

        {/* ── HTML Preview ── */}
        {reportHtml ? (
          <>
            <div
              style={{
                border: `1px solid ${token.colorBorderSecondary}`,
                borderRadius: token.borderRadius,
                overflow: 'hidden',
                marginBottom: 16,
              }}
            >
              <div
                style={{
                  padding: '6px 12px',
                  background: token.colorFillAlter,
                  borderBottom: `1px solid ${token.colorBorderSecondary}`,
                  fontSize: 13,
                  fontWeight: 500,
                }}
              >
                {reportTitle}
              </div>
              <iframe
                srcDoc={reportHtml}
                style={{
                  width: '100%',
                  height: 500,
                  border: 'none',
                }}
                title="报告预览"
              />
            </div>

            {/* ── Feedback / Refine ── */}
            <div style={{ display: 'flex', gap: 8 }}>
              <TextArea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="输入调整需求，例如：请重点分析用户留存、增加空间活跃度对比、简化摘要…"
                rows={2}
                style={{ flex: 1 }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    handleRefine();
                  }
                }}
              />
              <Button
                type="primary"
                icon={<SendOutlined />}
                loading={refining}
                disabled={!feedback.trim()}
                onClick={handleRefine}
              >
                调整优化
              </Button>
            </div>
          </>
        ) : (
          <div
            style={{
              textAlign: 'center',
              padding: 40,
              color: token.colorTextQuaternary,
            }}
          >
            选择时间范围后点击「生成报告」，AI 将分析数据并生成 HTML 报告供预览
          </div>
        )}
      </Card>

      {/* ====== History Table ====== */}
      <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 16px' }}>
        <HistoryOutlined style={{ marginRight: 8 }} />
        历史报告
      </h2>
      <Card size="small">
        <Table
          dataSource={history}
          columns={historyColumns}
          rowKey="id"
          size="small"
          loading={historyLoading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          locale={{ emptyText: '暂无历史报告，生成第一份报告吧' }}
        />
      </Card>
    </div>
  );
}
