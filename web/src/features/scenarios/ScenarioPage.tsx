import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Button,
  Modal,
  Form,
  Input,
  Space,
  Typography,
  Tag,
  Popconfirm,
  App,
  Empty,
  theme,
} from 'antd';
import { PlusOutlined, DeleteOutlined, ExperimentOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface Scenario {
  id: string;
  name: string;
  description: string | null;
  trigger_command: string;
  is_active: boolean;
  created_at: string;
}

export default function ScenarioPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();
  const [items, setItems] = useState<Scenario[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetch = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/scenarios');
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

  const handleCreate = async (values: Scenario) => {
    try {
      await api.post('/scenarios', values);
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
          <ExperimentOutlined style={{ color: token.colorPrimary }} />
          <span style={{ fontWeight: 500 }}>{v}</span>
        </Space>
      ),
    },
    {
      title: '触发命令',
      dataIndex: 'trigger_command',
      key: 'trigger_command',
      width: 160,
      render: (v: string) => <Tag style={{ borderRadius: 4, fontFamily: 'monospace' }}>{v}</Tag>,
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
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
      render: (_: unknown, r: Scenario) => (
        <Popconfirm
          title="确定删除？"
          onConfirm={async () => {
            try {
              await api.delete(`/scenarios/${r.id}`);
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
          场景运维
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          创建场景
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
          locale={{ emptyText: <Empty description="暂无场景" /> }}
        />
      </Card>
      <Modal
        title="创建场景"
        open={open}
        onCancel={() => {
          setOpen(false);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        okText="创建"
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="场景名称" />
          </Form.Item>
          <Form.Item name="trigger_command" label="触发命令" rules={[{ required: true }]}>
            <Input placeholder="/command" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
