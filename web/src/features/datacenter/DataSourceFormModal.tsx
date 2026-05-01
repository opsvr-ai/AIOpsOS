import { useEffect } from 'react';
import {
  Modal, Form, Input, Select, InputNumber, Switch, Segmented,
  Tabs, Typography, Alert, Space,
} from 'antd';
import {
  ApiOutlined, LinkOutlined, ClusterOutlined, CopyOutlined,
  FileTextOutlined, MessageOutlined, DatabaseOutlined,
} from '@ant-design/icons';
import ApiRequestStepEditor from './ApiRequestStepEditor';

const { Text } = Typography;
const { TextArea } = Input;
const { Option } = Select;

interface DataSourceItem {
  id: string;
  name: string;
  description: string | null;
  source_type: string;
  is_enabled: boolean;
  config: Record<string, unknown>;
  normalization_rules: Record<string, unknown>;
  last_ingested_at: string | null;
  total_ingested: number;
  status: string;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface Props {
  open: boolean;
  editing: DataSourceItem | null;
  onCancel: () => void;
  onSubmit: (values: Record<string, unknown>) => void;
}

const AUTH_TYPE_OPTIONS = [
  { label: '无', value: 'none' },
  { label: 'Basic', value: 'basic' },
  { label: 'Bearer Token', value: 'bearer' },
  { label: 'OAuth2', value: 'oauth2' },
  { label: 'API Key', value: 'api_key' },
];

export default function DataSourceFormModal({ open, editing, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm();
  const sourceType = Form.useWatch('source_type', form);
  const authType = Form.useWatch(['config', 'auth', 'type'], form);
  const isEditing = !!editing;

  useEffect(() => {
    if (!open) return;
    if (editing) {
      form.setFieldsValue({
        name: editing.name,
        description: editing.description,
        source_type: editing.source_type,
        is_enabled: editing.is_enabled,
        status: editing.status,
        config: { ...editing.config },
        normalization_rules: editing.normalization_rules
          ? JSON.stringify(editing.normalization_rules, null, 2)
          : '',
      });
    } else {
      form.resetFields();
      form.setFieldsValue({
        source_type: 'webhook',
        is_enabled: true,
        status: 'active',
        config: { auth: { type: 'none' }, request_chain: [] },
      });
    }
  }, [open, editing, form]);

  const handleFinish = (values: Record<string, unknown>) => {
    const payload = {
      ...values,
      normalization_rules: values.normalization_rules
        ? (() => { try { return JSON.parse(values.normalization_rules as string); } catch { return {}; } })()
        : {},
    };
    onSubmit(payload);
  };

  const webhookUrl = editing?.config?.endpoint_id
    ? `${window.location.origin}/api/v1/webhook/${editing.config.endpoint_id}`
    : '';

  return (
    <Modal
      title={isEditing ? '编辑数据源' : '创建数据源'}
      open={open}
      onCancel={onCancel}
      onOk={() => form.submit()}
      okText={isEditing ? '保存' : '创建'}
      width={720}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" onFinish={handleFinish} initialValues={{
        source_type: 'webhook',
        is_enabled: true,
        status: 'active',
        config: { auth: { type: 'none' }, request_chain: [] },
      }}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="数据源名称" />
        </Form.Item>

        <Form.Item name="description" label="描述">
          <TextArea rows={2} placeholder="数据源描述（可选）" />
        </Form.Item>

        <Form.Item name="source_type" label="数据源类型" rules={[{ required: true }]}>
          <Segmented
            block
            disabled={isEditing}
            options={[
              { label: <span><LinkOutlined /> Webhook</span>, value: 'webhook' },
              { label: <span><ApiOutlined /> API</span>, value: 'api' },
              { label: <span><ClusterOutlined /> Kafka</span>, value: 'kafka' },
              { label: <span><FileTextOutlined /> 日志</span>, value: 'log' },
              { label: <span><MessageOutlined /> ITSM</span>, value: 'itsm' },
              { label: <span><DatabaseOutlined /> CMDB</span>, value: 'cmdb' },
            ]}
          />
        </Form.Item>

        {/* --- Webhook config --- */}
        {sourceType === 'webhook' && (
          <>
            {isEditing && webhookUrl && (
              <Alert
                type="info"
                message={
                  <Space>
                    <Text copyable={{ text: webhookUrl, icon: <CopyOutlined /> }} style={{ fontSize: 12 }}>
                      {webhookUrl}
                    </Text>
                  </Space>
                }
                style={{ marginBottom: 12, borderRadius: 8 }}
              />
            )}
            {isEditing && editing?.config?.secret && (
              <Form.Item label="密钥 (Secret)">
                <Input.Password readOnly value={editing.config.secret as string}
                  iconRender={(v) => (v ? <CopyOutlined /> : <CopyOutlined />)}
                />
              </Form.Item>
            )}
            <Form.Item name={['config', 'allowed_ips']} label="允许 IP (逗号分隔)">
              <Input placeholder="192.168.1.0/24, 10.0.0.1" />
            </Form.Item>
            <Form.Item name={['config', 'rate_limit_per_min']} label="速率限制 (次/分钟)">
              <InputNumber min={1} max={1000} placeholder="60" style={{ width: '100%' }} />
            </Form.Item>
          </>
        )}

        {/* --- API config --- */}
        {sourceType === 'api' && (
          <>
            <Form.Item name={['config', 'base_url']} label="Base URL" rules={[{ required: true }]}>
              <Input placeholder="https://api.example.com" />
            </Form.Item>
            <Space size={12} style={{ display: 'flex' }}>
              <Form.Item name={['config', 'poll_interval_seconds']} label="轮询间隔 (秒)" style={{ flex: 1 }}>
                <InputNumber min={10} max={86400} placeholder="60" style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name={['config', 'timeout_seconds']} label="超时 (秒)" style={{ flex: 1 }}>
                <InputNumber min={1} max={300} placeholder="30" style={{ width: '100%' }} />
              </Form.Item>
            </Space>

            <Text strong style={{ fontSize: 13, display: 'block', marginBottom: 8 }}>鉴权配置</Text>
            <Form.Item name={['config', 'auth', 'type']} label="鉴权类型">
              <Select options={AUTH_TYPE_OPTIONS} />
            </Form.Item>

            {authType === 'basic' && (
              <Space size={12} style={{ display: 'flex' }}>
                <Form.Item name={['config', 'auth', 'username']} label="用户名" style={{ flex: 1 }}
                  rules={[{ required: true }]}>
                  <Input />
                </Form.Item>
                <Form.Item name={['config', 'auth', 'password']} label="密码" style={{ flex: 1 }}
                  rules={[{ required: true }]}>
                  <Input.Password />
                </Form.Item>
              </Space>
            )}

            {authType === 'bearer' && (
              <Form.Item name={['config', 'auth', 'token']} label="Token" rules={[{ required: true }]}>
                <Input.Password placeholder="Bearer token" />
              </Form.Item>
            )}

            {authType === 'oauth2' && (
              <>
                <Form.Item name={['config', 'auth', 'token_url']} label="Token URL" rules={[{ required: true }]}>
                  <Input placeholder="https://auth.example.com/oauth/token" />
                </Form.Item>
                <Space size={12} style={{ display: 'flex' }}>
                  <Form.Item name={['config', 'auth', 'client_id']} label="Client ID" style={{ flex: 1 }}>
                    <Input />
                  </Form.Item>
                  <Form.Item name={['config', 'auth', 'client_secret']} label="Client Secret" style={{ flex: 1 }}>
                    <Input.Password />
                  </Form.Item>
                </Space>
                <Form.Item name={['config', 'auth', 'scope']} label="Scope">
                  <Input placeholder="read write" />
                </Form.Item>
              </>
            )}

            {authType === 'api_key' && (
              <Space size={12} style={{ display: 'flex' }}>
                <Form.Item name={['config', 'auth', 'key_name']} label="Key 名称" style={{ flex: 1 }}>
                  <Input placeholder="X-API-Key" />
                </Form.Item>
                <Form.Item name={['config', 'auth', 'key_value']} label="Key 值" style={{ flex: 1 }}>
                  <Input.Password placeholder="api-key-value" />
                </Form.Item>
              </Space>
            )}

            <Text strong style={{ fontSize: 13, display: 'block', marginBottom: 8, marginTop: 16 }}>
              请求链
            </Text>
            <Form.Item name={['config', 'request_chain']}>
              <ApiRequestStepEditor />
            </Form.Item>
          </>
        )}

        {/* --- Kafka config --- */}
        {sourceType === 'kafka' && (
          <>
            <Form.Item name={['config', 'topic']} label="Topic" rules={[{ required: true }]}>
              <Input placeholder="ops-events" />
            </Form.Item>
            <Form.Item name={['config', 'bootstrap_servers']} label="Bootstrap Servers" rules={[{ required: true }]}>
              <Input placeholder="localhost:9092" />
            </Form.Item>
            <Form.Item name={['config', 'consumer_group']} label="Consumer Group">
              <Input placeholder="aiopsos-consumer" />
            </Form.Item>
            <Tabs
              items={[
                {
                  key: 'sasl',
                  label: 'SASL (可选)',
                  children: (
                    <>
                      <Form.Item name={['config', 'sasl_mechanism']} label="SASL Mechanism">
                        <Select allowClear placeholder="无">
                          <Option value="PLAIN">PLAIN</Option>
                          <Option value="SCRAM-SHA-256">SCRAM-SHA-256</Option>
                          <Option value="SCRAM-SHA-512">SCRAM-SHA-512</Option>
                        </Select>
                      </Form.Item>
                      <Space size={12} style={{ display: 'flex' }}>
                        <Form.Item name={['config', 'sasl_username']} label="Username" style={{ flex: 1 }}>
                          <Input />
                        </Form.Item>
                        <Form.Item name={['config', 'sasl_password']} label="Password" style={{ flex: 1 }}>
                          <Input.Password />
                        </Form.Item>
                      </Space>
                    </>
                  ),
                },
              ]}
            />
          </>
        )}

        {/* --- Log config --- */}
        {sourceType === 'log' && (
          <>
            <Form.Item name={['config', 'source']} label="采集来源">
              <Select
                options={[
                  { value: 'filebeat', label: 'Filebeat' },
                  { value: 'kafka', label: 'Kafka' },
                  { value: 'vector', label: 'Vector' },
                ]}
              />
            </Form.Item>
            <Space size={12} style={{ display: 'flex' }}>
              <Form.Item name={['config', 'batch_size']} label="批量大小" style={{ flex: 1 }}>
                <InputNumber min={100} max={5000} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name={['config', 'retention_minutes']} label="保留(分钟)" style={{ flex: 1 }}>
                <InputNumber min={5} max={1440} style={{ width: '100%' }} />
              </Form.Item>
            </Space>
          </>
        )}

        {/* --- ITSM config --- */}
        {sourceType === 'itsm' && (
          <>
            <Form.Item name={['config', 'itsm_system']} label="ITSM系统">
              <Select
                options={[
                  { value: 'servicenow', label: 'ServiceNow' },
                  { value: 'jira', label: 'Jira' },
                  { value: 'zendesk', label: 'Zendesk' },
                  { value: 'custom', label: '自定义' },
                ]}
              />
            </Form.Item>
            <Form.Item name={['config', 'api_base_url']} label="API地址">
              <Input placeholder="https://itsm.example.com" />
            </Form.Item>
            <Space size={12} style={{ display: 'flex' }}>
              <Form.Item name={['config', 'poll_interval_seconds']} label="轮询间隔(秒)" style={{ flex: 1 }}>
                <InputNumber min={60} max={3600} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name={['config', 'alert_link_window_minutes']} label="告警关联窗口(分)" style={{ flex: 1 }}>
                <InputNumber min={5} max={120} style={{ width: '100%' }} />
              </Form.Item>
            </Space>
            <Form.Item name={['config', 'ticket_types']} label="工单类型">
              <Select
                mode="multiple"
                options={[
                  { value: 'incident', label: '事件单' },
                  { value: 'change', label: '变更单' },
                  { value: 'problem', label: '问题单' },
                  { value: 'request', label: '服务请求' },
                ]}
              />
            </Form.Item>
          </>
        )}

        {/* --- CMDB config --- */}
        {sourceType === 'cmdb' && (
          <>
            <Form.Item name={['config', 'cmdb_system']} label="CMDB系统">
              <Select
                options={[
                  { value: 'itop', label: 'iTop' },
                  { value: 'servicenow', label: 'ServiceNow' },
                  { value: 'custom', label: '自定义' },
                ]}
              />
            </Form.Item>
            <Form.Item name={['config', 'api_base_url']} label="API地址">
              <Input placeholder="https://cmdb.example.com" />
            </Form.Item>
            <Form.Item name={['config', 'sync_schedule']} label="同步计划(Cron)">
              <Input placeholder="0 * * * *" />
            </Form.Item>
            <Form.Item name={['config', 'default_mode']} label="默认同步模式">
              <Select
                options={[
                  { value: 'discover', label: '发现模式(首次)' },
                  { value: 'incremental', label: '增量同步' },
                  { value: 'full', label: '全量同步' },
                ]}
              />
            </Form.Item>
          </>
        )}

        <Space size={12} style={{ display: 'flex', marginTop: 12 }}>
          <Form.Item name="is_enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select style={{ width: 100 }}>
              <Option value="active">正常</Option>
              <Option value="paused">暂停</Option>
            </Select>
          </Form.Item>
        </Space>

        <Form.Item name="normalization_rules" label="字段映射规则 (JSON)">
          <TextArea
            rows={3}
            placeholder='{"severity": "level", "title": "summary"}'
            style={{ fontFamily: 'monospace', fontSize: 12 }}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
