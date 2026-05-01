import { useEffect, useState, useCallback } from 'react';
import { Form, Input, Select, Button, List, Tag, Typography, App, Space, Popconfirm } from 'antd';
import {
  PlusOutlined,
  ThunderboltOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

const { Text } = Typography;

interface ModelProvider {
  id: string;
  name: string;
  provider_type: string;
  model_name: string;
  model_type: string;
  is_active: boolean;
}

export default function Step1ModelConfig({
  onModelCountChange,
}: {
  onModelCountChange: (n: number) => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);

  const fetchProviders = useCallback(async () => {
    try {
      const res = await api.get('/model-providers');
      const list = (res.data || []) as ModelProvider[];
      setProviders(list);
      onModelCountChange(list.filter((p) => p.is_active).length);
    } catch {
      // silent
    }
  }, [onModelCountChange]);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  const handleAdd = async () => {
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
        is_active: true,
        is_default: providers.length === 0,
        priority: 0,
        config: {},
      };
      await api.post('/model-providers', payload);
      message.success('模型已添加');
      form.resetFields();
      fetchProviders();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        '添加失败';
      message.error(detail);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.delete(`/model-providers/${id}`);
      message.success('已删除');
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

  return (
    <div>
      <Form form={form} layout="vertical">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="例如：DeepSeek V4" />
          </Form.Item>

          <Form.Item name="provider_type" label="提供商类型" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'openai', label: 'OpenAI 兼容' },
                { value: 'anthropic', label: 'Anthropic' },
              ]}
            />
          </Form.Item>

          <Form.Item
            name="model_name"
            label="模型名称"
            rules={[{ required: true, message: '必填' }]}
          >
            <Input placeholder="例如：deepseek-v4-flash" />
          </Form.Item>

          <Form.Item name="model_type" label="模型类型" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'llm', label: 'LLM' },
                { value: 'multimodal', label: '多模态' },
                { value: 'embedding', label: '嵌入' },
              ]}
            />
          </Form.Item>

          <Form.Item name="api_key" label="API 密钥" rules={[{ required: true, message: '必填' }]}>
            <Input.Password placeholder="sk-..." />
          </Form.Item>

          <Form.Item name="base_url" label="接口地址">
            <Input placeholder="https://api.openai.com/v1（留空使用默认）" />
          </Form.Item>
        </div>

        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={handleAdd}
          loading={saving}
          block
          style={{ borderRadius: 8 }}
        >
          添加模型
        </Button>
      </Form>

      {providers.length > 0 && (
        <List
          style={{ marginTop: 16 }}
          size="small"
          dataSource={providers}
          renderItem={(p) => (
            <List.Item
              actions={[
                <Button
                  key="test"
                  type="text"
                  size="small"
                  icon={<ThunderboltOutlined />}
                  loading={testing === p.id}
                  onClick={() => handleTest(p.id)}
                />,
                <Popconfirm
                  key="del"
                  title="确认删除？"
                  onConfirm={() => handleDelete(p.id)}
                  okText="删除"
                  okButtonProps={{ danger: true }}
                >
                  <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space size={4}>
                    <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 12 }} />
                    <Text strong>{p.name}</Text>
                    <Tag style={{ fontSize: 11 }}>{p.provider_type}</Tag>
                  </Space>
                }
                description={
                  <Text type="secondary" code style={{ fontSize: 11 }}>
                    {p.model_name}
                  </Text>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );
}
