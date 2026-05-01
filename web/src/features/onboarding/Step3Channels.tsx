import { useEffect, useState, useCallback } from 'react';
import {
  Form,
  Input,
  Button,
  List,
  Tag,
  Typography,
  App,
  Space,
  Popconfirm,
  Switch,
  Modal,
  Tabs,
} from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  DingtalkOutlined,
  WechatWorkOutlined,
  LinkOutlined,
  MailOutlined,
  ApiOutlined,
  SendOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

const { Text } = Typography;

interface Channel {
  id: string;
  name: string;
  channel_type: string;
  config: Record<string, unknown>;
  is_active: boolean;
}

const CHANNEL_TYPES: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  dingtalk: { icon: <DingtalkOutlined />, label: '钉钉', color: '#1677ff' },
  wecom: { icon: <WechatWorkOutlined />, label: '企业微信', color: '#07c160' },
  webhook: { icon: <LinkOutlined />, label: 'Webhook', color: '#722ed1' },
  email: { icon: <MailOutlined />, label: '邮件', color: '#1677ff' },
  custom_api: { icon: <ApiOutlined />, label: '自定义API', color: '#722ed1' },
};

const QUICK_FIELDS: Record<
  string,
  { key: string; label: string; placeholder: string; type?: string }[]
> = {
  dingtalk: [
    {
      key: 'webhook_url',
      label: 'Webhook URL',
      placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=...',
    },
    { key: 'secret', label: '加签密钥 (可选)', placeholder: 'SEC...' },
  ],
  wecom: [
    {
      key: 'webhook_url',
      label: 'Webhook URL',
      placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...',
    },
  ],
  webhook: [
    { key: 'url', label: 'URL', placeholder: 'https://your-server.com/callback' },
    { key: 'secret', label: '签名密钥 (可选)', placeholder: 'HMAC-SHA256 密钥' },
  ],
  email: [
    { key: 'smtp_host', label: 'SMTP 主机', placeholder: 'smtp.example.com' },
    { key: 'smtp_port', label: '端口', placeholder: '587' },
    { key: 'smtp_username', label: '用户名', placeholder: 'user@example.com' },
    { key: 'smtp_password', label: '密码', placeholder: 'SMTP 授权码', type: 'password' },
    { key: 'from_email', label: '发件人邮箱', placeholder: 'noreply@example.com' },
    { key: 'from_name', label: '发件人名称', placeholder: 'AIOpsOS 告警' },
  ],
  custom_api: [{ key: 'url', label: '请求 URL', placeholder: 'https://your-api.com/notify' }],
};

export default function Step3Channels() {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [channelType, setChannelType] = useState('dingtalk');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);

  const fetchChannels = useCallback(async () => {
    try {
      const res = await api.get('/channels');
      setChannels(res.data ?? []);
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    fetchChannels();
  }, [fetchChannels]);

  const handleAdd = async (values: Record<string, unknown>) => {
    const config: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(values)) {
      if (v !== undefined && v !== null && v !== '' && k !== 'name' && k !== 'is_active') {
        const num = Number(v);
        config[k] =
          isNaN(num) || k === 'webhook_url' || k === 'url' || k === 'secret' || k === 'from_name'
            ? v
            : num;
      }
    }

    setSaving(true);
    try {
      await api.post('/channels', {
        name: values.name,
        channel_type: channelType,
        config,
        is_active: values.is_active ?? true,
      });
      message.success('渠道已添加');
      setModalOpen(false);
      form.resetFields();
      fetchChannels();
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
      await api.delete(`/channels/${id}`);
      message.success('已删除');
      fetchChannels();
    } catch {
      message.error('删除失败');
    }
  };

  const handleTest = async (ch: Channel) => {
    setTesting(ch.id);
    try {
      const res = await api.post('/channels/test', {
        channel_type: ch.channel_type,
        config: ch.config,
      });
      if (res.data?.ok) {
        message.success(res.data.message || '测试发送成功');
      } else {
        message.error(res.data?.message || '测试发送失败');
      }
    } catch {
      message.error('测试请求失败');
    } finally {
      setTesting(null);
    }
  };

  const fields = QUICK_FIELDS[channelType] || [];

  return (
    <div>
      <div style={{ textAlign: 'center', paddingTop: 16, marginBottom: 20 }}>
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: 14,
            background: 'rgba(37,99,235,0.1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 16px',
            fontSize: 28,
            color: '#2563eb',
          }}
        >
          <SendOutlined />
        </div>
        <Typography.Title level={5} style={{ marginBottom: 8 }}>
          消息通知渠道
        </Typography.Title>
        <Text type="secondary" style={{ fontSize: 13 }}>
          配置告警通知渠道，将平台告警推送到钉钉、企微等协作工具
        </Text>
      </div>

      {channels.length > 0 && (
        <List
          size="small"
          style={{ marginBottom: 16 }}
          dataSource={channels}
          renderItem={(ch) => {
            const info = CHANNEL_TYPES[ch.channel_type] || CHANNEL_TYPES.webhook;
            return (
              <List.Item
                actions={[
                  <Button
                    key="test"
                    type="text"
                    size="small"
                    icon={<ExperimentOutlined />}
                    loading={testing === ch.id}
                    onClick={() => handleTest(ch)}
                  />,
                  <Popconfirm
                    key="del"
                    title="确认删除？"
                    onConfirm={() => handleDelete(ch.id)}
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
                      <span style={{ color: info.color }}>{info.icon}</span>
                      <Text strong>{ch.name}</Text>
                      <Tag color={info.color} style={{ fontSize: 11, borderRadius: 4 }}>
                        {info.label}
                      </Tag>
                    </Space>
                  }
                />
              </List.Item>
            );
          }}
        />
      )}

      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={() => setModalOpen(true)}
        block
        style={{ borderRadius: 8 }}
      >
        添加渠道
      </Button>

      <Modal
        title="添加通知渠道"
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        okText="添加"
        confirmLoading={saving}
        destroyOnHidden
        width={520}
      >
        <Tabs
          activeKey={channelType}
          onChange={(key) => {
            setChannelType(key);
            form.resetFields();
            form.setFieldsValue({ is_active: true });
          }}
          items={Object.entries(CHANNEL_TYPES).map(([key, info]) => ({
            key,
            label: (
              <Space>
                <span style={{ color: info.color }}>{info.icon}</span>
                {info.label}
              </Space>
            ),
          }))}
          style={{ marginBottom: 16 }}
        />
        <Form form={form} layout="vertical" onFinish={handleAdd}>
          <Form.Item
            name="name"
            label="渠道名称"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="例如：运维告警通知" />
          </Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>
          {fields.map((f) =>
            f.type === 'password' ? (
              <Form.Item
                key={f.key}
                name={f.key}
                label={f.label}
                rules={[{ required: true, message: `请输入${f.label}` }]}
              >
                <Input.Password placeholder={f.placeholder} />
              </Form.Item>
            ) : (
              <Form.Item
                key={f.key}
                name={f.key}
                label={f.label}
                rules={[{ required: true, message: `请输入${f.label}` }]}
              >
                <Input placeholder={f.placeholder} />
              </Form.Item>
            ),
          )}
        </Form>
      </Modal>
    </div>
  );
}
