import { useEffect, useState, useCallback } from 'react';
import {
  Card,
  Table,
  Button,
  Modal,
  Form,
  Input,
  Switch,
  InputNumber,
  Space,
  Typography,
  Tag,
  Popconfirm,
  App,
  Empty,
  Tabs,
  Tooltip,
  Select,
} from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  DingtalkOutlined,
  WechatWorkOutlined,
  LinkOutlined,
  ExperimentOutlined,
  MailOutlined,
  ApiOutlined,
  StopOutlined,
  CaretRightOutlined,
  MessageOutlined,
  UsergroupAddOutlined,
  SendOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

interface Channel {
  id: string;
  name: string;
  channel_type: string;
  config: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
}

const CHANNEL_TYPES: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  dingtalk: { icon: <DingtalkOutlined />, label: '钉钉', color: '#1677ff' },
  wecom: { icon: <WechatWorkOutlined />, label: '企业微信', color: '#07c160' },
  webhook: { icon: <LinkOutlined />, label: 'Webhook', color: '#722ed1' },
  email: { icon: <MailOutlined />, label: '邮件', color: '#1677ff' },
  custom_api: { icon: <ApiOutlined />, label: '自定义API', color: '#722ed1' },
};

interface FieldDef {
  key: string;
  label: string;
  placeholder?: string;
  type?: 'input' | 'password' | 'select' | 'textarea' | 'number' | 'json' | 'switch';
  options?: { value: string; label: string }[];
  required?: boolean;
  section?: string;
  tooltip?: string;
}

const TEMPLATE_VAR_HINT = '可用变量: {title} {message} {severity} {alert_id} {timestamp} {source}';

const CHANNEL_CONFIG_FIELDS: Record<string, FieldDef[]> = {
  dingtalk: [
    {
      key: 'webhook_url',
      label: 'Webhook URL',
      placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=...',
      required: true,
    },
    { key: 'secret', label: '加签密钥 (可选)', placeholder: 'SEC...' },
  ],
  wecom: [
    {
      key: 'wecom_sub_type',
      label: '渠道类型',
      type: 'select',
      required: true,
      options: [
        { value: 'bot_webhook', label: '智能机器人 — Webhook' },
        { value: 'bot_websocket', label: '智能机器人 — WebSocket' },
        { value: 'app', label: '企业微信应用' },
      ],
    },
  ],
  webhook: [
    { key: 'url', label: 'URL', placeholder: 'https://your-server.com/callback', required: true },
    { key: 'secret', label: '签名密钥 (可选)', placeholder: '用于 HMAC-SHA256 签名' },
  ],
  email: [
    {
      key: 'smtp_host',
      label: 'SMTP 主机',
      placeholder: 'smtp.example.com',
      required: true,
      section: 'SMTP 服务器',
    },
    {
      key: 'smtp_port',
      label: '端口',
      placeholder: '25',
      type: 'number',
      required: true,
      section: 'SMTP 服务器',
    },
    {
      key: 'use_ssl',
      label: 'SSL 连接',
      type: 'switch',
      section: 'SMTP 服务器',
      tooltip: '端口 465 通常使用 SSL',
    },
    {
      key: 'use_tls',
      label: 'STARTTLS',
      type: 'switch',
      section: 'SMTP 服务器',
      tooltip: '端口 587 通常使用 STARTTLS，内网端口 25 通常不勾选',
    },
    {
      key: 'smtp_username',
      label: '用户名',
      placeholder: 'user@example.com',
      section: 'SMTP 服务器',
    },
    {
      key: 'smtp_password',
      label: '密码',
      placeholder: 'SMTP 授权码',
      type: 'password',
      section: 'SMTP 服务器',
    },
    {
      key: 'from_email',
      label: '发件人邮箱',
      placeholder: 'noreply@example.com',
      required: true,
      section: '发件人',
    },
    { key: 'from_name', label: '发件人名称', placeholder: 'AIOpsOS 告警', section: '发件人' },
    {
      key: 'test_recipient',
      label: '测试收件人',
      placeholder: '留空则发送到发件人邮箱',
      section: '测试',
      tooltip: '点击测试发送时的收件人地址',
    },
  ],
  custom_api: [
    {
      key: 'url',
      label: '请求 URL',
      placeholder: 'https://your-api.com/notify',
      required: true,
      section: '请求配置',
    },
    {
      key: 'method',
      label: '请求方法',
      type: 'select',
      options: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map((v) => ({ value: v, label: v })),
      section: '请求配置',
    },
    { key: 'timeout_seconds', label: '超时 (秒)', type: 'number', section: '请求配置' },
    {
      key: 'auth_type',
      label: '认证方式',
      type: 'select',
      options: [
        { value: 'none', label: '无' },
        { value: 'basic', label: 'Basic Auth' },
        { value: 'bearer', label: 'Bearer Token' },
        { value: 'api_key', label: 'API Key' },
      ],
      section: '认证配置',
    },
    {
      key: 'auth_config',
      label: '认证配置 (JSON)',
      type: 'json',
      placeholder: '{"token": "xxx"}',
      section: '认证配置',
    },
    {
      key: 'headers',
      label: '请求头 (JSON)',
      type: 'json',
      placeholder: '{"Content-Type": "application/json"}',
      section: '请求头与参数',
    },
    {
      key: 'query_params',
      label: '查询参数 (JSON)',
      type: 'json',
      placeholder: '{"source": "aiops"}',
      section: '请求头与参数',
    },
    {
      key: 'body_template',
      label: '请求体模板',
      type: 'textarea',
      placeholder: '{"title": "{title}", "content": "{message}"}',
      section: '请求体',
    },
    {
      key: 'body_content_type',
      label: 'Content-Type',
      placeholder: 'application/json',
      section: '请求体',
    },
    {
      key: 'success_condition_type',
      label: '成功判断方式',
      type: 'select',
      options: [
        { value: 'status_code', label: 'HTTP 状态码' },
        { value: 'json_field', label: 'JSON 字段' },
        { value: 'body_regex', label: '响应正则' },
      ],
      section: '成功判断',
    },
    {
      key: 'success_condition_value',
      label: '判断值',
      placeholder: '200-299',
      section: '成功判断',
    },
  ],
};

// Extra fields per WeCom sub-type (not in CHANNEL_CONFIG_FIELDS — rendered conditionally)
const WECOM_SUB_FIELDS: Record<string, FieldDef[]> = {
  bot_webhook: [
    {
      key: 'webhook_url',
      label: 'Webhook URL',
      placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...',
      required: true,
      section: '机器人配置',
    },
    {
      key: 'deployment_mode',
      label: '部署模式',
      type: 'select',
      required: true,
      options: [
        { value: 'cloud', label: '腾讯云官方 (默认)' },
        { value: 'private', label: '私有部署' },
      ],
      section: '部署设置',
    },
    {
      key: 'api_base_url',
      label: 'API 基础 URL',
      placeholder: 'https://your-wecom-domain.com',
      section: '部署设置',
    },
  ],
  bot_websocket: [
    {
      key: 'bot_id',
      label: 'BotID',
      placeholder: '智能机器人 BotID',
      required: true,
      section: '机器人配置',
    },
    {
      key: 'bot_secret',
      label: 'Secret',
      placeholder: '长连接专用密钥',
      type: 'password',
      required: true,
      section: '机器人配置',
    },
    {
      key: 'ws_api_base',
      label: 'WebSocket 地址 (可选)',
      placeholder: 'wss://openws.work.weixin.qq.com',
      section: '机器人配置',
    },
    {
      key: 'chatid',
      label: '会话 ID (可选)',
      placeholder: '单聊填 userid，群聊填 chatid',
      section: '发送配置',
    },
    {
      key: 'callback_token',
      label: '回调 Token (可选)',
      placeholder: '用于 Webhook 回调 URL 验证',
      type: 'password',
      section: 'Webhook 回调 (可选)',
    },
    {
      key: 'callback_encoding_aes_key',
      label: '回调 EncodingAESKey (可选)',
      placeholder: '43 位随机字符串',
      type: 'password',
      section: 'Webhook 回调 (可选)',
    },
    {
      key: 'callback_receive_id',
      label: '回调 ReceiveId (可选)',
      placeholder: '默认为 corp_id，留空自动推断',
      section: 'Webhook 回调 (可选)',
    },
  ],
  app: [
    {
      key: 'deployment_mode',
      label: '部署模式',
      type: 'select',
      required: true,
      options: [
        { value: 'cloud', label: '腾讯云官方 (默认)' },
        { value: 'private', label: '私有部署' },
      ],
      section: '部署设置',
    },
    {
      key: 'api_base_url',
      label: 'API 基础 URL',
      placeholder: 'https://your-wecom-domain.com',
      section: '部署设置',
    },
    {
      key: 'corp_id',
      label: '企业 ID (corp_id)',
      placeholder: 'ww...',
      required: true,
      section: '应用配置',
    },
    {
      key: 'corp_secret',
      label: '应用 Secret',
      placeholder: '密钥',
      type: 'password',
      required: true,
      section: '应用配置',
    },
    {
      key: 'agent_id',
      label: '应用 AgentId',
      placeholder: '1000002',
      required: true,
      section: '应用配置',
    },
    {
      key: 'msg_type',
      label: '消息类型',
      type: 'select',
      options: [
        { value: 'markdown', label: 'Markdown (推荐)' },
        { value: 'text', label: '纯文本' },
      ],
      section: '消息设置',
    },
  ],
};

export default function ChannelsPage() {
  const { message: msg } = App.useApp();
  const [items, setItems] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [channelType, setChannelType] = useState('dingtalk');
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

  // Monitor connection status for WeCom WebSocket channels
  const [monitorStatuses, setMonitorStatuses] = useState<Record<string, boolean>>({});
  const [monitorLoading, setMonitorLoading] = useState<Record<string, boolean>>({});

  const fetchMonitorStatus = useCallback(async () => {
    const wecomSockets = items.filter(
      (ch) =>
        ch.channel_type === 'wecom' &&
        (ch.config as Record<string, unknown>)?.wecom_sub_type === 'bot_websocket',
    );
    if (wecomSockets.length === 0) return;
    const statuses: Record<string, boolean> = {};
    for (const ch of wecomSockets) {
      try {
        const res = await api.get(`/channels/${ch.id}/monitor/status`);
        statuses[ch.id] = res.data?.connected ?? false;
      } catch {
        statuses[ch.id] = false;
      }
    }
    setMonitorStatuses((prev) => ({ ...prev, ...statuses }));
  }, [items]);

  useEffect(() => {
    if (items.length > 0) fetchMonitorStatus();
  }, [items, fetchMonitorStatus]);

  const handleMonitorAction = async (ch: Channel, action: 'start' | 'stop') => {
    setMonitorLoading((prev) => ({ ...prev, [ch.id]: true }));
    try {
      const res = await api.post(`/channels/${ch.id}/monitor/${action}`);
      if (res.data?.ok) {
        msg.success(action === 'start' ? '已启动' : '已停止');
        await fetchMonitorStatus();
      } else {
        msg.error(res.data?.message || '操作失败');
      }
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        '操作失败';
      msg.error(detail);
    } finally {
      setMonitorLoading((prev) => ({ ...prev, [ch.id]: false }));
    }
  };

  // ── App operations ────────────────────────────────────────────────────

  const [appModalOpen, setAppModalOpen] = useState(false);
  const [appModalType, setAppModalType] = useState<'send' | 'create_chat' | 'chat_send'>('send');
  const [appTargetId, setAppTargetId] = useState<string | null>(null);
  const [appLoading, setAppLoading] = useState(false);
  const [appForm] = Form.useForm();

  const openAppModal = (ch: Channel, type: 'send' | 'create_chat' | 'chat_send') => {
    setAppTargetId(ch.id);
    setAppModalType(type);
    appForm.resetFields();
    if (type === 'send') {
      appForm.setFieldsValue({ msgtype: 'text' });
    } else if (type === 'chat_send') {
      appForm.setFieldsValue({ msgtype: 'text' });
    }
    setAppModalOpen(true);
  };

  const handleAppAction = async (values: Record<string, unknown>) => {
    if (!appTargetId) return;
    setAppLoading(true);
    try {
      let res;
      if (appModalType === 'send') {
        res = await api.post(`/channels/${appTargetId}/app/send`, {
          msgtype: values.msgtype || 'text',
          content: values.content || '',
          touser: values.touser || '',
          toparty: values.toparty || '',
          totag: values.totag || '',
        });
      } else if (appModalType === 'create_chat') {
        const userlistStr = (values.userlist as string) || '';
        res = await api.post(`/channels/${appTargetId}/app/chat/create`, {
          name: values.name || '',
          owner: values.owner || '',
          userlist: userlistStr
            .split(',')
            .map((s: string) => s.trim())
            .filter(Boolean),
          chatid: values.chatid || '',
        });
      } else {
        res = await api.post(`/channels/${appTargetId}/app/chat/send`, {
          chatid: values.chatid || '',
          msgtype: values.msgtype || 'text',
          content: values.content || '',
        });
      }
      if (res.data?.ok) {
        msg.success(
          appModalType === 'create_chat'
            ? `群聊创建成功: ${res.data?.data?.chatid || ''}`
            : '发送成功',
        );
        setAppModalOpen(false);
      } else {
        msg.error(res.data?.data?.errmsg || '操作失败');
      }
    } catch (err: unknown) {
      msg.error(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
          '请求失败',
      );
    } finally {
      setAppLoading(false);
    }
  };

  const buildConfig = (values: Record<string, unknown>): Record<string, unknown> | null => {
    const config: Record<string, unknown> = {};
    const baseFields = CHANNEL_CONFIG_FIELDS[channelType] || [];

    let allFields = baseFields;
    if (channelType === 'wecom') {
      const subType = (values.wecom_sub_type as string) || 'bot_webhook';
      allFields = [...baseFields, ...(WECOM_SUB_FIELDS[subType] || [])];
    }

    for (const f of allFields) {
      const raw = values[f.key];
      if (raw === undefined || raw === null || raw === '') continue;

      if (f.type === 'json') {
        try {
          config[f.key] = JSON.parse(raw as string);
        } catch {
          msg.error(`${f.label}: 无效的 JSON 格式`);
          return null;
        }
      } else if (f.type === 'number') {
        config[f.key] = Number(raw);
      } else {
        config[f.key] = raw;
      }
    }

    if (channelType === 'custom_api') {
      const scType = values.success_condition_type;
      const scValue = values.success_condition_value;
      if (scType || scValue) {
        config.success_condition = {
          type: scType || 'status_code',
          value: scValue || '200-299',
        };
      }
      delete config.success_condition_type;
      delete config.success_condition_value;
    }

    return config;
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    const config = buildConfig(values);
    if (!config) return;

    try {
      if (editingId) {
        await api.patch(`/channels/${editingId}`, {
          name: values.name,
          config,
          is_active: values.is_active ?? true,
        });
        msg.success('更新成功');
      } else {
        await api.post('/channels', {
          name: values.name,
          channel_type: channelType,
          config,
          is_active: values.is_active ?? true,
        });
        msg.success('创建成功');
      }
      setOpen(false);
      setEditingId(null);
      form.resetFields();
      fetch();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (editingId ? '更新失败' : '创建失败');
      msg.error(detail);
    }
  };

  const openEdit = (ch: Channel) => {
    setEditingId(ch.id);
    setChannelType(ch.channel_type);
    const config = { ...ch.config };
    const sc = config.success_condition as { type?: string; value?: string } | undefined;
    if (sc && typeof sc === 'object') {
      delete config.success_condition;
      config.success_condition_type = sc.type || 'status_code';
      config.success_condition_value = sc.value || '200-299';
    }
    form.setFieldsValue({
      name: ch.name,
      is_active: ch.is_active,
      ...config,
    });
    setOpen(true);
  };

  const openCreate = () => {
    setEditingId(null);
    setChannelType('dingtalk');
    form.resetFields();
    form.setFieldsValue({ is_active: true });
    setOpen(true);
  };

  const handleTest = async (ch: Channel) => {
    setTesting(ch.id);
    try {
      const res = await api.post('/channels/test', {
        channel_type: ch.channel_type,
        config: ch.config,
      });
      if (res.data?.ok) {
        msg.success(res.data.message || '测试发送成功');
      } else {
        msg.error(res.data?.message || '测试发送失败');
      }
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { message?: string } } })?.response?.data?.message ||
        '测试请求失败';
      msg.error(detail);
    } finally {
      setTesting(null);
    }
  };

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (v: string, r: Channel) => {
        const info = CHANNEL_TYPES[r.channel_type] || CHANNEL_TYPES.webhook;
        return (
          <Space>
            <span style={{ color: info.color, fontSize: 18 }}>{info.icon}</span>
            <span style={{ fontWeight: 600 }}>{v}</span>
          </Space>
        );
      },
    },
    {
      title: '类型',
      dataIndex: 'channel_type',
      key: 'channel_type',
      width: 120,
      render: (v: string) => {
        const info = CHANNEL_TYPES[v] || CHANNEL_TYPES.webhook;
        return (
          <Tag color={info.color} style={{ borderRadius: 4 }}>
            {info.label}
          </Tag>
        );
      },
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
      render: (v: string) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '连接',
      key: 'monitor_status',
      width: 80,
      render: (_: unknown, r: Channel) => {
        const isWebsocket =
          r.channel_type === 'wecom' &&
          (r.config as Record<string, unknown>)?.wecom_sub_type === 'bot_websocket';
        if (!isWebsocket) return <span style={{ color: '#999', fontSize: 12 }}>-</span>;
        const connected = monitorStatuses[r.id] ?? false;
        return (
          <Tag color={connected ? 'success' : 'default'} style={{ borderRadius: 4, fontSize: 11 }}>
            {connected ? '已连接' : '未连接'}
          </Tag>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: unknown, r: Channel) => (
        <Space size={4}>
          <Tooltip title="测试发送">
            <Button
              type="text"
              size="small"
              icon={<ExperimentOutlined />}
              loading={testing === r.id}
              onClick={() => handleTest(r)}
            />
          </Tooltip>
          <Tooltip title="编辑">
            <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          </Tooltip>
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
          {r.channel_type === 'wecom' &&
            (r.config as Record<string, unknown>)?.wecom_sub_type === 'bot_websocket' && (
              <>
                {(monitorStatuses[r.id] ?? false) ? (
                  <Tooltip title="断开连接">
                    <Button
                      type="text"
                      size="small"
                      icon={<StopOutlined />}
                      loading={monitorLoading[r.id]}
                      onClick={() => handleMonitorAction(r, 'stop')}
                    />
                  </Tooltip>
                ) : (
                  <Tooltip title="连接">
                    <Button
                      type="text"
                      size="small"
                      icon={<CaretRightOutlined />}
                      loading={monitorLoading[r.id]}
                      onClick={() => handleMonitorAction(r, 'start')}
                    />
                  </Tooltip>
                )}
              </>
            )}
          {r.channel_type === 'wecom' &&
            (r.config as Record<string, unknown>)?.wecom_sub_type === 'app' && (
              <>
                <Tooltip title="发送应用消息">
                  <Button
                    type="text"
                    size="small"
                    icon={<SendOutlined />}
                    onClick={() => openAppModal(r, 'send')}
                  />
                </Tooltip>
                <Tooltip title="创建群聊">
                  <Button
                    type="text"
                    size="small"
                    icon={<UsergroupAddOutlined />}
                    onClick={() => openAppModal(r, 'create_chat')}
                  />
                </Tooltip>
                <Tooltip title="发送群聊消息">
                  <Button
                    type="text"
                    size="small"
                    icon={<MessageOutlined />}
                    onClick={() => openAppModal(r, 'chat_send')}
                  />
                </Tooltip>
              </>
            )}
        </Space>
      ),
    },
  ];

  function renderFields(fields: FieldDef[]) {
    return fields.map((field) => {
      const rules: Record<string, unknown>[] = [];
      if (field.required) {
        rules.push({ required: true, message: `请输入${field.label}` });
      }

      if (field.type === 'select') {
        return (
          <Form.Item
            key={field.key}
            name={field.key}
            label={field.label}
            rules={rules}
            initialValue={
              field.key === 'wecom_sub_type'
                ? 'bot_webhook'
                : field.key === 'method'
                  ? 'POST'
                  : field.key === 'auth_type'
                    ? 'none'
                    : field.key === 'success_condition_type'
                      ? 'status_code'
                      : field.key === 'deployment_mode'
                        ? 'cloud'
                        : field.key === 'msg_type'
                          ? 'markdown'
                          : undefined
            }
          >
            <Select options={field.options} />
          </Form.Item>
        );
      }
      if (field.type === 'password') {
        return (
          <Form.Item key={field.key} name={field.key} label={field.label} rules={rules}>
            <Input.Password placeholder={field.placeholder} />
          </Form.Item>
        );
      }
      if (field.type === 'textarea') {
        return (
          <Form.Item
            key={field.key}
            name={field.key}
            label={field.label}
            rules={rules}
            extra={field.key === 'body_template' ? TEMPLATE_VAR_HINT : undefined}
          >
            <Input.TextArea
              rows={4}
              placeholder={field.placeholder}
              style={{ fontFamily: 'monospace', fontSize: 12 }}
            />
          </Form.Item>
        );
      }
      if (field.type === 'number') {
        return (
          <Form.Item
            key={field.key}
            name={field.key}
            label={field.label}
            rules={rules}
            initialValue={
              field.key === 'smtp_port' ? 587 : field.key === 'timeout_seconds' ? 30 : undefined
            }
          >
            <InputNumber placeholder={field.placeholder} style={{ width: '100%' }} />
          </Form.Item>
        );
      }
      if (field.type === 'json') {
        return (
          <Form.Item key={field.key} name={field.key} label={field.label} rules={rules}>
            <Input.TextArea
              rows={3}
              placeholder={field.placeholder}
              style={{ fontFamily: 'monospace', fontSize: 12 }}
            />
          </Form.Item>
        );
      }
      if (field.type === 'switch') {
        return (
          <Form.Item
            key={field.key}
            name={field.key}
            label={field.label}
            valuePropName="checked"
            tooltip={field.tooltip}
          >
            <Switch />
          </Form.Item>
        );
      }
      return (
        <Form.Item
          key={field.key}
          name={field.key}
          label={field.label}
          rules={rules}
          extra={field.key === 'url' ? TEMPLATE_VAR_HINT : undefined}
        >
          <Input placeholder={field.placeholder} />
        </Form.Item>
      );
    });
  }

  function renderFormContent() {
    const baseFields = CHANNEL_CONFIG_FIELDS[channelType] || [];

    // For wecom: show base fields (sub_type selector) + conditional sub-type fields
    if (channelType === 'wecom') {
      return (
        <Form.Item
          noStyle
          shouldUpdate={(prev, cur) =>
            prev.wecom_sub_type !== cur.wecom_sub_type ||
            prev.deployment_mode !== cur.deployment_mode
          }
        >
          {({ getFieldValue }) => {
            const subType = (getFieldValue('wecom_sub_type') as string) || 'bot_webhook';
            const deploymentMode = (getFieldValue('deployment_mode') as string) || 'cloud';
            const allFields = WECOM_SUB_FIELDS[subType] || [];

            // Filter: hide api_base_url unless deployment_mode is private
            const subFields = allFields.filter((f) => {
              if (f.key === 'api_base_url' && deploymentMode !== 'private') return false;
              return true;
            });

            // Group sub-type specific fields by section
            const subSections = new Map<string, FieldDef[]>();
            for (const f of subFields) {
              const sec = f.section || '配置';
              if (!subSections.has(sec)) subSections.set(sec, []);
              subSections.get(sec)!.push(f);
            }

            return (
              <>
                {renderFields(baseFields)}
                {Array.from(subSections.entries()).map(([secName, fields]) => (
                  <div key={secName} style={{ marginTop: 8 }}>
                    <Typography.Text
                      type="secondary"
                      style={{ fontSize: 12, display: 'block', marginBottom: 8 }}
                    >
                      {secName}
                    </Typography.Text>
                    {renderFields(fields)}
                  </div>
                ))}
              </>
            );
          }}
        </Form.Item>
      );
    }

    // Other channel types: section-based layout
    const sections = new Map<string, FieldDef[]>();
    const orphanFields: FieldDef[] = [];
    for (const f of baseFields) {
      if (f.section) {
        if (!sections.has(f.section)) sections.set(f.section, []);
        sections.get(f.section)!.push(f);
      } else {
        orphanFields.push(f);
      }
    }

    if (sections.size > 0) {
      return (
        <>
          {orphanFields.length > 0 && (
            <div style={{ marginTop: 8 }}>{renderFields(orphanFields)}</div>
          )}
          {Array.from(sections.entries()).map(([secName, fields]) => (
            <div key={secName} style={{ marginTop: 8 }}>
              <Typography.Text
                type="secondary"
                style={{ fontSize: 12, display: 'block', marginBottom: 8 }}
              >
                {secName}
              </Typography.Text>
              {renderFields(fields)}
            </div>
          ))}
        </>
      );
    }

    return (
      <div style={{ marginTop: 8 }}>
        <Typography.Text
          type="secondary"
          style={{ fontSize: 12, display: 'block', marginBottom: 12 }}
        >
          配置参数
        </Typography.Text>
        {renderFields(orphanFields)}
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
        <div>
          <Typography.Title level={4} style={{ margin: 0, fontWeight: 600 }}>
            通知渠道
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            配置钉钉、企业微信、Webhook、邮件或自定义 API 通知渠道
          </Typography.Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
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
          locale={{ emptyText: <Empty description="暂无通知渠道，点击上方按钮添加" /> }}
        />
      </Card>

      <Modal
        title={editingId ? '编辑通知渠道' : '添加通知渠道'}
        open={open}
        onCancel={() => {
          setOpen(false);
          setEditingId(null);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        okText={editingId ? '保存' : '添加'}
        destroyOnHidden
        width={560}
      >
        <Tabs
          activeKey={channelType}
          onChange={(key) => {
            if (editingId) return;
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
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
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
          {renderFormContent()}
        </Form>
      </Modal>

      {/* App operations modal */}
      <Modal
        title={
          appModalType === 'send'
            ? '发送应用消息'
            : appModalType === 'create_chat'
              ? '创建应用群聊'
              : '发送群聊消息'
        }
        open={appModalOpen}
        onCancel={() => setAppModalOpen(false)}
        onOk={() => appForm.submit()}
        confirmLoading={appLoading}
        okText={appModalType === 'create_chat' ? '创建' : '发送'}
        destroyOnHidden
        width={480}
      >
        <Form form={appForm} layout="vertical" onFinish={handleAppAction}>
          {appModalType === 'send' && (
            <>
              <Form.Item name="msgtype" label="消息类型" initialValue="text">
                <Select
                  options={[
                    { value: 'text', label: '纯文本' },
                    { value: 'markdown', label: 'Markdown' },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="content"
                label="消息内容"
                rules={[{ required: true, message: '请输入内容' }]}
              >
                <Input.TextArea rows={4} placeholder="消息内容" />
              </Form.Item>
              <Form.Item name="touser" label="接收用户 (touser)">
                <Input placeholder="userid1|userid2，留空则使用 '@all'" />
              </Form.Item>
              <Form.Item name="toparty" label="接收部门 (toparty)">
                <Input placeholder="partyid1|partyid2" />
              </Form.Item>
              <Form.Item name="totag" label="接收标签 (totag)">
                <Input placeholder="tagid1|tagid2" />
              </Form.Item>
            </>
          )}
          {appModalType === 'create_chat' && (
            <>
              <Form.Item
                name="name"
                label="群聊名称"
                rules={[{ required: true, message: '请输入群聊名称' }]}
              >
                <Input placeholder="测试群聊" />
              </Form.Item>
              <Form.Item
                name="owner"
                label="群主 UserID"
                rules={[{ required: true, message: '请输入群主 UserID' }]}
              >
                <Input placeholder="zhangsan" />
              </Form.Item>
              <Form.Item
                name="userlist"
                label="成员列表"
                rules={[{ required: true, message: '请输入成员' }]}
                extra="逗号分隔的 UserID 列表，至少 2 人（含群主）"
              >
                <Input placeholder="zhangsan, lisi, wangwu" />
              </Form.Item>
              <Form.Item name="chatid" label="指定群聊 ID (可选)">
                <Input placeholder="留空自动生成" />
              </Form.Item>
            </>
          )}
          {appModalType === 'chat_send' && (
            <>
              <Form.Item
                name="chatid"
                label="群聊 ID"
                rules={[{ required: true, message: '请输入群聊 ID' }]}
              >
                <Input placeholder="从创建群聊返回的 chatid" />
              </Form.Item>
              <Form.Item name="msgtype" label="消息类型" initialValue="text">
                <Select
                  options={[
                    { value: 'text', label: '纯文本' },
                    { value: 'markdown', label: 'Markdown' },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="content"
                label="消息内容"
                rules={[{ required: true, message: '请输入内容' }]}
              >
                <Input.TextArea rows={4} placeholder="群聊消息内容" />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}
