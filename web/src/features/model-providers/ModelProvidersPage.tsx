import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Button,
  Drawer,
  Form,
  Input,
  Select,
  InputNumber,
  Switch,
  Space,
  Tag,
  Typography,
  Popconfirm,
  App,
  Tooltip,
} from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ThunderboltOutlined,
  StarOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

const { Text } = Typography;

const MODEL_TYPE_OPTIONS = [
  { value: 'llm', label: 'LLM' },
  { value: 'multimodal', label: '多模态' },
  { value: 'voice', label: '语音' },
  { value: 'embedding', label: '嵌入' },
  { value: 'rerank', label: '重排' },
];

const MODEL_TYPE_COLORS: Record<string, string> = {
  llm: 'blue',
  multimodal: 'purple',
  voice: 'orange',
  embedding: 'green',
  rerank: 'cyan',
};

interface ModelProvider {
  id: string;
  name: string;
  provider_type: string;
  api_key: string;
  base_url: string | null;
  model_name: string;
  model_type: string;
  is_active: boolean;
  is_default: boolean;
  priority: number;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export default function ModelProvidersPage() {
  const { message } = App.useApp();
  const [form] = Form.useForm();

  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [loading, setLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);

  const fetchProviders = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/model-providers');
      setProviders(res.data || []);
    } catch {
      message.error('加载模型提供商失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  const openCreate = () => {
    setEditingId(null);
    form.resetFields();
    form.setFieldsValue({
      model_type: 'llm',
      is_active: true,
      is_default: false,
      priority: 0,
      config: {},
    });
    setDrawerOpen(true);
  };

  const openEdit = (p: ModelProvider) => {
    setEditingId(p.id);
    form.setFieldsValue({
      name: p.name,
      provider_type: p.provider_type,
      api_key: p.api_key,
      base_url: p.base_url || '',
      model_name: p.model_name,
      model_type: p.model_type,
      is_active: p.is_active,
      is_default: p.is_default,
      priority: p.priority,
      temperature: p.config?.temperature,
      max_tokens: p.config?.max_tokens,
      timeout: p.config?.timeout,
    });
    setDrawerOpen(true);
  };

  const handleSave = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const payload = {
        name: values.name,
        provider_type: values.provider_type,
        api_key: values.api_key,
        base_url: values.base_url || null,
        model_name: values.model_name,
        model_type: values.model_type,
        is_active: values.is_active,
        is_default: values.is_default,
        priority: values.priority,
        config: {
          temperature: values.temperature,
          max_tokens: values.max_tokens,
          timeout: values.timeout,
        },
      };
      if (editingId) {
        await api.patch(`/model-providers/${editingId}`, payload);
        message.success('提供商已更新');
      } else {
        await api.post('/model-providers', payload);
        message.success('提供商已创建');
      }
      setDrawerOpen(false);
      fetchProviders();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        '保存失败';
      message.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/model-providers/${id}`);
      message.success('提供商已删除');
      fetchProviders();
    } catch {
      message.error('删除失败');
    }
  };

  const handleTest = async (id: string) => {
    setTesting(id);
    try {
      const res = await api.post(`/model-providers/${id}/test`);
      if (res.data.ok) {
        message.success(`连接正常 — ${res.data.latency_ms}ms`);
      } else {
        message.error(res.data.message);
      }
    } catch {
      message.error('测试失败');
    } finally {
      setTesting(null);
    }
  };

  const handleSetDefault = async (id: string) => {
    try {
      await api.post(`/model-providers/${id}/set-default`);
      message.success('已设为默认');
      fetchProviders();
    } catch {
      message.error('设置默认失败');
    }
  };

  const providerTypeLabel = useCallback(
    (p: ModelProvider) => (
      <Tag
        color={
          p.provider_type === 'anthropic'
            ? 'purple'
            : p.provider_type === 'openai'
              ? 'blue'
              : 'default'
        }
        icon={<ApiOutlined />}
      >
        {p.provider_type}
      </Tag>
    ),
    [],
  );

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: ModelProvider) => (
        <Space>
          {name}
          {record.is_default && (
            <Tag color="gold" style={{ fontSize: 11 }}>
              默认
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: '提供商',
      dataIndex: 'provider_type',
      key: 'provider_type',
      width: 100,
      render: (_: string, record: ModelProvider) => providerTypeLabel(record),
    },
    {
      title: '模型类型',
      dataIndex: 'model_type',
      key: 'model_type',
      width: 90,
      render: (mt: string) => (
        <Tag color={MODEL_TYPE_COLORS[mt] || 'default'}>
          {MODEL_TYPE_OPTIONS.find((o) => o.value === mt)?.label || mt}
        </Tag>
      ),
    },
    {
      title: '模型',
      dataIndex: 'model_name',
      key: 'model_name',
      render: (m: string) => (
        <Text code style={{ fontSize: 12 }}>
          {m}
        </Text>
      ),
    },
    {
      title: '接口地址',
      dataIndex: 'base_url',
      key: 'base_url',
      width: 200,
      render: (url: string | null) =>
        url ? (
          <Text ellipsis={{ tooltip: url }} style={{ fontSize: 12, maxWidth: 180 }}>
            {url}
          </Text>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 70,
      render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '开' : '关'}</Tag>,
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 70,
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_: unknown, record: ModelProvider) => (
        <Space size="small">
          {!record.is_default && (
            <Tooltip title="设为默认">
              <Button
                size="small"
                type="text"
                icon={<StarOutlined />}
                onClick={() => handleSetDefault(record.id)}
              />
            </Tooltip>
          )}
          <Button
            size="small"
            type="text"
            icon={<ThunderboltOutlined />}
            onClick={() => handleTest(record.id)}
            loading={testing === record.id}
          >
            测试
          </Button>
          <Button
            size="small"
            type="text"
            icon={<EditOutlined />}
            onClick={() => openEdit(record)}
          />
          <Popconfirm
            title="确认删除此提供商？"
            onConfirm={() => handleDelete(record.id)}
            okText="删除"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
        }}
      >
        <Typography.Title level={4} style={{ margin: 0 }}>
          <ApiOutlined style={{ marginRight: 8 }} />
          模型配置
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          添加提供商
        </Button>
      </div>

      <Card>
        <Table
          dataSource={providers}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={false}
          size="middle"
        />
      </Card>

      <Drawer
        title={editingId ? '编辑提供商' : '添加提供商'}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={520}
        extra={
          <Button type="primary" onClick={handleSave} loading={saving}>
            保存
          </Button>
        }
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={{ config: {} }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="例如：DeepSeek V4, Claude Opus" />
          </Form.Item>

          <Form.Item name="provider_type" label="提供商类型" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'openai', label: 'OpenAI 兼容' },
                { value: 'anthropic', label: 'Anthropic' },
              ]}
            />
          </Form.Item>

          <Form.Item name="model_type" label="模型类型" rules={[{ required: true }]}>
            <Select options={MODEL_TYPE_OPTIONS} />
          </Form.Item>

          <Form.Item name="api_key" label="API 密钥" rules={[{ required: true, message: '必填' }]}>
            <Input.Password placeholder="sk-..." />
          </Form.Item>

          <Form.Item name="base_url" label="接口地址" extra="留空使用官方 API 端点">
            <Input placeholder="https://api.openai.com/v1" />
          </Form.Item>

          <Form.Item
            name="model_name"
            label="模型名称"
            rules={[{ required: true, message: '必填' }]}
          >
            <Input placeholder="例如：deepseek-v4-flash, claude-opus-4-7" />
          </Form.Item>

          <Space style={{ marginBottom: 24 }} size="large">
            <Form.Item
              name="is_active"
              label="激活"
              valuePropName="checked"
              style={{ marginBottom: 0 }}
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="is_default"
              label="默认"
              valuePropName="checked"
              style={{ marginBottom: 0 }}
            >
              <Switch />
            </Form.Item>
            <Form.Item name="priority" label="优先级" style={{ marginBottom: 0 }}>
              <InputNumber min={0} max={100} style={{ width: 80 }} />
            </Form.Item>
          </Space>

          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
            高级配置（JSON 存储，可选覆盖）
          </Typography.Text>

          <Form.Item name="temperature" label="温度">
            <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item name="max_tokens" label="最大 Token">
            <InputNumber min={1} max={200000} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item name="timeout" label="超时（秒）">
            <InputNumber min={1} max={300} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}
