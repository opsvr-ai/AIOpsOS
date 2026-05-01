import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Tag,
  Space,
  Typography,
  App,
  Button,
  Modal,
  Form,
  Input,
  Select,
  Popconfirm,
  Tooltip,
  theme,
  Badge,
} from 'antd';
import {
  TeamOutlined,
  UserOutlined,
  PlusOutlined,
  CheckOutlined,
  CloseOutlined,
  SearchOutlined,
  MailOutlined,
} from '@ant-design/icons';
import { authApi } from '@/services/auth';

interface UserRecord {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  is_ldap: boolean;
  roles: { id: string; name: string; permissions: { resource: string; action: string }[] }[];
  display_name?: string;
  phone?: string;
  department?: string;
  title?: string;
  source: string;
  status: string;
  created_at?: string;
}

const statusMap: Record<string, { color: string; text: string }> = {
  active: { color: 'green', text: '正常' },
  pending: { color: 'gold', text: '待审批' },
  disabled: { color: 'default', text: '已禁用' },
};

const sourceMap: Record<string, { color: string; text: string }> = {
  local: { color: 'blue', text: '本地' },
  ldap: { color: 'purple', text: 'LDAP' },
  invited: { color: 'cyan', text: '邀请' },
};

export default function UsersPage() {
  const { token } = theme.useToken();
  const { message: msg } = App.useApp();
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm] = Form.useForm();

  const fetch = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authApi.getUsers({
        q: search || undefined,
        status: statusFilter,
        page_size: 100,
      });
      setUsers((res.data as unknown as UserRecord[]) || []);
    } catch {
      setUsers([]);
    } finally {
      setLoading(false);
    }
  }, [search, statusFilter]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const handleCreate = async () => {
    try {
      const values = await createForm.validateFields();
      await authApi.register(values as any);
      msg.success('用户创建成功');
      setCreateOpen(false);
      createForm.resetFields();
      fetch();
    } catch (e: any) {
      if (e?.errorFields) return;
      msg.error(e?.response?.data?.detail || '创建失败');
    }
  };

  const handleInvite = async () => {
    try {
      const values = await inviteForm.validateFields();
      await authApi.inviteUser({ email: values.email, platform_url: window.location.origin });
      msg.success('邀请已发送');
      setInviteOpen(false);
      inviteForm.resetFields();
    } catch (e: any) {
      if (e?.errorFields) return;
      msg.error(e?.response?.data?.detail || '邀请失败');
    }
  };

  const handleApprove = async (userId: string, approved: boolean) => {
    try {
      await authApi.approveUser(userId, { approved });
      msg.success(approved ? '已批准' : '已拒绝');
      fetch();
    } catch (e: any) {
      msg.error(e?.response?.data?.detail || '操作失败');
    }
  };

  const handleDelete = async (userId: string) => {
    try {
      await authApi.deleteUser(userId);
      msg.success('已禁用');
      fetch();
    } catch (e: any) {
      msg.error(e?.response?.data?.detail || '操作失败');
    }
  };

  const columns = [
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      render: (v: string, r: UserRecord) => (
        <Space>
          <UserOutlined style={{ color: token.colorPrimary }} />
          <span style={{ fontWeight: 500 }}>{v}</span>
          {r.display_name && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              ({r.display_name})
            </Typography.Text>
          )}
        </Space>
      ),
    },
    { title: '邮箱', dataIndex: 'email', key: 'email' },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 70,
      render: (v: string) => {
        const s = sourceMap[v] || { color: 'default', text: v };
        return <Tag color={s.color}>{s.text}</Tag>;
      },
    },
    {
      title: '角色',
      key: 'roles',
      width: 120,
      render: (_: unknown, r: UserRecord) =>
        r.roles?.map((role) => <Tag key={role.id}>{role.name}</Tag>),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (v: string) => {
        const s = statusMap[v] || { color: 'default', text: v };
        return <Badge status={s.color as any} text={s.text} />;
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 150,
      render: (v: string) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: unknown, r: UserRecord) => (
        <Space size="small">
          {r.status === 'pending' && (
            <>
              <Tooltip title="批准">
                <Button
                  type="link"
                  size="small"
                  icon={<CheckOutlined />}
                  onClick={() => handleApprove(r.id, true)}
                />
              </Tooltip>
              <Tooltip title="拒绝">
                <Button
                  type="link"
                  size="small"
                  danger
                  icon={<CloseOutlined />}
                  onClick={() => handleApprove(r.id, false)}
                />
              </Tooltip>
            </>
          )}
          {r.status !== 'disabled' && (
            <Popconfirm title="确定禁用该用户？" onConfirm={() => handleDelete(r.id)}>
              <Button type="link" size="small" danger>
                禁用
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div
        style={{
          marginBottom: 20,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <Typography.Title level={4} style={{ margin: 0, fontWeight: 600 }}>
          <TeamOutlined style={{ marginRight: 8 }} />
          用户管理
        </Typography.Title>
        <Space>
          <Input
            prefix={<SearchOutlined />}
            placeholder="搜索用户名/邮箱"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
            style={{ width: 200 }}
          />
          <Select
            placeholder="状态筛选"
            value={statusFilter}
            onChange={setStatusFilter}
            allowClear
            style={{ width: 110 }}
            options={[
              { value: 'active', label: '正常' },
              { value: 'pending', label: '待审批' },
              { value: 'disabled', label: '已禁用' },
            ]}
          />
          <Button icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            创建用户
          </Button>
          <Button icon={<MailOutlined />} onClick={() => setInviteOpen(true)}>
            邀请用户
          </Button>
        </Space>
      </div>

      <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={users}
          columns={columns}
          rowKey="id"
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="middle"
          loading={loading}
        />
      </Card>

      <Modal
        title="创建用户"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => setCreateOpen(false)}
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, message: '请输入邮箱' }]}>
            <Input type="email" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="邀请用户"
        open={inviteOpen}
        onOk={handleInvite}
        onCancel={() => setInviteOpen(false)}
      >
        <Form form={inviteForm} layout="vertical">
          <Form.Item
            name="email"
            label="邮箱"
            rules={[{ required: true, message: '请输入邮箱' }, { type: 'email' }]}
          >
            <Input placeholder="被邀请人的邮箱" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
