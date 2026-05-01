import { useEffect, useState } from 'react';
import {
  Card,
  Form,
  Input,
  InputNumber,
  Switch,
  Button,
  Steps,
  App,
  Spin,
  Typography,
  Table,
  Tag,
  Space,
  Divider,
} from 'antd';
import {
  ApiOutlined,
  UserOutlined,
  TeamOutlined,
  SyncOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

interface LdapConfig {
  server_url: string;
  bind_dn: string;
  bind_password: string;
  base_dn: string;
  user_filter: string;
  attr_username: string;
  attr_email: string;
  attr_display_name: string;
  group_base_dn: string;
  group_filter: string;
  group_role_map: Record<string, string>;
  sync_enabled: boolean;
  sync_interval_hours: number;
}

const DEFAULT_CONFIG: LdapConfig = {
  server_url: '',
  bind_dn: '',
  bind_password: '',
  base_dn: '',
  user_filter: '(objectClass=person)',
  attr_username: 'sAMAccountName',
  attr_email: 'mail',
  attr_display_name: 'displayName',
  group_base_dn: '',
  group_filter: '(objectClass=group)',
  group_role_map: {},
  sync_enabled: false,
  sync_interval_hours: 24,
};

export default function LdapWizard() {
  const { message } = App.useApp();
  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [syncResult, setSyncResult] = useState<any>(null);
  const [config, setConfig] = useState<LdapConfig>(DEFAULT_CONFIG);

  useEffect(() => {
    api
      .get('/system/ldap')
      .then((res) => setConfig({ ...DEFAULT_CONFIG, ...res.data }))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const updateField = (field: keyof LdapConfig, value: any) => {
    setConfig((prev) => ({ ...prev, [field]: value }));
  };

  const saveConfig = async () => {
    await api.put('/system/ldap', config);
    message.success('LDAP 配置已保存');
  };

  const handleTest = async () => {
    await saveConfig();
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.post('/system/ldap/test');
      setTestResult(res.data);
    } catch {
      setTestResult({ ok: false, message: '请求失败' });
    } finally {
      setTesting(false);
    }
  };

  const handleSync = async () => {
    await saveConfig();
    setSyncing(true);
    setSyncResult(null);
    try {
      const res = await api.post('/system/ldap/sync');
      setSyncResult(res.data);
      message.success(`同步完成：找到 ${res.data.total_found} 个用户`);
    } catch {
      message.error('同步失败');
    } finally {
      setSyncing(false);
    }
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
        <Spin />
      </div>
    );
  }

  const stepItems = [
    { title: '服务器连接', icon: <ApiOutlined /> },
    { title: '用户映射', icon: <UserOutlined /> },
    { title: '群组映射', icon: <TeamOutlined /> },
    { title: '测试与同步', icon: <SyncOutlined /> },
  ];

  return (
    <div>
      <Steps current={step} onChange={setStep} items={stepItems} style={{ marginBottom: 24 }} />

      {step === 0 && (
        <Card title="LDAP 服务器连接" style={{ borderRadius: 12 }}>
          <Form layout="vertical">
            <Form.Item label="服务器地址" required>
              <Input
                placeholder="ldap://ldap.example.com:389"
                value={config.server_url}
                onChange={(e) => updateField('server_url', e.target.value)}
              />
            </Form.Item>
            <Form.Item label="绑定 DN" required>
              <Input
                placeholder="CN=svc-ldap,OU=Service Accounts,DC=example,DC=com"
                value={config.bind_dn}
                onChange={(e) => updateField('bind_dn', e.target.value)}
              />
            </Form.Item>
            <Form.Item label="绑定密码" required>
              <Input.Password
                value={config.bind_password}
                onChange={(e) => updateField('bind_password', e.target.value)}
              />
            </Form.Item>
          </Form>
          <Divider />
          <Space>
            <Button
              type="primary"
              onClick={() => {
                saveConfig();
                setStep(1);
              }}
              style={{ borderRadius: 8 }}
            >
              下一步：用户映射
            </Button>
            <Button onClick={handleTest} loading={testing} style={{ borderRadius: 8 }}>
              测试连接
            </Button>
          </Space>
          {testResult && (
            <div style={{ marginTop: 16 }}>
              <Tag
                icon={testResult.ok ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
                color={testResult.ok ? 'success' : 'error'}
                style={{ borderRadius: 4, padding: '4px 12px' }}
              >
                {testResult.message}
              </Tag>
            </div>
          )}
        </Card>
      )}

      {step === 1 && (
        <Card title="用户属性映射" style={{ borderRadius: 12 }}>
          <Form layout="vertical">
            <Form.Item label="基础 DN" required>
              <Input
                placeholder="OU=Users,DC=example,DC=com"
                value={config.base_dn}
                onChange={(e) => updateField('base_dn', e.target.value)}
              />
            </Form.Item>
            <Form.Item label="用户过滤器">
              <Input
                value={config.user_filter}
                onChange={(e) => updateField('user_filter', e.target.value)}
              />
            </Form.Item>
            <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
              用于识别用户对象的 LDAP 过滤器，默认：(objectClass=person)
            </Typography.Text>

            <Typography.Title level={5} style={{ marginBottom: 12 }}>
              属性映射
            </Typography.Title>
            <Form.Item label="用户名字段">
              <Input
                placeholder="sAMAccountName"
                value={config.attr_username}
                onChange={(e) => updateField('attr_username', e.target.value)}
              />
            </Form.Item>
            <Form.Item label="邮箱字段">
              <Input
                placeholder="mail"
                value={config.attr_email}
                onChange={(e) => updateField('attr_email', e.target.value)}
              />
            </Form.Item>
            <Form.Item label="显示名称字段">
              <Input
                placeholder="displayName"
                value={config.attr_display_name}
                onChange={(e) => updateField('attr_display_name', e.target.value)}
              />
            </Form.Item>
          </Form>
          <Divider />
          <Space>
            <Button onClick={() => setStep(0)} style={{ borderRadius: 8 }}>
              上一步
            </Button>
            <Button
              type="primary"
              onClick={() => {
                saveConfig();
                setStep(2);
              }}
              style={{ borderRadius: 8 }}
            >
              下一步：群组映射
            </Button>
          </Space>
        </Card>
      )}

      {step === 2 && (
        <Card title="群组与角色映射" style={{ borderRadius: 12 }}>
          <Form layout="vertical">
            <Form.Item label="群组基础 DN">
              <Input
                placeholder="OU=Groups,DC=example,DC=com"
                value={config.group_base_dn}
                onChange={(e) => updateField('group_base_dn', e.target.value)}
              />
            </Form.Item>
            <Form.Item label="群组过滤器">
              <Input
                value={config.group_filter}
                onChange={(e) => updateField('group_filter', e.target.value)}
              />
            </Form.Item>
            <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
              将 LDAP 群组 DN 映射到 AIOpsOS 角色（admin, operator, viewer）。
            </Typography.Text>

            <Typography.Title level={5} style={{ marginBottom: 12 }}>
              群组-角色映射
            </Typography.Title>
            {Object.entries(config.group_role_map).length === 0 && (
              <Typography.Text type="secondary">尚未配置映射，点击"添加映射"创建。</Typography.Text>
            )}
            {Object.entries(config.group_role_map).map(([group, role], idx) => (
              <Space key={idx} style={{ display: 'flex', marginBottom: 8 }} align="baseline">
                <Input
                  placeholder="CN=OpsAdmins,OU=Groups,DC=example,DC=com"
                  value={group}
                  onChange={(e) => {
                    const newMap = { ...config.group_role_map };
                    delete newMap[group];
                    newMap[e.target.value] = role;
                    updateField('group_role_map', newMap);
                  }}
                  style={{ width: 400 }}
                />
                <Input
                  placeholder="operator"
                  value={role}
                  onChange={(e) => {
                    const newMap = { ...config.group_role_map };
                    newMap[group] = e.target.value;
                    updateField('group_role_map', newMap);
                  }}
                  style={{ width: 140 }}
                />
                <Button
                  danger
                  size="small"
                  onClick={() => {
                    const newMap = { ...config.group_role_map };
                    delete newMap[group];
                    updateField('group_role_map', newMap);
                  }}
                >
                  移除
                </Button>
              </Space>
            ))}
            <Button
              type="dashed"
              onClick={() =>
                updateField('group_role_map', { ...config.group_role_map, '': 'operator' })
              }
              style={{ marginTop: 8, borderRadius: 8 }}
            >
              + 添加映射
            </Button>
          </Form>
          <Divider />
          <Space>
            <Button onClick={() => setStep(1)} style={{ borderRadius: 8 }}>
              上一步
            </Button>
            <Button
              type="primary"
              onClick={() => {
                saveConfig();
                setStep(3);
              }}
              style={{ borderRadius: 8 }}
            >
              下一步：测试与同步
            </Button>
          </Space>
        </Card>
      )}

      {step === 3 && (
        <Card title="测试与同步" style={{ borderRadius: 12 }}>
          <Form layout="vertical">
            <Form.Item label="启用定时同步">
              <Switch
                checked={config.sync_enabled}
                onChange={(v) => updateField('sync_enabled', v)}
              />
            </Form.Item>
            {config.sync_enabled && (
              <Form.Item label="同步间隔（小时）">
                <InputNumber
                  min={1}
                  max={168}
                  value={config.sync_interval_hours}
                  onChange={(v) => updateField('sync_interval_hours', v || 24)}
                />
              </Form.Item>
            )}
          </Form>

          <Divider />

          <Space direction="vertical" style={{ width: '100%' }}>
            <Space>
              <Button
                type="primary"
                icon={<ApiOutlined />}
                onClick={handleTest}
                loading={testing}
                style={{ borderRadius: 8 }}
              >
                测试连接
              </Button>
              <Button
                icon={<SyncOutlined />}
                onClick={handleSync}
                loading={syncing}
                style={{ borderRadius: 8 }}
              >
                立即同步
              </Button>
            </Space>

            {testResult && (
              <Card size="small" style={{ borderRadius: 8, marginTop: 8 }}>
                <Space>
                  {testResult.ok ? (
                    <CheckCircleOutlined style={{ color: '#52c41a' }} />
                  ) : (
                    <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
                  )}
                  <Typography.Text strong>连接测试：</Typography.Text>
                  <Tag color={testResult.ok ? 'success' : 'error'} style={{ borderRadius: 4 }}>
                    {testResult.ok ? '成功' : '失败'}
                  </Tag>
                  <Typography.Text type="secondary">{testResult.message}</Typography.Text>
                </Space>
              </Card>
            )}

            {syncResult && (
              <Card size="small" title="同步结果" style={{ borderRadius: 8, marginTop: 8 }}>
                <Table
                  dataSource={[
                    { key: 'found', label: '找到用户', value: syncResult.total_found },
                    { key: 'created', label: '已创建', value: syncResult.created },
                    { key: 'updated', label: '已更新', value: syncResult.updated },
                    { key: 'errors', label: '错误数', value: syncResult.errors },
                  ]}
                  columns={[
                    { title: '指标', dataIndex: 'label', key: 'label' },
                    {
                      title: '数值',
                      dataIndex: 'value',
                      key: 'value',
                      render: (v: number, r: any) => (
                        <Tag
                          color={r.key === 'errors' && v > 0 ? 'error' : 'default'}
                          style={{ borderRadius: 4 }}
                        >
                          {v}
                        </Tag>
                      ),
                    },
                  ]}
                  pagination={false}
                  size="small"
                />
              </Card>
            )}
          </Space>

          <Divider />
          <Button onClick={() => setStep(2)} style={{ borderRadius: 8 }}>
            上一步
          </Button>
        </Card>
      )}
    </div>
  );
}
