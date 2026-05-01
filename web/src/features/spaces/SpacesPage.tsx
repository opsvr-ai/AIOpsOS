import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Button,
  Space,
  Typography,
  Tag,
  App,
  Empty,
  Spin,
  Row,
  Col,
  Tabs,
  Modal,
  Input,
  Radio,
  List,
  theme,
} from 'antd';
import {
  PlusOutlined,
  TeamOutlined,
  LockOutlined,
  GlobalOutlined,
  LoginOutlined,
  CheckOutlined,
  CloseOutlined,
  MailOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import api from '@/services/api';
import { useSpaceStore } from '@/stores/spaceStore';

interface SpaceData {
  id: string;
  name: string;
  description: string | null;
  visibility: string;
  created_by: string;
  member_count: number;
  created_at: string;
}

export default function SpacesPage() {
  const navigate = useNavigate();
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();

  const [mySpaces, setMySpaces] = useState<SpaceData[]>([]);
  const [discoverSpaces, setDiscoverSpaces] = useState<SpaceData[]>([]);
  const [loading, setLoading] = useState(true);
  const [discoverLoading, setDiscoverLoading] = useState(false);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createDesc, setCreateDesc] = useState('');
  const [createVisibility, setCreateVisibility] = useState('private');
  const [creating, setCreating] = useState(false);
  const [pendingInvites, setPendingInvites] = useState<any[]>([]);
  const [pendingLoading, setPendingLoading] = useState(false);

  const fetchMySpaces = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/spaces');
      setMySpaces(res.data ?? []);
    } catch {
      msg.error('加载空间列表失败');
    } finally {
      setLoading(false);
    }
  }, [msg]);

  const fetchDiscover = useCallback(async (keyword?: string) => {
    setDiscoverLoading(true);
    try {
      const params = keyword ? { keyword } : {};
      const res = await api.get('/spaces/discover', { params });
      setDiscoverSpaces(res.data ?? []);
    } catch {
      /* ignore */
    } finally {
      setDiscoverLoading(false);
    }
  }, []);

  const fetchPendingInvites = useCallback(async () => {
    setPendingLoading(true);
    try {
      const res = await api.get('/spaces/invitations/pending');
      setPendingInvites(res.data ?? []);
    } catch {
      /* ignore */
    } finally {
      setPendingLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMySpaces();
    fetchDiscover();
    fetchPendingInvites();
  }, [fetchMySpaces, fetchDiscover, fetchPendingInvites]);

  const handleRespondInvite = async (inviteId: string, accept: boolean) => {
    try {
      await api.post(`/spaces/invitations/${inviteId}/respond`, { accept });
      msg.success(accept ? '已接受邀请' : '已拒绝邀请');
      fetchPendingInvites();
      if (accept) {
        useSpaceStore.getState().bumpSpaceVersion();
        fetchMySpaces();
      }
    } catch {
      msg.error('操作失败');
    }
  };

  const handleSearch = (kw: string) => {
    setSearchKeyword(kw);
    fetchDiscover(kw);
  };

  const handleCreate = async () => {
    if (!createName.trim()) return;
    setCreating(true);
    try {
      await api.post('/spaces', {
        name: createName.trim(),
        description: createDesc.trim() || null,
        visibility: createVisibility,
      });
      msg.success('空间已创建');
      setCreateOpen(false);
      setCreateName('');
      setCreateDesc('');
      setCreateVisibility('private');
      useSpaceStore.getState().bumpSpaceVersion();
      fetchMySpaces();
    } catch {
      msg.error('创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleJoinRequest = async (spaceId: string) => {
    try {
      await api.post(`/spaces/${spaceId}/join-request`, { message: '' });
      msg.success('已发送加入申请');
    } catch (e: any) {
      msg.error(e?.response?.data?.detail ?? '申请失败');
    }
  };

  const renderSpaceCard = (space: SpaceData, showJoin: boolean) => (
    <Col xs={24} sm={12} lg={8} key={space.id}>
      <Card
        hoverable
        style={{ borderRadius: 12, height: '100%' }}
        onClick={() => navigate(`/spaces/${space.id}`)}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <Space direction="vertical" size={4} style={{ flex: 1 }}>
            <Space size={6}>
              <Typography.Text strong style={{ fontSize: 15 }}>
                {space.name}
              </Typography.Text>
              <Tag
                color={space.visibility === 'public' ? 'green' : 'default'}
                style={{ borderRadius: 4, fontSize: 11 }}
                icon={space.visibility === 'public' ? <GlobalOutlined /> : <LockOutlined />}
              >
                {space.visibility === 'public' ? '公开' : '私有'}
              </Tag>
            </Space>
            <Typography.Paragraph
              type="secondary"
              ellipsis={{ rows: 2 }}
              style={{ marginBottom: 0, fontSize: 13 }}
            >
              {space.description || '暂无描述'}
            </Typography.Paragraph>
          </Space>
          {showJoin && (
            <Button
              type="primary"
              size="small"
              icon={<LoginOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                handleJoinRequest(space.id);
              }}
            >
              申请加入
            </Button>
          )}
        </div>
        <div style={{ marginTop: 12 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            <TeamOutlined style={{ marginRight: 4 }} />
            {space.member_count} 位成员
          </Typography.Text>
        </div>
      </Card>
    </Col>
  );

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
          空间管理
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          创建空间
        </Button>
      </div>

      <Tabs
        defaultActiveKey="my"
        type="card"
        items={[
          {
            key: 'my',
            label: '我的空间',
            children: loading ? (
              <div style={{ textAlign: 'center', padding: 80 }}>
                <Spin size="large" />
              </div>
            ) : mySpaces.length === 0 ? (
              <Card style={{ borderRadius: 12, textAlign: 'center', padding: 60 }}>
                <Empty description="暂无空间">
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => setCreateOpen(true)}
                  >
                    创建第一个空间
                  </Button>
                </Empty>
              </Card>
            ) : (
              <Row gutter={[16, 16]}>{mySpaces.map((s) => renderSpaceCard(s, false))}</Row>
            ),
          },
          {
            key: 'invitations',
            label: (
              <span>
                待处理邀请
                {pendingInvites.length > 0 && (
                  <span
                    style={{
                      marginLeft: 6,
                      background: token?.colorError || '#ff4d4f',
                      color: '#fff',
                      borderRadius: 10,
                      padding: '0 6px',
                      fontSize: 11,
                    }}
                  >
                    {pendingInvites.length}
                  </span>
                )}
              </span>
            ),
            children: pendingLoading ? (
              <div style={{ textAlign: 'center', padding: 60 }}>
                <Spin />
              </div>
            ) : pendingInvites.length === 0 ? (
              <Card style={{ borderRadius: 12, textAlign: 'center', padding: 60 }}>
                <Empty description="暂无待处理邀请" />
              </Card>
            ) : (
              <List
                dataSource={pendingInvites}
                renderItem={(inv: any) => (
                  <List.Item
                    actions={[
                      <Button
                        key="accept"
                        type="primary"
                        size="small"
                        icon={<CheckOutlined />}
                        onClick={() => handleRespondInvite(inv.id, true)}
                      >
                        接受
                      </Button>,
                      <Button
                        key="reject"
                        size="small"
                        danger
                        icon={<CloseOutlined />}
                        onClick={() => handleRespondInvite(inv.id, false)}
                      >
                        拒绝
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Space size={4}>
                          <MailOutlined />
                          <Typography.Text strong>{inv.space_name}</Typography.Text>
                        </Space>
                      }
                      description={
                        <Typography.Text type="secondary">
                          {inv.inviter_name || '系统'} 邀请你加入该空间
                        </Typography.Text>
                      }
                    />
                  </List.Item>
                )}
              />
            ),
          },
          {
            key: 'discover',
            label: '发现空间',
            children: (
              <>
                <Input.Search
                  placeholder="搜索公开空间..."
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  onSearch={handleSearch}
                  style={{ marginBottom: 16, maxWidth: 400 }}
                  allowClear
                />
                {discoverLoading ? (
                  <div style={{ textAlign: 'center', padding: 60 }}>
                    <Spin />
                  </div>
                ) : discoverSpaces.length === 0 ? (
                  <Card style={{ borderRadius: 12, textAlign: 'center', padding: 60 }}>
                    <Empty description="暂无公开空间" />
                  </Card>
                ) : (
                  <Row gutter={[16, 16]}>{discoverSpaces.map((s) => renderSpaceCard(s, true))}</Row>
                )}
              </>
            ),
          },
        ]}
      />

      <Modal
        title="创建空间"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => setCreateOpen(false)}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          <div>
            <Typography.Text strong>空间名称</Typography.Text>
            <Input
              placeholder="输入空间名称"
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              maxLength={100}
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Typography.Text strong>描述</Typography.Text>
            <Input.TextArea
              placeholder="空间描述（可选）"
              value={createDesc}
              onChange={(e) => setCreateDesc(e.target.value)}
              rows={3}
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Typography.Text strong>可见性</Typography.Text>
            <Radio.Group
              value={createVisibility}
              onChange={(e) => setCreateVisibility(e.target.value)}
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
    </div>
  );
}
