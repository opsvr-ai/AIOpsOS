import { useEffect, useState, useCallback } from 'react';
import {
  Typography, Table, Switch, Button, Tag, Card, Statistic, Input,
  Skeleton, Empty, theme, App, Row, Col, Tooltip, Space, Popconfirm,
} from 'antd';
import {
  MoonOutlined, SunOutlined, CheckCircleOutlined, ClockCircleOutlined,
  ReloadOutlined, SearchOutlined, ArrowRightOutlined,
  ThunderboltOutlined, CoffeeOutlined, MessageOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import api from '@/services/api';

const { Title, Text } = Typography;

interface SleepSession {
  id: string;
  title: string;
  status: string;
  sleep_status: 'awake' | 'sleeping';
  memory_status: 'consolidated' | 'unconsolidated';
  auto_consolidate: boolean;
  last_active_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface SleepStats {
  total: number;
  sleeping: number;
  unconsolidated: number;
}

function formatRelative(dateStr: string | null): string {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  const now = Date.now();
  const diff = now - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return d.toLocaleDateString('zh-CN');
}

export default function SleepManagementPage() {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const navigate = useNavigate();

  const [sessions, setSessions] = useState<SleepSession[]>([]);
  const [stats, setStats] = useState<SleepStats>({ total: 0, sleeping: 0, unconsolidated: 0 });
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [toggling, setToggling] = useState<Set<string>>(new Set());
  const [consolidating, setConsolidating] = useState<Set<string>>(new Set());

  const fetchData = useCallback(async () => {
    try {
      const [sessRes, statsRes] = await Promise.all([
        api.get('/sleep-management/sessions'),
        api.get('/sleep-management/stats'),
      ]);
      setSessions(sessRes.data ?? []);
      setStats(statsRes.data ?? { total: 0, sleeping: 0, unconsolidated: 0 });
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleToggle = async (sessionId: string) => {
    setToggling((prev) => new Set(prev).add(sessionId));
    try {
      const res = await api.post(`/sleep-management/sessions/${sessionId}/toggle`);
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId ? { ...s, auto_consolidate: res.data.auto_consolidate } : s,
        ),
      );
    } catch {
      message.error('操作失败');
    } finally {
      setToggling((prev) => {
        const next = new Set(prev);
        next.delete(sessionId);
        return next;
      });
    }
  };

  const handleConsolidate = async (sessionId: string) => {
    setConsolidating((prev) => new Set(prev).add(sessionId));
    try {
      await api.post(`/sleep-management/sessions/${sessionId}/consolidate`);
      message.success('记忆整理完成');
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId ? { ...s, memory_status: 'consolidated' } : s,
        ),
      );
      setStats((prev) => ({
        ...prev,
        unconsolidated: Math.max(0, prev.unconsolidated - 1),
      }));
    } catch {
      message.error('整理失败');
    } finally {
      setConsolidating((prev) => {
        const next = new Set(prev);
        next.delete(sessionId);
        return next;
      });
    }
  };

  const handleWake = async (sessionId: string) => {
    try {
      await api.post(`/sleep-management/sessions/${sessionId}/wake`);
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId ? { ...s, sleep_status: 'awake' } : s,
        ),
      );
      setStats((prev) => ({
        ...prev,
        sleeping: Math.max(0, prev.sleeping - 1),
      }));
      message.success('已唤醒');
    } catch {
      message.error('唤醒失败');
    }
  };

  const filtered = search.trim()
    ? sessions.filter((s) => (s.title || '').toLowerCase().includes(search.toLowerCase()))
    : sessions;

  const columns = [
    {
      title: '会话标题',
      dataIndex: 'title',
      key: 'title',
      width: 260,
      ellipsis: true,
      render: (title: string, record: SleepSession) => (
        <a
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/ops/chat?session=${record.id}`);
          }}
          style={{
            fontWeight: 500,
            fontSize: 13,
            color: token.colorText,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {title || '新对话'}
          </span>
          <ArrowRightOutlined style={{ fontSize: 11, opacity: 0.3, flexShrink: 0 }} />
        </a>
      ),
    },
    {
      title: '睡眠状态',
      dataIndex: 'sleep_status',
      key: 'sleep_status',
      width: 110,
      render: (status: string) => {
        const awake = status === 'awake';
        return (
          <Tag
            color={awake ? 'green' : 'blue'}
            icon={awake ? <SunOutlined /> : <MoonOutlined />}
            style={{ borderRadius: 6, fontSize: 12, margin: 0, padding: '2px 10px' }}
          >
            {awake ? '清醒' : '睡眠中'}
          </Tag>
        );
      },
    },
    {
      title: '记忆整理',
      dataIndex: 'memory_status',
      key: 'memory_status',
      width: 110,
      render: (status: string) => {
        const done = status === 'consolidated';
        return (
          <Tag
            color={done ? 'green' : 'orange'}
            icon={done ? <CheckCircleOutlined /> : <ClockCircleOutlined />}
            style={{ borderRadius: 6, fontSize: 12, margin: 0, padding: '2px 10px' }}
          >
            {done ? '已整理' : '待整理'}
          </Tag>
        );
      },
    },
    {
      title: '自动整理',
      dataIndex: 'auto_consolidate',
      key: 'auto_consolidate',
      width: 90,
      align: 'center' as const,
      render: (enabled: boolean, record: SleepSession) => (
        <Tooltip title={enabled ? '关闭自动整理' : '开启自动整理'}>
          <span onClick={(e) => e.stopPropagation()}>
            <Switch
              size="small"
              checked={enabled}
              loading={toggling.has(record.id)}
              onChange={() => handleToggle(record.id)}
            />
          </span>
        </Tooltip>
      ),
    },
    {
      title: '最后活跃',
      dataIndex: 'last_active_at',
      key: 'last_active_at',
      width: 120,
      render: (val: string | null) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {formatRelative(val)}
        </Text>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_: unknown, record: SleepSession) => (
        <Space size={4} onClick={(e) => e.stopPropagation()}>
          {record.memory_status === 'unconsolidated' && (
            <Popconfirm
              title="手动整理该会话记忆？"
              onConfirm={() => handleConsolidate(record.id)}
              okText="整理"
              cancelText="取消"
            >
              <Button
                type="link"
                size="small"
                icon={<ThunderboltOutlined />}
                loading={consolidating.has(record.id)}
                style={{ fontSize: 12, padding: '0 4px' }}
              >
                整理
              </Button>
            </Popconfirm>
          )}
          {record.sleep_status === 'sleeping' && (
            <Button
              type="link"
              size="small"
              icon={<CoffeeOutlined />}
              onClick={() => handleWake(record.id)}
              style={{ fontSize: 12, padding: '0 4px' }}
            >
              唤醒
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={{ height: '100%', overflow: 'auto', padding: 24 }}>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 24,
        }}
      >
        <div>
          <Title level={4} style={{ margin: 0, color: token.colorText }}>
            睡眠管理
          </Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            管理会话睡眠状态和记忆自动整理
          </Text>
        </div>
        <Button
          icon={<ReloadOutlined />}
          onClick={fetchData}
          loading={loading}
          style={{ borderRadius: 8 }}
        >
          刷新
        </Button>
      </div>

      {/* Stats Cards */}
      {loading ? (
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          {[1, 2, 3].map((i) => (
            <Col xs={24} sm={8} key={i}>
              <Card style={{ borderRadius: 12 }}>
                <Skeleton active paragraph={{ rows: 1 }} title={{ width: '40%' }} />
              </Card>
            </Col>
          ))}
        </Row>
      ) : (
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={24} sm={8}>
            <Card
              style={{
                borderRadius: 12,
                border: `1px solid ${token.colorBorder}`,
                boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
              }}
            >
              <Statistic
                title="总会话"
                value={stats.total}
                prefix={<MessageOutlined style={{ fontSize: 20, color: token.colorPrimary }} />}
                valueStyle={{ fontSize: 28, fontWeight: 700 }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card
              style={{
                borderRadius: 12,
                border: `1px solid ${token.colorBorder}`,
                boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
              }}
            >
              <Statistic
                title="睡眠中"
                value={stats.sleeping}
                prefix={<MoonOutlined style={{ fontSize: 20, color: token.colorInfo }} />}
                valueStyle={{ fontSize: 28, fontWeight: 700 }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card
              style={{
                borderRadius: 12,
                border: `1px solid ${token.colorBorder}`,
                boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
              }}
            >
              <Statistic
                title="待整理"
                value={stats.unconsolidated}
                prefix={<ClockCircleOutlined style={{ fontSize: 20, color: token.colorWarning }} />}
                valueStyle={{ fontSize: 28, fontWeight: 700 }}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* Search */}
      <div style={{ marginBottom: 16 }}>
        <Input
          prefix={<SearchOutlined style={{ color: token.colorTextTertiary }} />}
          placeholder="搜索会话..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          allowClear
          style={{ borderRadius: 10, height: 40, fontSize: 13, maxWidth: 320 }}
        />
      </div>

      {/* Sessions Table */}
      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {[1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} active paragraph={{ rows: 1 }} title={{ width: '60%' }} />
          ))}
        </div>
      ) : (
        <Table<SleepSession>
          dataSource={filtered}
          columns={columns}
          rowKey="id"
          size="middle"
          locale={{
            emptyText: (
              <Empty
                description={
                  <Text type="secondary" style={{ fontSize: 13 }}>
                    暂无会话记录。开始对话后，会话将在此管理。
                  </Text>
                }
              />
            ),
          }}
          pagination={{
            pageSize: 20,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 个会话`,
            style: { marginTop: 16 },
          }}
          style={{
            background: token.colorBgContainer,
          }}
          onRow={(record) => ({
            style: { cursor: 'pointer' },
            onClick: () => navigate(`/ops/chat?session=${record.id}`),
          })}
        />
      )}
    </div>
  );
}
