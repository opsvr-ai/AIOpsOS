import { useCallback, useEffect, useState } from 'react';
import {
  Card,
  Table,
  Button,
  Space,
  Tag,
  Badge,
  Drawer,
  Modal,
  Input,
  Select,
  Radio,
  App,
  Empty,
  Spin,
  Typography,
  Descriptions,
  theme,
  Row,
  Col,
} from 'antd';
import {
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  EditOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

const { Text } = Typography;
const { TextArea } = Input;

interface AgentProfileData {
  id: string;
  name: string;
  profile_version: number;
  skills: Record<string, any>;
  collection: Record<string, any>;
  rules: Record<string, any>;
  model_config: Record<string, any>;
  resources: Record<string, any>;
  update_policy: Record<string, any>;
  online: boolean;
  last_heartbeat: string | null;
  agent_version: string | null;
  connected_agent_id: string | null;
  hostname: string | null;
  ip_address: string | null;
  os_info: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface TaskEntry {
  task_id: string;
  type: string;
  content: string;
  status: string;
  output: string | null;
  created_at: string;
}

interface MetricEntry {
  cpu_percent: number | null;
  memory_percent: number | null;
  disk_percent: number | null;
  network_rx_bytes: number | null;
  network_tx_bytes: number | null;
  recorded_at: string | null;
}

const STATUS_OPTIONS = [
  { value: 'all', label: '全部状态' },
  { value: 'online', label: '在线' },
  { value: 'offline', label: '离线' },
];

const DISPATCH_TYPES = [
  { value: 'shell', label: 'Shell 命令' },
  { value: 'nl', label: '自然语言' },
  { value: 'script', label: '脚本' },
];

const JSON_EDITOR_FIELDS = [
  'skills',
  'collection',
  'rules',
  'model_config',
  'resources',
  'update_policy',
];

export default function AgentProfileList() {
  const { token } = theme.useToken();
  const { message: msg } = App.useApp();

  const [profiles, setProfiles] = useState<AgentProfileData[]>([]);
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState<string>('all');

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingProfile, setEditingProfile] = useState<AgentProfileData | null>(null);
  const [formName, setFormName] = useState('');
  const [formJsonFields, setFormJsonFields] = useState<Record<string, string>>({});
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const [dispatchOpen, setDispatchOpen] = useState(false);
  const [dispatchTarget, setDispatchTarget] = useState<string>('');
  const [dispatchType, setDispatchType] = useState<string>('shell');
  const [dispatchContent, setDispatchContent] = useState('');
  const [dispatching, setDispatching] = useState(false);
  const [dispatchResult, setDispatchResult] = useState<{ task_id: string; status: string } | null>(
    null,
  );

  const [detailOpen, setDetailOpen] = useState(false);
  const [detailProfile, setDetailProfile] = useState<AgentProfileData | null>(null);
  const [detailTasks, setDetailTasks] = useState<TaskEntry[]>([]);
  const [detailMetrics, setDetailMetrics] = useState<MetricEntry[]>([]);

  const fetchProfiles = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/agent-profiles');
      setProfiles(res.data ?? []);
    } catch {
      msg.error('加载客户端列表失败');
    } finally {
      setLoading(false);
    }
  }, [msg]);

  useEffect(() => {
    fetchProfiles();
  }, [fetchProfiles]);

  const filtered =
    filterStatus === 'all'
      ? profiles
      : profiles.filter((p) => (filterStatus === 'online' ? p.online : !p.online));

  const onlineCount = profiles.filter((p) => p.online).length;

  const openCreate = () => {
    setEditingProfile(null);
    setFormName('');
    const defaults: Record<string, string> = {};
    JSON_EDITOR_FIELDS.forEach((f) => {
      defaults[f] = '{}';
    });
    setFormJsonFields(defaults);
    setJsonErrors({});
    setDrawerOpen(true);
  };

  const openEdit = (profile: AgentProfileData) => {
    setEditingProfile(profile);
    setFormName(profile.name);
    const fields: Record<string, string> = {};
    JSON_EDITOR_FIELDS.forEach((f) => {
      fields[f] = JSON.stringify((profile as any)[f] || {}, null, 2);
    });
    setFormJsonFields(fields);
    setJsonErrors({});
    setDrawerOpen(true);
  };

  const handleJsonChange = (field: string, value: string) => {
    setFormJsonFields((prev) => ({ ...prev, [field]: value }));
    try {
      JSON.parse(value);
      setJsonErrors((prev) => ({ ...prev, [field]: '' }));
    } catch {
      setJsonErrors((prev) => ({ ...prev, [field]: 'JSON 格式错误' }));
    }
  };

  const handleSave = async () => {
    const hasError = Object.values(jsonErrors).some((e) => e);
    if (hasError) {
      msg.error('请修复 JSON 格式错误');
      return;
    }

    const jsonData: Record<string, any> = {};
    JSON_EDITOR_FIELDS.forEach((f) => {
      try {
        jsonData[f] = JSON.parse(formJsonFields[f]);
      } catch {
        jsonData[f] = {};
      }
    });

    setSaving(true);
    try {
      if (editingProfile) {
        await api.patch(`/agent-profiles/${editingProfile.id}`, { name: formName, ...jsonData });
        msg.success('已更新');
      } else {
        await api.post('/agent-profiles', { name: formName, ...jsonData });
        msg.success('已创建');
      }
      setDrawerOpen(false);
      fetchProfiles();
    } catch {
      msg.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/agent-profiles/${id}`);
      msg.success('已删除');
      fetchProfiles();
    } catch {
      msg.error('删除失败');
    }
  };

  const openDispatch = (profileId?: string) => {
    setDispatchTarget(profileId || '');
    setDispatchType('shell');
    setDispatchContent('');
    setDispatchResult(null);
    setDispatchOpen(true);
  };

  const handleDispatch = async () => {
    if (!dispatchTarget) {
      msg.error('请选择目标客户端');
      return;
    }
    if (!dispatchContent.trim()) {
      msg.error('请输入任务内容');
      return;
    }
    setDispatching(true);
    try {
      const res = await api.post(`/agent-profiles/${dispatchTarget}/dispatch`, {
        type: dispatchType,
        content: dispatchContent,
      });
      setDispatchResult(res.data);
      msg.success('任务已下发');
    } catch (err: any) {
      msg.error(err?.response?.data?.detail || '下发失败');
    } finally {
      setDispatching(false);
    }
  };

  const openDetail = async (profile: AgentProfileData) => {
    setDetailProfile(profile);
    setDetailOpen(true);
    try {
      const [tRes, mRes] = await Promise.all([
        api.get(`/agent-profiles/${profile.id}/tasks`),
        api.get(`/agent-profiles/${profile.id}/metrics`),
      ]);
      setDetailTasks(tRes.data ?? []);
      setDetailMetrics(mRes.data ?? []);
    } catch {
      /* keep stale */
    }
  };

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, r: AgentProfileData) => (
        <Space>
          <Badge status={r.online ? 'success' : 'default'} />
          <a onClick={() => openDetail(r)}>{name}</a>
          {r.profile_version > 0 && <Tag style={{ fontSize: 10 }}>v{r.profile_version}</Tag>}
        </Space>
      ),
    },
    {
      title: '主机名',
      dataIndex: 'hostname',
      key: 'hostname',
      render: (v: string | null) => v || '-',
    },
    {
      title: 'IP 地址',
      dataIndex: 'ip_address',
      key: 'ip_address',
      render: (v: string | null) => v || '-',
    },
    {
      title: '系统',
      dataIndex: 'os_info',
      key: 'os_info',
      render: (v: string | null) => v || '-',
    },
    {
      title: 'Agent 版本',
      dataIndex: 'agent_version',
      key: 'agent_version',
      render: (v: string | null) => v || '-',
    },
    {
      title: '最后心跳',
      dataIndex: 'last_heartbeat',
      key: 'last_heartbeat',
      render: (v: string | null) => (v ? new Date(v).toLocaleString() : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: any, r: AgentProfileData) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<SendOutlined />}
            disabled={!r.online}
            onClick={() => openDispatch(r.id)}
          >
            下发
          </Button>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(r.id)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: '0 0 16px' }}>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8}>
          <Card size="small" style={{ borderRadius: 12, textAlign: 'center' }}>
            <Text type="secondary">客户端总数</Text>
            <div style={{ fontSize: 28, fontWeight: 700, color: token.colorPrimary }}>
              {profiles.length}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card size="small" style={{ borderRadius: 12, textAlign: 'center' }}>
            <Text type="secondary">在线</Text>
            <div style={{ fontSize: 28, fontWeight: 700, color: token.colorSuccess }}>
              {onlineCount}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card size="small" style={{ borderRadius: 12, textAlign: 'center' }}>
            <Text type="secondary">离线</Text>
            <div style={{ fontSize: 28, fontWeight: 700, color: token.colorTextTertiary }}>
              {profiles.length - onlineCount}
            </div>
          </Card>
        </Col>
      </Row>

      <Card size="small" style={{ borderRadius: 12, marginBottom: 16 }}>
        <Space wrap>
          <Select
            value={filterStatus}
            onChange={setFilterStatus}
            options={STATUS_OPTIONS}
            style={{ width: 120 }}
            size="small"
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={fetchProfiles}>
            刷新
          </Button>
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建
          </Button>
          <Button size="small" icon={<SendOutlined />} onClick={() => openDispatch()}>
            下发任务
          </Button>
        </Space>
      </Card>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
        </div>
      ) : filtered.length === 0 ? (
        <Empty description="暂无客户端配置，点击「新建」添加" />
      ) : (
        <Table
          dataSource={filtered}
          columns={columns}
          rowKey="id"
          size="middle"
          pagination={{ pageSize: 20, showSizeChanger: true }}
        />
      )}

      <Drawer
        title={editingProfile ? `编辑 ${editingProfile.name}` : '新建客户端配置'}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={640}
        destroyOnHidden
        footer={
          <Space style={{ float: 'right' }}>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            <Button type="primary" onClick={handleSave} loading={saving}>
              保存
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text strong>名称</Text>
            <Input
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder="例如：生产服务器-Agent"
              maxLength={256}
              style={{ marginTop: 4 }}
            />
          </div>
          {JSON_EDITOR_FIELDS.map((field) => (
            <div key={field}>
              <Text strong>{field}</Text>
              <TextArea
                value={formJsonFields[field]}
                onChange={(e) => handleJsonChange(field, e.target.value)}
                rows={4}
                style={{
                  marginTop: 4,
                  fontFamily: 'monospace',
                  fontSize: 12,
                  ...(jsonErrors[field] ? { borderColor: token.colorError } : {}),
                }}
              />
              {jsonErrors[field] && (
                <Text type="danger" style={{ fontSize: 12 }}>
                  {jsonErrors[field]}
                </Text>
              )}
            </div>
          ))}
        </Space>
      </Drawer>

      <Modal
        title="下发任务"
        open={dispatchOpen}
        onCancel={() => setDispatchOpen(false)}
        onOk={handleDispatch}
        confirmLoading={dispatching}
        okText="下发"
        width={520}
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text strong>目标客户端</Text>
            <Select
              value={dispatchTarget || undefined}
              onChange={setDispatchTarget}
              placeholder="选择客户端"
              style={{ width: '100%', marginTop: 4 }}
              options={profiles
                .filter((p) => p.online)
                .map((p) => ({
                  value: p.id,
                  label: `${p.name}${p.hostname ? ` (${p.hostname})` : ''}`,
                }))}
            />
          </div>
          <div>
            <Text strong>任务类型</Text>
            <div style={{ marginTop: 4 }}>
              <Radio.Group
                value={dispatchType}
                onChange={(e) => setDispatchType(e.target.value)}
                options={DISPATCH_TYPES}
                optionType="button"
                buttonStyle="solid"
              />
            </div>
          </div>
          <div>
            <Text strong>任务内容</Text>
            <TextArea
              value={dispatchContent}
              onChange={(e) => setDispatchContent(e.target.value)}
              rows={6}
              placeholder={
                dispatchType === 'shell'
                  ? '例如：df -h'
                  : dispatchType === 'nl'
                    ? '检查磁盘使用情况'
                    : '#!/bin/bash\n...'
              }
              style={{ marginTop: 4, fontFamily: 'monospace', fontSize: 13 }}
            />
          </div>
          {dispatchResult && (
            <Card size="small" style={{ background: token.colorSuccessBg, borderRadius: 8 }}>
              <Text>
                任务已下发: <Text code>{dispatchResult.task_id}</Text>
              </Text>
            </Card>
          )}
        </Space>
      </Modal>

      <Drawer
        title={detailProfile ? `${detailProfile.name} — 详情` : '详情'}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={720}
        destroyOnHidden
      >
        {detailProfile && (
          <Space direction="vertical" style={{ width: '100%' }} size="large">
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="在线状态">
                <Badge
                  status={detailProfile.online ? 'success' : 'default'}
                  text={detailProfile.online ? '在线' : '离线'}
                />
              </Descriptions.Item>
              <Descriptions.Item label="版本">v{detailProfile.profile_version}</Descriptions.Item>
              <Descriptions.Item label="Agent 版本">
                {detailProfile.agent_version || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="最后心跳">
                {detailProfile.last_heartbeat
                  ? new Date(detailProfile.last_heartbeat).toLocaleString()
                  : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="主机名">{detailProfile.hostname || '-'}</Descriptions.Item>
              <Descriptions.Item label="IP 地址">
                {detailProfile.ip_address || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="操作系统" span={2}>
                {detailProfile.os_info || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间" span={2}>
                {detailProfile.created_at
                  ? new Date(detailProfile.created_at).toLocaleString()
                  : '-'}
              </Descriptions.Item>
            </Descriptions>

            <Card title="任务历史" size="small" style={{ borderRadius: 12 }}>
              {detailTasks.length === 0 ? (
                <Empty description="暂无任务" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <Table
                  dataSource={detailTasks}
                  rowKey="task_id"
                  size="small"
                  pagination={false}
                  columns={[
                    {
                      title: '任务ID',
                      dataIndex: 'task_id',
                      key: 'id',
                      width: 100,
                      render: (v: string) => <Text code>{v.slice(0, 8)}</Text>,
                    },
                    { title: '类型', dataIndex: 'type', key: 'type', width: 70 },
                    {
                      title: '内容',
                      dataIndex: 'content',
                      key: 'content',
                      render: (v: string) => (
                        <Text ellipsis style={{ maxWidth: 200 }}>
                          {v}
                        </Text>
                      ),
                    },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      key: 'status',
                      width: 80,
                      render: (v: string) => (
                        <Tag
                          color={v === 'ok' ? 'success' : v === 'pending' ? 'processing' : 'error'}
                        >
                          {v}
                        </Tag>
                      ),
                    },
                    {
                      title: '时间',
                      dataIndex: 'created_at',
                      key: 'time',
                      width: 150,
                      render: (v: string) => (v ? new Date(v).toLocaleString() : '-'),
                    },
                  ]}
                />
              )}
            </Card>

            <Card title="指标数据" size="small" style={{ borderRadius: 12 }}>
              {detailMetrics.length === 0 ? (
                <Empty description="暂无指标" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <Table
                  dataSource={detailMetrics.slice(-20)}
                  rowKey="recorded_at"
                  size="small"
                  pagination={false}
                  columns={[
                    {
                      title: 'CPU %',
                      dataIndex: 'cpu_percent',
                      key: 'cpu',
                      width: 80,
                      render: (v: number | null) => (v != null ? `${v.toFixed(1)}%` : '-'),
                    },
                    {
                      title: '内存 %',
                      dataIndex: 'memory_percent',
                      key: 'mem',
                      width: 80,
                      render: (v: number | null) => (v != null ? `${v.toFixed(1)}%` : '-'),
                    },
                    {
                      title: '磁盘 %',
                      dataIndex: 'disk_percent',
                      key: 'disk',
                      width: 80,
                      render: (v: number | null) => (v != null ? `${v.toFixed(1)}%` : '-'),
                    },
                    {
                      title: '网络 RX',
                      dataIndex: 'network_rx_bytes',
                      key: 'rx',
                      width: 100,
                      render: (v: number | null) =>
                        v != null ? `${(v / 1024).toFixed(0)} KB` : '-',
                    },
                    {
                      title: '网络 TX',
                      dataIndex: 'network_tx_bytes',
                      key: 'tx',
                      width: 100,
                      render: (v: number | null) =>
                        v != null ? `${(v / 1024).toFixed(0)} KB` : '-',
                    },
                    {
                      title: '时间',
                      dataIndex: 'recorded_at',
                      key: 'time',
                      render: (v: string | null) => (v ? new Date(v).toLocaleString() : '-'),
                    },
                  ]}
                />
              )}
            </Card>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
