import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Button,
  Space,
  Typography,
  Tag,
  Popconfirm,
  App,
  Empty,
  Spin,
  theme,
  Tabs,
  Modal,
  Input,
  Radio,
  Select,
  List,
  Avatar,
} from 'antd';
import {
  TeamOutlined,
  LockOutlined,
  GlobalOutlined,
  EditOutlined,
  DeleteOutlined,
  UserOutlined,
  CheckOutlined,
  CloseOutlined,
  CrownOutlined,
  ArrowLeftOutlined,
  UserAddOutlined,
} from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import api from '@/services/api';

interface SpaceDetail {
  id: string;
  name: string;
  description: string | null;
  visibility: string;
  created_by: string;
  member_count: number;
  my_role: string | null;
}

interface MemberData {
  id: string;
  user_id: string;
  username: string;
  email: string;
  role: string;
  joined_at: string;
}

interface InvitationData {
  id: string;
  inviter_id: string;
  invitee_id: string;
  invitee_name: string;
  status: string;
  created_at: string;
}

interface JoinRequestData {
  id: string;
  user_id: string;
  username: string;
  message: string | null;
  status: string;
  created_at: string;
}

export default function SpaceDetailPage() {
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const { spaceId } = useParams<{ spaceId: string }>();
  const { message: msg } = App.useApp();

  const [space, setSpace] = useState<SpaceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [members, setMembers] = useState<MemberData[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [invitations, setInvitations] = useState<InvitationData[]>([]);
  const [joinRequests, setJoinRequests] = useState<JoinRequestData[]>([]);

  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editVisibility, setEditVisibility] = useState('private');
  const [saving, setSaving] = useState(false);

  const [inviteOpen, setInviteOpen] = useState(false);
  const [searchUsers, setSearchUsers] = useState<any[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [inviteUserId, setInviteUserId] = useState('');

  const isAdmin = space?.my_role === 'admin';

  const fetchSpace = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`/spaces/${spaceId}`);
      setSpace(res.data);
    } catch {
      msg.error('加载空间信息失败');
    } finally {
      setLoading(false);
    }
  }, [spaceId, msg]);

  const fetchMembers = useCallback(async () => {
    setMembersLoading(true);
    try {
      const res = await api.get(`/spaces/${spaceId}/members`);
      setMembers(res.data ?? []);
    } catch {
      /* ignore */
    } finally {
      setMembersLoading(false);
    }
  }, [spaceId]);

  const fetchInvitations = useCallback(async () => {
    try {
      const res = await api.get(`/spaces/${spaceId}/invitations`);
      setInvitations(res.data ?? []);
    } catch {
      /* ignore */
    }
  }, [spaceId]);

  const fetchJoinRequests = useCallback(async () => {
    try {
      const res = await api.get(`/spaces/${spaceId}/join-requests`);
      setJoinRequests(res.data ?? []);
    } catch {
      /* ignore */
    }
  }, [spaceId]);

  useEffect(() => {
    fetchSpace();
    fetchMembers();
    fetchInvitations();
    fetchJoinRequests();
  }, [fetchSpace, fetchMembers, fetchInvitations, fetchJoinRequests]);

  const handleEdit = async () => {
    setSaving(true);
    try {
      await api.put(`/spaces/${spaceId}`, {
        name: editName.trim(),
        description: editDesc.trim() || null,
        visibility: editVisibility,
      });
      msg.success('已更新');
      setEditOpen(false);
      fetchSpace();
    } catch {
      msg.error('更新失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    try {
      await api.delete(`/spaces/${spaceId}`);
      msg.success('空间已删除');
      navigate('/spaces');
    } catch {
      msg.error('删除失败');
    }
  };

  const handleInvite = async () => {
    if (!inviteUserId) return;
    try {
      await api.post(`/spaces/${spaceId}/invite`, { user_id: inviteUserId });
      msg.success('已发送邀请');
      setInviteOpen(false);
      setInviteUserId('');
      fetchInvitations();
    } catch (e: any) {
      msg.error(e?.response?.data?.detail ?? '邀请失败');
    }
  };

  const handleSearchUser = async (q: string) => {
    if (q.length < 1) {
      setSearchUsers([]);
      return;
    }
    setSearchLoading(true);
    try {
      const res = await api.get('/spaces/search-users', { params: { q } });
      setSearchUsers(res.data ?? []);
    } catch {
      /* ignore */
    } finally {
      setSearchLoading(false);
    }
  };

  const handleRoleChange = async (userId: string, role: string) => {
    try {
      await api.put(`/spaces/${spaceId}/members/${userId}/role`, { role });
      msg.success('角色已更新');
      fetchMembers();
    } catch (e: any) {
      msg.error(e?.response?.data?.detail ?? '操作失败');
    }
  };

  const handleRemoveMember = async (userId: string) => {
    try {
      await api.delete(`/spaces/${spaceId}/members/${userId}`);
      msg.success('已移除成员');
      fetchMembers();
      fetchSpace();
    } catch (e: any) {
      msg.error(e?.response?.data?.detail ?? '移除失败');
    }
  };

  const handleReviewJoin = async (reqId: string, status: string) => {
    try {
      await api.put(`/spaces/${spaceId}/join-requests/${reqId}`, { status });
      msg.success(status === 'approved' ? '已通过' : '已拒绝');
      fetchJoinRequests();
      fetchMembers();
      fetchSpace();
    } catch {
      msg.error('操作失败');
    }
  };

  const handleLeave = async () => {
    try {
      await api.post(`/spaces/${spaceId}/leave`);
      msg.success('已退出空间');
      navigate('/spaces');
    } catch (e: any) {
      msg.error(e?.response?.data?.detail ?? '退出失败');
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!space) {
    return <Empty description="空间不存在" />;
  }

  const spacesActions = isAdmin
    ? [
        <Button
          key="edit"
          icon={<EditOutlined />}
          onClick={() => {
            setEditName(space.name);
            setEditDesc(space.description ?? '');
            setEditVisibility(space.visibility);
            setEditOpen(true);
          }}
        >
          编辑
        </Button>,
        <Popconfirm key="del" title="确定删除此空间？" onConfirm={handleDelete}>
          <Button danger icon={<DeleteOutlined />}>
            删除
          </Button>
        </Popconfirm>,
      ]
    : [
        <Popconfirm key="leave" title="确定退出此空间？" onConfirm={handleLeave}>
          <Button danger>退出空间</Button>
        </Popconfirm>,
      ];

  return (
    <div>
      <Space style={{ marginBottom: 20 }}>
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/spaces')}>
          返回
        </Button>
      </Space>

      <Card style={{ borderRadius: 12, marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <Space direction="vertical" size={4}>
            <Space size={8}>
              <Typography.Title level={4} style={{ margin: 0 }}>
                {space.name}
              </Typography.Title>
              <Tag
                color={space.visibility === 'public' ? 'green' : 'default'}
                icon={space.visibility === 'public' ? <GlobalOutlined /> : <LockOutlined />}
                style={{ borderRadius: 4 }}
              >
                {space.visibility === 'public' ? '公开' : '私有'}
              </Tag>
              {isAdmin && (
                <Tag color="orange" style={{ borderRadius: 4 }}>
                  管理员
                </Tag>
              )}
            </Space>
            <Typography.Text type="secondary">{space.description || '暂无描述'}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              <TeamOutlined style={{ marginRight: 4 }} />
              {space.member_count} 位成员
            </Typography.Text>
          </Space>
          <Space>{spacesActions}</Space>
        </div>
      </Card>

      <Tabs
        type="card"
        items={[
          {
            key: 'members',
            label: `成员 (${members.length})`,
            children: (
              <>
                {isAdmin && (
                  <div style={{ marginBottom: 16 }}>
                    <Button
                      type="primary"
                      icon={<UserAddOutlined />}
                      onClick={() => setInviteOpen(true)}
                    >
                      邀请成员
                    </Button>
                  </div>
                )}
                {membersLoading ? (
                  <div style={{ textAlign: 'center', padding: 40 }}>
                    <Spin />
                  </div>
                ) : (
                  <List
                    dataSource={members}
                    renderItem={(m) => (
                      <List.Item
                        actions={
                          isAdmin && m.user_id !== space.created_by
                            ? [
                                <Select
                                  key="role"
                                  value={m.role}
                                  onChange={(v) => handleRoleChange(m.user_id, v)}
                                  style={{ width: 80 }}
                                  size="small"
                                  options={[
                                    { value: 'admin', label: '管理员' },
                                    { value: 'member', label: '成员' },
                                  ]}
                                />,
                                <Popconfirm
                                  key="remove"
                                  title="确定移除？"
                                  onConfirm={() => handleRemoveMember(m.user_id)}
                                >
                                  <Button
                                    size="small"
                                    danger
                                    type="text"
                                    icon={<CloseOutlined />}
                                  />
                                </Popconfirm>,
                              ]
                            : undefined
                        }
                      >
                        <List.Item.Meta
                          avatar={
                            <Avatar
                              icon={<UserOutlined />}
                              style={{
                                backgroundColor:
                                  m.user_id === space.created_by
                                    ? token.colorPrimary
                                    : token.colorTextQuaternary,
                              }}
                            />
                          }
                          title={
                            <Space size={4}>
                              {m.username}
                              {m.role === 'admin' && (
                                <Tag color="orange" style={{ borderRadius: 4, fontSize: 10 }}>
                                  管理员
                                </Tag>
                              )}
                              {m.user_id === space.created_by && (
                                <Tag color="blue" style={{ borderRadius: 4, fontSize: 10 }}>
                                  <CrownOutlined /> 创建者
                                </Tag>
                              )}
                            </Space>
                          }
                          description={m.email}
                        />
                      </List.Item>
                    )}
                  />
                )}
              </>
            ),
          },
          ...(isAdmin
            ? [
                {
                  key: 'invitations',
                  label: `邀请 (${invitations.length})`,
                  children: (
                    <List
                      dataSource={invitations}
                      locale={{ emptyText: <Empty description="暂无邀请记录" /> }}
                      renderItem={(inv) => (
                        <List.Item>
                          <List.Item.Meta
                            title={`被邀请人: ${inv.invitee_name || inv.invitee_id}`}
                            description={new Date(inv.created_at).toLocaleString()}
                          />
                          <Tag
                            color={
                              inv.status === 'pending'
                                ? 'blue'
                                : inv.status === 'accepted'
                                  ? 'green'
                                  : 'red'
                            }
                          >
                            {inv.status === 'pending'
                              ? '待确认'
                              : inv.status === 'accepted'
                                ? '已接受'
                                : '已拒绝'}
                          </Tag>
                        </List.Item>
                      )}
                    />
                  ),
                },
                {
                  key: 'join-requests',
                  label: `申请 (${joinRequests.filter((r) => r.status === 'pending').length})`,
                  children: (
                    <List
                      dataSource={joinRequests}
                      locale={{ emptyText: <Empty description="暂无加入申请" /> }}
                      renderItem={(req) => (
                        <List.Item
                          actions={
                            req.status === 'pending'
                              ? [
                                  <Button
                                    key="approve"
                                    size="small"
                                    type="primary"
                                    icon={<CheckOutlined />}
                                    onClick={() => handleReviewJoin(req.id, 'approved')}
                                  >
                                    通过
                                  </Button>,
                                  <Button
                                    key="reject"
                                    size="small"
                                    danger
                                    icon={<CloseOutlined />}
                                    onClick={() => handleReviewJoin(req.id, 'rejected')}
                                  >
                                    拒绝
                                  </Button>,
                                ]
                              : undefined
                          }
                        >
                          <List.Item.Meta
                            title={req.username}
                            description={req.message || new Date(req.created_at).toLocaleString()}
                          />
                          <Tag
                            color={
                              req.status === 'pending'
                                ? 'blue'
                                : req.status === 'approved'
                                  ? 'green'
                                  : 'red'
                            }
                          >
                            {req.status === 'pending'
                              ? '待审核'
                              : req.status === 'approved'
                                ? '已通过'
                                : '已拒绝'}
                          </Tag>
                        </List.Item>
                      )}
                    />
                  ),
                },
              ]
            : []),
        ]}
      />

      <Modal
        title="编辑空间"
        open={editOpen}
        onOk={handleEdit}
        onCancel={() => setEditOpen(false)}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          <div>
            <Typography.Text strong>空间名称</Typography.Text>
            <Input
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              maxLength={100}
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Typography.Text strong>描述</Typography.Text>
            <Input.TextArea
              value={editDesc}
              onChange={(e) => setEditDesc(e.target.value)}
              rows={3}
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Typography.Text strong>可见性</Typography.Text>
            <Radio.Group
              value={editVisibility}
              onChange={(e) => setEditVisibility(e.target.value)}
              style={{ marginTop: 4, display: 'block' }}
            >
              <Radio.Button value="private">
                <LockOutlined /> 私有
              </Radio.Button>
              <Radio.Button value="public">
                <GlobalOutlined /> 公开
              </Radio.Button>
            </Radio.Group>
          </div>
        </Space>
      </Modal>

      <Modal
        title="邀请成员"
        open={inviteOpen}
        onOk={handleInvite}
        onCancel={() => {
          setInviteOpen(false);
          setInviteUserId('');
        }}
        okText="邀请"
        cancelText="取消"
        okButtonProps={{ disabled: !inviteUserId }}
      >
        <Select
          showSearch
          value={inviteUserId || undefined}
          placeholder="搜索用户..."
          filterOption={false}
          onSearch={handleSearchUser}
          onChange={(v) => setInviteUserId(v)}
          loading={searchLoading}
          style={{ width: '100%' }}
          options={searchUsers.map((u: any) => ({
            value: u.id,
            label: `${u.username} (${u.email})`,
          }))}
        />
      </Modal>
    </div>
  );
}
