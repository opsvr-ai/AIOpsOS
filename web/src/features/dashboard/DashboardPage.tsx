import { useEffect, useState } from 'react';
import { Card, Col, Row, Statistic, Typography, Table, Tag, Spin, theme } from 'antd';
import { AlertOutlined, MessageOutlined, BookOutlined, RobotOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface AlertItem {
  id: string;
  title: string;
  severity: string;
  status: string;
  created_at: string;
  source: string;
}

interface AgentItem {
  id: string;
  name: string;
  type: string;
  is_active: boolean;
}

export default function DashboardPage() {
  const { token } = theme.useToken();
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState({ alerts: 0, sessions: 0, knowledge: 0, agents: 0 });
  const [recentAlerts, setRecentAlerts] = useState<AlertItem[]>([]);
  const [agents, setAgents] = useState<AgentItem[]>([]);

  useEffect(() => {
    Promise.all([
      api.get('/alerts?page=1&page_size=5'),
      api.get('/sessions'),
      api.get('/knowledge/documents'),
      api.get('/agents'),
    ])
      .then(([alertsRes, sessionsRes, knowledgeRes, agentsRes]) => {
        const allAlerts: AlertItem[] = alertsRes.data ?? [];
        const allSessions = sessionsRes.data ?? [];
        const allKnowledge = knowledgeRes.data ?? [];
        const allAgents: AgentItem[] = agentsRes.data ?? [];

        setStats({
          alerts: allAlerts.length,
          sessions: allSessions.length,
          knowledge: allKnowledge.length,
          agents: allAgents.length,
        });
        setRecentAlerts(allAlerts.slice(0, 5));
        setAgents(allAgents);
      })
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

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card style={{ borderRadius: 12 }} hoverable>
            <Statistic
              title={
                <span style={{ fontSize: 13, color: token.colorTextSecondary }}>告警总数</span>
              }
              value={stats.alerts}
              prefix={<AlertOutlined style={{ color: '#DC2626', fontSize: 20 }} />}
              valueStyle={{ color: token.colorText, fontWeight: 600, fontSize: 28 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card style={{ borderRadius: 12 }} hoverable>
            <Statistic
              title={
                <span style={{ fontSize: 13, color: token.colorTextSecondary }}>活跃会话</span>
              }
              value={stats.sessions}
              prefix={<MessageOutlined style={{ color: '#3B82F6', fontSize: 20 }} />}
              valueStyle={{ color: token.colorText, fontWeight: 600, fontSize: 28 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card style={{ borderRadius: 12 }} hoverable>
            <Statistic
              title={
                <span style={{ fontSize: 13, color: token.colorTextSecondary }}>知识条目</span>
              }
              value={stats.knowledge}
              prefix={<BookOutlined style={{ color: '#059669', fontSize: 20 }} />}
              valueStyle={{ color: token.colorText, fontWeight: 600, fontSize: 28 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card style={{ borderRadius: 12 }} hoverable>
            <Statistic
              title={<span style={{ fontSize: 13, color: token.colorTextSecondary }}>智能体</span>}
              value={stats.agents}
              prefix={<RobotOutlined style={{ color: '#7C3AED', fontSize: 20 }} />}
              valueStyle={{ color: token.colorText, fontWeight: 600, fontSize: 28 }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={16}>
          <Card
            title={<span style={{ fontSize: 14, fontWeight: 600 }}>最新告警</span>}
            style={{ borderRadius: 12 }}
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

        <Col span={8}>
          <Card
            title={<span style={{ fontSize: 14, fontWeight: 600 }}>智能体状态</span>}
            style={{ borderRadius: 12 }}
            styles={{ body: { padding: 0 } }}
          >
            {agents.length === 0 ? (
              <div
                style={{
                  padding: 40,
                  textAlign: 'center',
                  color: token.colorTextTertiary,
                  fontSize: 13,
                }}
              >
                暂无智能体
              </div>
            ) : (
              agents.map((a) => (
                <div
                  key={a.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '12px 16px',
                    borderBottom: `1px solid ${token.colorBorderSecondary}`,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <RobotOutlined style={{ color: token.colorPrimary, fontSize: 16 }} />
                    <span style={{ fontSize: 13, color: token.colorText }}>{a.name}</span>
                  </div>
                  <Tag
                    color={a.is_active ? 'success' : 'default'}
                    style={{ borderRadius: 4, fontSize: 11 }}
                  >
                    {a.is_active ? '在线' : '离线'}
                  </Tag>
                </div>
              ))
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
