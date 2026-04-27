import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Button,
  Modal,
  Form,
  Input,
  Select,
  Space,
  Typography,
  Tag,
  Popconfirm,
  App,
  Empty,
  theme,
} from 'antd';
import { PlusOutlined, DeleteOutlined, SendOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface Channel {
  id: string;
  name: string;
  type: string;
  config: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
}

export default function ChannelsPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();
  const [items, setItems] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetch = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/channels');
      setItems(res.data ?? []);
    } catch {
      msg.error('加载失败');
    } finally {
      setLoading(false);
    }
  }, [msg]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  const handleCreate = async (values: Channel) => {
    try {
      await api.post('/channels', values);
      msg.success('创建成功');
      setOpen(false);
      form.resetFields();
      fetch();
    } catch {
      msg.error('创建失败');
    }
  };

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (v: string) => (
        <Space>
          <SendOutlined style={{ color: token.colorPrimary }} />
          <span style={{ fontWeight: 500 }}>{v}</span>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 120,
      render: (v: string) => <Tag style={{ borderRadius: 4 }}>{v}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (v: boolean) => (
        <Tag color={v ? 'success' : 'default'} style={{ borderRadius: 4 }}>
          {v ? '启用' : '停用'}
        </Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string) => new Date(v).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      width: 60,
      render: (_: unknown, r: Channel) => (
        <Popconfirm
          title="确定删除？"
          onConfirm={async () => {
            try {
              await api.delete(`/channels/${r.id}`);
              fetch();
              msg.success('已删除');
            } catch {
              msg.error('删除失败');
            }
          }}
        >
          <Button type="text" danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
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
          消息渠道
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          添加渠道
        </Button>
      </div>
      <Card style={{ borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table
          dataSource={items}
          columns={columns}
          rowKey="id"
          pagination={false}
          size="middle"
          loading={loading}
          locale={{ emptyText: <Empty description="暂无消息渠道" /> }}
        />
      </Card>
      <Modal
        title="添加消息渠道"
        open={open}
        onCancel={() => {
          setOpen(false);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        okText="添加"
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="渠道名称" />
          </Form.Item>
          <Form.Item name="type" label="类型" rules={[{ required: true }]} initialValue="webhook">
            <Select
              options={[
                { value: 'webhook', label: 'Webhook' },
                { value: 'email', label: '邮件' },
                { value: 'sms', label: '短信' },
              ]}
            />
          </Form.Item>
          <Form.Item name="config" label="配置 (JSON)">
            <Input.TextArea rows={3} placeholder='{"url": "https://..."}' />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
