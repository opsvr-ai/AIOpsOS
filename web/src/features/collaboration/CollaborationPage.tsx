import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Button,
  Space,
  Typography,
  Tag,
  App,
  Empty,
  theme,
  Select,
  DatePicker,
  Input,
  Drawer,
  Descriptions,
  Timeline,
  List,
  Tabs,
  Progress,
  Tooltip,
  Badge,
  Statistic,
  Row,
  Col,
} from 'antd';
import {
  TeamOutlined,
  SearchOutlined,
  ReloadOutlined,
  EyeOutlined,
  ExportOutlined,
  MessageOutlined,
  BulbOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  SyncOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import api from '@/services/api';
import dayjs from 'dayjs';

interface CollaborationSession {
  id: string;
  scenario_id: string;
  status: 'created' | 'active' | 'resolved' | 'closed';
  trigger_reason: string | null;
  group_chat_id: string | null;
  group_chat_name: string | null;
  progress_summary: Record<string, any> | null;
  created_at: string;
  resolved_at: string | null;
  closed_at: string | null;
  scenario?: {
    id: string;
    name: string;
  };
}

interface CollaborationMessage {
  id: string;
  session_id: string;
  content: string;
  sender_name: string | null;
  sender_id: string | null;
  source_channel: string;
  created_at: string;
}

interface CollaborationRecommendation {
  id: string;
  session_id: string;
  content: string;
  priority: number;
  status: 'pending' | 'adopted' | 'ignored' | 'modified';
  impact_assessment: string | null;
  created_at: string;
}

const statusConfig: Record<string, { color: string; label: string; icon: React.ReactNode }> = {
  created: { color: 'default', label: '已创建', icon: <ClockCircleOutlined /> },
  active: { color: 'processing', label: '进行中', icon: <SyncOutlined spin /> },
  resolved: { color: 'success', label: '已解决', icon: <CheckCircleOutlined /> },
  closed: { color: 'default', label: '已关闭', icon: <CloseCircleOutlined /> },
};

export default function CollaborationPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();
  const [items, setItems] = useState<CollaborationSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [dateRange, setDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>(null);
  
  // Detail drawer state
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedSession, setSelectedSession] = useState<CollaborationSession | null>(null);
  const [messages, setMessages] = useState<CollaborationMessage[]>([]);
  const [recommendations, setRecommendations] = useState<CollaborationRecommendation[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  
  // Statistics
  const [stats, setStats] = useState<Record<string, number>>({});

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = {
        page,
        page_size: pageSize,
      };
      if (statusFilter) params.status = statusFilter;
      if (dateRange?.[0]) params.start_time = dateRange[0].toISOString();
      if (dateRange?.[1]) params.end_time = dateRange[1].toISOString();
      
      const res = await api.get('/collaboration-sessions', { params });
      setItems(res.data.items ?? []);
      setTotal(res.data.total ?? 0);
    } catch {
      msg.error('加载协同会话失败');
    } finally {
      setLoading(false);
    }
  }, [msg, page, pageSize, statusFilter, dateRange]);

  const fetchStats = useCallback(async () => {
    try {
      const res = await api.get('/collaboration-sessions/count');
      setStats(res.data.by_status ?? {});
    } catch {
      // Ignore stats error
    }
  }, []);

  useEffect(() => {
    fetchSessions();
    fetchStats();
  }, [fetchSessions, fetchStats]);

  const fetchSessionDetail = async (session: CollaborationSession) => {
    setSelectedSession(session);
    setDetailOpen(true);
    setDetailLoading(true);
    try {
      const [msgRes, recRes] = await Promise.all([
        api.get(`/collaboration-sessions/${session.id}/messages`),
        api.get(`/collaboration-sessions/${session.id}/recommendations`),
      ]);
      setMessages(msgRes.data.items ?? []);
      setRecommendations(recRes.data.items ?? []);
    } catch {
      msg.error('加载详情失败');
    } finally {
      setDetailLoading(false);
    }
  };

  const handleExport = async (sessionId: string) => {
    try {
      const res = await api.get(`/collaboration-sessions/${sessionId}/report`);
      // Create a downloadable JSON file
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `collaboration-report-${sessionId}.json`;
      a.click();
      URL.revokeObjectURL(url);
      msg.success('导出成功');
    } catch {
      msg.error('导出失败');
    }
  };

  const handleTriggerAnalysis = async (sessionId: string) => {
    try {
      await api.post(`/collaboration-sessions/${sessionId}/analyze`);
      msg.success('分析已触发');
      fetchSessions();
    } catch {
      msg.error('触发分析失败');
    }
  };

  const columns = [
    {
      title: '会话',
      key: 'session',
      width: 280,
      render: (_: unknown, r: CollaborationSession) => (
        <Space direction="vertical" size={0}>
          <Space>
            <TeamOutlined style={{ color: token.colorPrimary }} />
            <span style={{ fontWeight: 500 }}>{r.group_chat_name || `会话 ${r.id.slice(0, 8)}`}</span>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {r.trigger_reason?.slice(0, 50) || '无触发原因'}
            {(r.trigger_reason?.length ?? 0) > 50 ? '...' : ''}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => {
        const config = statusConfig[v] || statusConfig.created;
        return (
          <Tag color={config.color} icon={config.icon} style={{ borderRadius: 4 }}>
            {config.label}
          </Tag>
        );
      },
    },
    {
      title: '进度',
      key: 'progress',
      width: 200,
      render: (_: unknown, r: CollaborationSession) => {
        const progress = r.progress_summary;
        if (!progress) return <Typography.Text type="secondary">-</Typography.Text>;
        const phase = progress.current_phase || '未知';
        const completedSteps = progress.completed_steps?.length || 0;
        const pendingItems = progress.pending_items?.length || 0;
        return (
          <Space direction="vertical" size={0}>
            <Tag color="blue">{phase}</Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已完成 {completedSteps} 步 / 待处理 {pendingItems} 项
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm'),
    },
    {
      title: '持续时间',
      key: 'duration',
      width: 100,
      render: (_: unknown, r: CollaborationSession) => {
        const start = dayjs(r.created_at);
        const end = r.closed_at ? dayjs(r.closed_at) : r.resolved_at ? dayjs(r.resolved_at) : dayjs();
        const minutes = end.diff(start, 'minute');
        if (minutes < 60) return `${minutes} 分钟`;
        const hours = Math.floor(minutes / 60);
        const mins = minutes % 60;
        return `${hours}h ${mins}m`;
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: unknown, r: CollaborationSession) => (
        <Space>
          <Tooltip title="查看详情">
            <Button
              type="text"
              icon={<EyeOutlined />}
              size="small"
              onClick={() => fetchSessionDetail(r)}
            />
          </Tooltip>
          <Tooltip title="触发分析">
            <Button
              type="text"
              icon={<BulbOutlined />}
              size="small"
              onClick={() => handleTriggerAnalysis(r.id)}
              disabled={r.status === 'closed'}
            />
          </Tooltip>
          <Tooltip title="导出报告">
            <Button
              type="text"
              icon={<ExportOutlined />}
              size="small"
              onClick={() => handleExport(r.id)}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 20,
        }}
      >
        <Typography.Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          应急协同
        </Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={() => { fetchSessions(); fetchStats(); }}>
          刷新
        </Button>
      </div>

      {/* Statistics Cards */}
      <Row gutter={16} style={{ marginBottom: 20 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="进行中"
              value={stats.active || 0}
              prefix={<Badge status="processing" />}
              valueStyle={{ color: token.colorPrimary }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="已创建"
              value={stats.created || 0}
              prefix={<Badge status="default" />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="已解决"
              value={stats.resolved || 0}
              prefix={<Badge status="success" />}
              valueStyle={{ color: token.colorSuccess }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="已关闭"
              value={stats.closed || 0}
              prefix={<Badge status="default" />}
            />
          </Card>
        </Col>
      </Row>

      {/* Filters */}
      <Card size="small" style={{ marginBottom: 16, borderRadius: 8 }}>
        <Space wrap>
          <Select
            placeholder="状态筛选"
            allowClear
            style={{ width: 120 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: 'created', label: '已创建' },
              { value: 'active', label: '进行中' },
              { value: 'resolved', label: '已解决' },
              { value: 'closed', label: '已关闭' },
            ]}
          />
          <DatePicker.RangePicker
            value={dateRange}
            onChange={(v) => setDateRange(v)}
            placeholder={['开始时间', '结束时间']}
          />
          <Input.Search
            placeholder="搜索消息内容"
            allowClear
            style={{ width: 200 }}
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)}
            onSearch={async (v) => {
              if (!v) return;
              try {
                const res = await api.get('/collaboration-sessions/search/messages', {
                  params: { keyword: v },
                });
                msg.info(`找到 ${res.data.total} 条匹配消息`);
              } catch {
                msg.error('搜索失败');
              }
            }}
          />
        </Space>
      </Card>

      {/* Table */}
      <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={items}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          size="middle"
          locale={{ emptyText: <Empty description="暂无协同会话" /> }}
        />
      </Card>

      {/* Detail Drawer */}
      <Drawer
        title={
          <Space>
            <TeamOutlined />
            {selectedSession?.group_chat_name || '协同会话详情'}
          </Space>
        }
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={720}
      >
        {selectedSession && (
          <div>
            <Descriptions column={2} size="small" style={{ marginBottom: 24 }}>
              <Descriptions.Item label="状态">
                <Tag color={statusConfig[selectedSession.status]?.color}>
                  {statusConfig[selectedSession.status]?.label}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {dayjs(selectedSession.created_at).format('YYYY-MM-DD HH:mm:ss')}
              </Descriptions.Item>
              <Descriptions.Item label="触发原因" span={2}>
                {selectedSession.trigger_reason || '-'}
              </Descriptions.Item>
              {selectedSession.resolved_at && (
                <Descriptions.Item label="解决时间">
                  {dayjs(selectedSession.resolved_at).format('YYYY-MM-DD HH:mm:ss')}
                </Descriptions.Item>
              )}
              {selectedSession.closed_at && (
                <Descriptions.Item label="关闭时间">
                  {dayjs(selectedSession.closed_at).format('YYYY-MM-DD HH:mm:ss')}
                </Descriptions.Item>
              )}
            </Descriptions>

            {/* Progress Summary */}
            {selectedSession.progress_summary && (
              <Card size="small" title="进度摘要" style={{ marginBottom: 16 }}>
                <Space direction="vertical" style={{ width: '100%' }}>
                  <div>
                    <Typography.Text strong>当前阶段：</Typography.Text>
                    <Tag color="blue" style={{ marginLeft: 8 }}>
                      {selectedSession.progress_summary.current_phase || '未知'}
                    </Tag>
                  </div>
                  {selectedSession.progress_summary.completed_steps?.length > 0 && (
                    <div>
                      <Typography.Text strong>已完成步骤：</Typography.Text>
                      <ul style={{ margin: '8px 0', paddingLeft: 20 }}>
                        {selectedSession.progress_summary.completed_steps.map((step: string, i: number) => (
                          <li key={i}>{step}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {selectedSession.progress_summary.pending_items?.length > 0 && (
                    <div>
                      <Typography.Text strong>待处理事项：</Typography.Text>
                      <ul style={{ margin: '8px 0', paddingLeft: 20 }}>
                        {selectedSession.progress_summary.pending_items.map((item: string, i: number) => (
                          <li key={i}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </Space>
              </Card>
            )}

            <Tabs
              items={[
                {
                  key: 'messages',
                  label: (
                    <Space>
                      <MessageOutlined />
                      消息记录 ({messages.length})
                    </Space>
                  ),
                  children: (
                    <List
                      loading={detailLoading}
                      dataSource={messages}
                      renderItem={(item) => (
                        <List.Item>
                          <List.Item.Meta
                            title={
                              <Space>
                                <Typography.Text strong>
                                  {item.sender_name || '系统'}
                                </Typography.Text>
                                <Tag size="small">{item.source_channel}</Tag>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                  {dayjs(item.created_at).format('HH:mm:ss')}
                                </Typography.Text>
                              </Space>
                            }
                            description={item.content}
                          />
                        </List.Item>
                      )}
                      locale={{ emptyText: '暂无消息' }}
                    />
                  ),
                },
                {
                  key: 'recommendations',
                  label: (
                    <Space>
                      <BulbOutlined />
                      建议 ({recommendations.length})
                    </Space>
                  ),
                  children: (
                    <List
                      loading={detailLoading}
                      dataSource={recommendations}
                      renderItem={(item) => (
                        <List.Item
                          actions={[
                            <Tag
                              key="status"
                              color={
                                item.status === 'adopted'
                                  ? 'success'
                                  : item.status === 'ignored'
                                  ? 'default'
                                  : 'processing'
                              }
                            >
                              {item.status === 'adopted'
                                ? '已采纳'
                                : item.status === 'ignored'
                                ? '已忽略'
                                : item.status === 'modified'
                                ? '已修改'
                                : '待处理'}
                            </Tag>,
                          ]}
                        >
                          <List.Item.Meta
                            title={
                              <Space>
                                <Tag color={item.priority >= 3 ? 'red' : item.priority >= 2 ? 'orange' : 'blue'}>
                                  P{item.priority}
                                </Tag>
                                <Typography.Text>{item.content}</Typography.Text>
                              </Space>
                            }
                            description={item.impact_assessment}
                          />
                        </List.Item>
                      )}
                      locale={{ emptyText: '暂无建议' }}
                    />
                  ),
                },
              ]}
            />
          </div>
        )}
      </Drawer>
    </div>
  );
}
