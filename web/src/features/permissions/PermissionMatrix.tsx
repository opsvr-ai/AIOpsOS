import { useCallback, useEffect, useState } from 'react';
import {
  Card,
  Button,
  Space,
  App,
  Select,
  Checkbox,
  Typography,
  theme,
  Spin,
  Empty,
  Modal,
  Input,
  Tag,
  Popconfirm,
} from 'antd';
import {
  PlusOutlined,
  ReloadOutlined,
  DeleteOutlined,
  SafetyCertificateOutlined,
  CopyOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

const { Text, Title } = Typography;

interface Permission {
  id: string;
  resource: string;
  action: string;
  description: string | null;
}

interface Role {
  id: string;
  name: string;
  description: string | null;
  permissions: Permission[];
}

const RESOURCES = [
  { key: 'agents', label: '智能体', group: 'AI Center' },
  { key: 'tools', label: '工具', group: 'AI Center' },
  { key: 'knowledge', label: '知识库', group: 'AI Center' },
  { key: 'cron', label: '定时任务', group: 'AI Center' },
  { key: 'schedules', label: '场景计划', group: 'AI Center' },
  { key: 'agent_profiles', label: '客户端Agent', group: 'AI Center' },
  { key: 'alerts', label: '告警', group: 'Ops Center' },
  { key: 'scenarios', label: '场景运维', group: 'Ops Center' },
  { key: 'datasources', label: '数据源', group: 'Ops Center' },
  { key: 'automation', label: '自动化', group: 'Ops Center' },
  { key: 'channels', label: '消息渠道', group: 'Control Center' },
  { key: 'users', label: '用户管理', group: 'Control Center' },
  { key: 'roles', label: '角色管理', group: 'Control Center' },
  { key: 'permissions', label: '权限管理', group: 'Control Center' },
  { key: 'system', label: '系统设置', group: 'Control Center' },
];

const ACTIONS = ['view', 'create', 'update', 'delete', 'manage'];

const ROLE_TEMPLATES: Record<string, { name: string; description: string; resources: string[] }> = {
  admin: {
    name: '管理员',
    description: '全部权限',
    resources: RESOURCES.map((r) => r.key),
  },
  operator: {
    name: '运维操作员',
    description: '查看 + 操作权限',
    resources: [
      'agents',
      'tools',
      'knowledge',
      'cron',
      'alerts',
      'scenarios',
      'datasources',
      'channels',
    ],
  },
  viewer: {
    name: '只读用户',
    description: '仅查看权限',
    resources: ['agents', 'tools', 'knowledge', 'alerts', 'scenarios', 'datasources', 'channels'],
  },
};

export default function PermissionMatrix() {
  const { token } = theme.useToken();
  const { message: msg } = App.useApp();

  const [roles, setRoles] = useState<Role[]>([]);
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedRoleId, setSelectedRoleId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [roleModalOpen, setRoleModalOpen] = useState(false);
  const [roleFormName, setRoleFormName] = useState('');
  const [roleFormDesc, setRoleFormDesc] = useState('');

  const [templateOpen, setTemplateOpen] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);

    let roleList: Role[] = [];
    try {
      const roleRes = await api.get('/roles');
      roleList = roleRes.data ?? [];
      setRoles(roleList);
    } catch {
      msg.error('加载角色列表失败');
    }

    try {
      const permRes = await api.get('/permissions');
      setPermissions(permRes.data ?? []);
    } catch {
      msg.error('加载权限列表失败');
    }

    if (!selectedRoleId && roleList.length > 0) {
      setSelectedRoleId(roleList[0].id);
    }

    setLoading(false);
  }, [msg]);

  useEffect(() => {
    fetchData();
  }, []);

  const selectedRole = roles.find((r) => r.id === selectedRoleId);
  const permMap = new Map<string, Permission>();
  permissions.forEach((p) => permMap.set(`${p.resource}:${p.action}`, p));

  const rolePermSet = new Set(
    (selectedRole?.permissions || []).map((p) => `${p.resource}:${p.action}`),
  );

  const togglePerm = async (resource: string, action: string) => {
    if (!selectedRoleId || !selectedRole) return;
    const key = `${resource}:${action}`;
    let permId = permMap.get(key)?.id;

    if (!permId) {
      try {
        const res = await api.post('/permissions', { resource, action });
        permId = res.data.id;
        setPermissions((prev) => [...prev, res.data]);
        permMap.set(key, res.data);
      } catch {
        msg.error('创建权限失败');
        return;
      }
    }

    const currentIds = selectedRole.permissions.map((p) => p.id);
    const newIds = rolePermSet.has(key)
      ? currentIds.filter((id) => id !== permId)
      : [...currentIds, permId];

    setSaving(true);
    try {
      await api.patch(`/roles/${selectedRoleId}`, { permission_ids: newIds });
      setRoles((prev) =>
        prev.map((r) =>
          r.id === selectedRoleId
            ? {
                ...r,
                permissions: newIds.map(
                  (id) =>
                    r.permissions.find((p) => p.id === id) ||
                    ({ id, resource, action } as Permission),
                ),
              }
            : r,
        ),
      );
    } catch {
      msg.error('更新权限失败');
    } finally {
      setSaving(false);
    }
  };

  const handleCreateRole = async () => {
    if (!roleFormName.trim()) {
      msg.error('请输入角色名称');
      return;
    }
    try {
      await api.post('/roles', { name: roleFormName, description: roleFormDesc });
      msg.success('角色已创建');
      setRoleModalOpen(false);
      setRoleFormName('');
      setRoleFormDesc('');
      fetchData();
    } catch {
      msg.error('创建角色失败');
    }
  };

  const handleDeleteRole = async (id: string) => {
    try {
      await api.delete(`/roles/${id}`);
      msg.success('已删除');
      if (selectedRoleId === id) setSelectedRoleId(null);
      fetchData();
    } catch {
      msg.error('删除失败');
    }
  };

  const handleTemplate = async (type: string) => {
    const tmpl = ROLE_TEMPLATES[type];
    if (!tmpl) return;
    try {
      const existingPermIds: string[] = [];
      for (const resource of tmpl.resources) {
        for (const action of ACTIONS) {
          const key = `${resource}:${action}`;
          let pid: string | undefined = permMap.get(key)?.id;
          if (!pid) {
            const res = await api.post('/permissions', { resource, action });
            pid = res.data.id;
            permMap.set(key, res.data);
          }
          existingPermIds.push(pid!);
        }
      }
      await api.post('/roles', {
        name: tmpl.name,
        description: tmpl.description,
        permission_ids: existingPermIds,
      });
      msg.success(`已从模板创建: ${tmpl.name}`);
      setTemplateOpen(false);
      fetchData();
    } catch {
      msg.error('模板创建失败');
    }
  };

  const groupedResources = RESOURCES.reduce<Record<string, typeof RESOURCES>>((acc, r) => {
    (acc[r.group] ??= []).push(r);
    return acc;
  }, {});

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

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
        <Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          权限矩阵
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchData}>
            刷新
          </Button>
          <Button icon={<CopyOutlined />} onClick={() => setTemplateOpen(true)}>
            从模板创建
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setRoleFormName('');
              setRoleFormDesc('');
              setRoleModalOpen(true);
            }}
          >
            创建角色
          </Button>
        </Space>
      </div>

      <Card size="small" style={{ borderRadius: 12, marginBottom: 16 }}>
        <Space wrap>
          <Text strong>当前角色:</Text>
          <Select
            value={selectedRoleId}
            onChange={setSelectedRoleId}
            style={{ width: 240 }}
            placeholder="选择角色"
            options={roles.map((r) => ({ value: r.id, label: r.name }))}
          />
          {selectedRole && (
            <>
              <Tag color="blue">{selectedRole.description || selectedRole.name}</Tag>
              <Popconfirm title="删除此角色?" onConfirm={() => handleDeleteRole(selectedRole.id!)}>
                <Button size="small" danger icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            </>
          )}
          {saving && <Spin size="small" />}
        </Space>
      </Card>

      {!selectedRole ? (
        <Empty description="请选择或创建一个角色" />
      ) : (
        Object.entries(groupedResources).map(([group, resources]) => (
          <Card
            key={group}
            title={
              <Text strong style={{ color: token.colorPrimary }}>
                {group}
              </Text>
            }
            size="small"
            style={{ borderRadius: 12, marginBottom: 16 }}
          >
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th
                      style={{
                        textAlign: 'left',
                        padding: '6px 12px',
                        fontWeight: 600,
                        width: 120,
                      }}
                    >
                      资源
                    </th>
                    {ACTIONS.map((a) => (
                      <th
                        key={a}
                        style={{
                          textAlign: 'center',
                          padding: '6px 8px',
                          fontWeight: 600,
                          width: 80,
                        }}
                      >
                        {a}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {resources.map((r) => (
                    <tr
                      key={r.key}
                      style={{ borderTop: `1px solid ${token.colorBorderSecondary}` }}
                    >
                      <td style={{ padding: '8px 12px' }}>
                        <Text>{r.label}</Text>
                      </td>
                      {ACTIONS.map((a) => (
                        <td key={a} style={{ textAlign: 'center', padding: '6px 8px' }}>
                          <Checkbox
                            checked={rolePermSet.has(`${r.key}:${a}`)}
                            onChange={() => togglePerm(r.key, a)}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ))
      )}

      <Modal
        title="创建角色"
        open={roleModalOpen}
        onCancel={() => setRoleModalOpen(false)}
        onOk={handleCreateRole}
        okText="创建"
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            <Text strong>角色名称</Text>
            <Input
              value={roleFormName}
              onChange={(e) => setRoleFormName(e.target.value)}
              placeholder="例如：运维工程师"
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Text strong>描述</Text>
            <Input
              value={roleFormDesc}
              onChange={(e) => setRoleFormDesc(e.target.value)}
              placeholder="可选描述"
              style={{ marginTop: 4 }}
            />
          </div>
        </Space>
      </Modal>

      <Modal
        title="从模板创建角色"
        open={templateOpen}
        onCancel={() => setTemplateOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          {Object.entries(ROLE_TEMPLATES).map(([key, tmpl]) => (
            <Card
              key={key}
              size="small"
              hoverable
              style={{ borderRadius: 8, cursor: 'pointer' }}
              onClick={() => handleTemplate(key)}
            >
              <Space>
                <SafetyCertificateOutlined style={{ color: token.colorPrimary }} />
                <div>
                  <Text strong>{tmpl.name}</Text>
                  <br />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {tmpl.description}
                  </Text>
                  <br />
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {tmpl.resources.length} 资源 x {ACTIONS.length} 操作 ={' '}
                    {tmpl.resources.length * ACTIONS.length} 项权限
                  </Text>
                </div>
              </Space>
            </Card>
          ))}
        </Space>
      </Modal>
    </div>
  );
}
