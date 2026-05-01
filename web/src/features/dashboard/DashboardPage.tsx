import React, { useEffect, useState } from 'react';
import {
  Card,
  Col,
  Row,
  Statistic,
  Typography,
  Table,
  Tag,
  Spin,
  theme,
  Timeline,
  Button,
} from 'antd';
import {
  AlertOutlined,
  MessageOutlined,
  BookOutlined,
  RobotOutlined,
  ClockCircleOutlined,
  SendOutlined,
  HeartOutlined,
  ThunderboltOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import api from '@/services/api';

interface AlertItem {
  id: string;
  title: string;
  severity: string;
  status: string;
  created_at: string;
  source: string;
}

export default function DashboardPage() {
  const { token } = theme.useToken();
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  const [stats, setStats] = useState({
    alerts: 0,
    sessions: 0,
    knowledge: 0,
    agents: 0,
    onlineAgents: 0,
    cronJobs: 0,
    channels: 0,
    health: 100,
  });
  const [recentAlerts, setRecentAlerts] = useState<AlertItem[]>([]);

  useEffect(() => {
    Promise.all([
      api.get('/alerts?page=1&page_size=5'),
      api.get('/sessions'),
      api.get('/knowledge/documents'),
      api.get('/agents'),
      api.get('/agent-profiles'),
      api.get('/cron/jobs'),
      api.get('/channels'),
    ])
      .then(
        ([alertsRes, sessionsRes, knowledgeRes, agentsRes, profilesRes, cronRes, channelsRes]) => {
          const allAlerts: AlertItem[] = alertsRes.data ?? [];
          const allSessions = sessionsRes.data ?? [];
          const allKnowledge = knowledgeRes.data ?? [];
          const allAgents: any[] = agentsRes.data ?? [];
          const allProfiles: any[] = profilesRes.data ?? [];
          const allCron: any[] = cronRes.data ?? [];
          const allChannels: any[] = channelsRes.data ?? [];

          setStats({
            alerts: allAlerts.length,
            sessions: allSessions.length,
            knowledge: allKnowledge.length,
            agents: allAgents.length,
            onlineAgents: allProfiles.filter((p: any) => p.online).length,
            cronJobs: allCron.length,
            channels: allChannels.length,
            health: 100,
          });
          setRecentAlerts(allAlerts.slice(0, 5));
        },
      )
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 100 }}>
        <Spin size="large" />
      </div>
    );
  }

  const severityColor = (sev: string) => {
    switch (sev) {
      case 'critical':
        return '#DC2626';
      case 'warning':
        return '#F59E0B';
      case 'info':
        return '#3B82F6';
      default:
        return token.colorTextTertiary;
    }
  };

  const alertColumns = [
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: '级别',
      dataIndex: 'severity',
      key: 'severity',
      width: 80,
      render: (v: string) => (
        <Tag color={severityColor(v)} style={{ borderRadius: 4 }}>
          {v}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (v: string) => (
        <Tag color={v === 'active' ? 'processing' : 'default'} style={{ borderRadius: 4 }}>
          {v === 'active' ? '进行中' : v}
        </Tag>
      ),
    },
    { title: '来源', dataIndex: 'source', key: 'source', width: 120 },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
  ];

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 24, fontWeight: 600 }}>
        运维总览
      </Typography.Title>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        {[
          {
            span: 6,
            label: '告警总数',
            value: stats.alerts,
            icon: <AlertOutlined />,
            color: '#DC2626',
          },
          {
            span: 6,
            label: '活跃会话',
            value: stats.sessions,
            icon: <MessageOutlined />,
            color: '#3B82F6',
          },
          {
            span: 6,
            label: '知识条目',
            value: stats.knowledge,
            icon: <BookOutlined />,
            color: '#059669',
          },
          {
            span: 6,
            label: '智能体数量',
            value: stats.agents,
            icon: <RobotOutlined />,
            color: '#7C3AED',
          },
          {
            span: 6,
            label: '在线 Agent',
            value: stats.onlineAgents,
            icon: <ThunderboltOutlined />,
            color: '#0891B2',
          },
          {
            span: 6,
            label: '定时任务',
            value: stats.cronJobs,
            icon: <ClockCircleOutlined />,
            color: '#D97706',
          },
          {
            span: 6,
            label: '通知渠道',
            value: stats.channels,
            icon: <SendOutlined />,
            color: '#059669',
          },
          {
            span: 6,
            label: '健康评分',
            value: stats.health,
            suffix: '%',
            icon: <HeartOutlined />,
            color: '#DC2626',
          },
        ].map((s) => (
          <Col xs={12} sm={12} md={6} key={s.label}>
            <Card style={{ borderRadius: 12 }} hoverable size="small">
              <Statistic
                title={
                  <span style={{ fontSize: 12, color: token.colorTextSecondary }}>{s.label}</span>
                }
                value={s.value}
                suffix={s.suffix as any}
                prefix={React.cloneElement(s.icon as any, {
                  style: { color: s.color, fontSize: 18 },
                })}
                valueStyle={{ color: token.colorText, fontWeight: 600, fontSize: 24 }}
              />
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={16}>
        <Col xs={24} lg={16}>
          <Card
            title={<span style={{ fontSize: 14, fontWeight: 600 }}>最新告警</span>}
            style={{ borderRadius: 12, marginBottom: 16 }}
            styles={{ body: { padding: 0 } }}
          >
            <Table
              dataSource={recentAlerts}
              columns={alertColumns}
              rowKey="id"
              pagination={false}
              size="small"
            />
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          <Card
            title={<span style={{ fontSize: 14, fontWeight: 600 }}>最近活动</span>}
            style={{ borderRadius: 12, marginBottom: 16 }}
          >
            <Timeline
              items={[
                { children: '系统启动完成', color: 'green' },
                ...recentAlerts.slice(0, 3).map((a) => ({
                  children: `告警: ${a.title}`,
                  color:
                    a.severity === 'critical'
                      ? 'red'
                      : a.severity === 'warning'
                        ? 'orange'
                        : 'blue',
                })),
                { children: `共 ${stats.agents} 个智能体在线`, color: 'blue' },
              ]}
            />
          </Card>

          <Card
            title={<span style={{ fontSize: 14, fontWeight: 600 }}>快速入口</span>}
            style={{ borderRadius: 12 }}
            size="small"
          >
            <Row gutter={[8, 8]}>
              {[
                { label: '新对话', path: '/ops/chat', icon: <MessageOutlined /> },
                { label: '告警中心', path: '/ops/alerts', icon: <AlertOutlined /> },
                { label: '知识库', path: '/ai/knowledge', icon: <BookOutlined /> },
                { label: '智能体', path: '/ai/agents', icon: <RobotOutlined /> },
              ].map((q) => (
                <Col span={12} key={q.path}>
                  <Button
                    block
                    icon={q.icon}
                    onClick={() => navigate(q.path)}
                    style={{ borderRadius: 8, height: 40 }}
                  >
                    {q.label}
                    <ArrowRightOutlined style={{ marginLeft: 'auto', fontSize: 10 }} />
                  </Button>
                </Col>
              ))}
            </Row>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
