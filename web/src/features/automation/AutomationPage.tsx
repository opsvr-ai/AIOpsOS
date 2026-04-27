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
  Switch,
  App,
  Empty,
  theme,
} from 'antd';
import { PlusOutlined, DeleteOutlined, ThunderboltOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface Schedule {
  id: string;
  name: string;
  cron_expr: string;
  task_type: string;
  is_active: boolean;
  created_at: string;
}

export default function AutomationPage() {
  const { message: msg } = App.useApp();
  const { token } = theme.useToken();
  const [items, setItems] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const fetch = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/schedules');
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

  const handleCreate = async (values: Schedule) => {
    try {
      await api.post('/schedules', values);
      msg.success('创建成功');
      setOpen(false);
      form.resetFields();
      fetch();
    } catch {
      msg.error('创建失败');
    }
  };

  const handleToggle = async (id: string, active: boolean) => {
    try {
      await api.patch(`/schedules/${id}`, { is_active: active });
      fetch();
    } catch {
      msg.error('操作失败');
    }
  };

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (v: string) => (
        <Space>
          <ThunderboltOutlined style={{ color: token.colorPrimary }} />
          <span style={{ fontWeight: 500 }}>{v}</span>
        </Space>
      ),
    },
    {
      title: 'Cron',
      dataIndex: 'cron_expr',
      key: 'cron_expr',
      width: 140,
      render: (v: string) => <Tag style={{ borderRadius: 4, fontFamily: 'monospace' }}>{v}</Tag>,
    },
    { title: '类型', dataIndex: 'task_type', key: 'task_type', width: 100 },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (v: boolean, r: Schedule) => (
        <Switch size="small" checked={v} onChange={(val) => handleToggle(r.id, val)} />
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
      render: (_: unknown, r: Schedule) => (
        <Popconfirm
          title="确定删除？"
          onConfirm={async () => {
            try {
              await api.delete(`/schedules/${r.id}`);
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
          自动化
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          创建任务
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
          locale={{ emptyText: <Empty description="暂无自动化任务" /> }}
        />
      </Card>
      <Modal
        title="创建自动化任务"
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
            <Input placeholder="任务名称" />
          </Form.Item>
          <Form.Item name="cron_expr" label="Cron 表达式" rules={[{ required: true }]}>
            <Input placeholder="*/5 * * * *" />
          </Form.Item>
          <Form.Item
            name="task_type"
            label="任务类型"
            rules={[{ required: true }]}
            initialValue="script"
          >
            <Select
              options={[
                { value: 'script', label: '脚本' },
                { value: 'api', label: 'API调用' },
                { value: 'workflow', label: '工作流' },
              ]}
            />
          </Form.Item>
          <Form.Item name="config" label="配置 (JSON)">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
