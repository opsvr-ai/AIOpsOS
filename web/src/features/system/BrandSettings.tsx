import { useEffect, useState } from 'react';
import {
  Card,
  Form,
  Input,
  Button,
  ColorPicker,
  App,
  Spin,
  Space,
  Typography,
  theme,
  Upload,
} from 'antd';
import { EyeOutlined, UploadOutlined } from '@ant-design/icons';
import api from '@/services/api';

interface BrandingData {
  logo_url: string;
  favicon_url: string;
  company_name: string;
  primary_color: string;
}

export default function BrandSettings() {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<BrandingData>();

  const fetchBranding = () => {
    setLoading(true);
    api
      .get('/system/branding')
      .then((res) => form.setFieldsValue(res.data))
      .catch(() => message.error('加载品牌设置失败'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchBranding();
  }, []);

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const color =
        typeof values.primary_color === 'string'
          ? values.primary_color
          : (values.primary_color as any)?.toHexString?.() || '#1677ff';
      await api.put('/system/branding', { ...values, primary_color: color });
      message.success('品牌设置已保存');
    } catch {
      // validation failed
    } finally {
      setSaving(false);
    }
  };

  const previewCard = (
    <Card
      size="small"
      title="预览"
      style={{ borderRadius: 12, marginTop: 16 }}
      extra={<EyeOutlined />}
    >
      <div
        style={{
          background: token.colorBgContainer,
          borderRadius: 8,
          border: `1px solid ${token.colorBorderSecondary}`,
          padding: 16,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          {form.getFieldValue('logo_url') && (
            <img
              src={form.getFieldValue('logo_url')}
              alt="logo"
              style={{ height: 32, maxWidth: 120, objectFit: 'contain' }}
            />
          )}
          <Typography.Title level={5} style={{ margin: 0, color: token.colorText }}>
            {form.getFieldValue('company_name') || '公司名称'}
          </Typography.Title>
        </div>
        <div
          style={{
            height: 4,
            borderRadius: 2,
            background: form.getFieldValue('primary_color') || '#1677ff',
          }}
        />
        <Space style={{ marginTop: 12 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 6,
              background: form.getFieldValue('primary_color') || '#1677ff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontWeight: 700,
              fontSize: 14,
            }}
          >
            {(form.getFieldValue('company_name') || 'A')[0]}
          </div>
          <span style={{ color: token.colorTextSecondary, fontSize: 12 }}>侧边栏头部预览</span>
        </Space>
      </div>
    </Card>
  );

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
        <Spin />
      </div>
    );
  }

  return (
    <div>
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          company_name: '',
          primary_color: '#1677ff',
          logo_url: '',
          favicon_url: '',
        }}
      >
        <Card title="品牌标识" style={{ borderRadius: 12, marginBottom: 16 }}>
          <Form.Item name="company_name" label="公司名称">
            <Input placeholder="AIOpsOS" style={{ maxWidth: 400 }} />
          </Form.Item>
          <Form.Item name="primary_color" label="主题色" getValueFromEvent={(c: any) => c}>
            <ColorPicker showText format="hex" />
          </Form.Item>
        </Card>

        <Card title="素材" style={{ borderRadius: 12, marginBottom: 16 }}>
          <Form.Item name="logo_url" label="Logo">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Input placeholder="输入 URL 或点击上传" style={{ maxWidth: 400 }} />
              <Upload
                accept=".png,.jpg,.jpeg,.gif,.webp,.svg"
                showUploadList={false}
                customRequest={async (options) => {
                  const formData = new FormData();
                  formData.append('file', options.file as File);
                  try {
                    const res = await api.post('/system/upload', formData, {
                      headers: { 'Content-Type': 'multipart/form-data' },
                    });
                    if (res.data?.url) {
                      form.setFieldsValue({ logo_url: res.data.url });
                      message.success('Logo 上传成功');
                    } else {
                      message.error(res.data?.error || '上传失败');
                    }
                  } catch {
                    message.error('上传失败');
                  }
                }}
              >
                <Button icon={<UploadOutlined />}>上传 Logo</Button>
              </Upload>
            </Space>
          </Form.Item>
          <Form.Item name="favicon_url" label="Favicon">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Input placeholder="输入 URL 或点击上传" style={{ maxWidth: 400 }} />
              <Upload
                accept=".ico,.png,.svg"
                showUploadList={false}
                customRequest={async (options) => {
                  const formData = new FormData();
                  formData.append('file', options.file as File);
                  try {
                    const res = await api.post('/system/upload', formData, {
                      headers: { 'Content-Type': 'multipart/form-data' },
                    });
                    if (res.data?.url) {
                      form.setFieldsValue({ favicon_url: res.data.url });
                      message.success('Favicon 上传成功');
                    } else {
                      message.error(res.data?.error || '上传失败');
                    }
                  } catch {
                    message.error('上传失败');
                  }
                }}
              >
                <Button icon={<UploadOutlined />}>上传 Favicon</Button>
              </Upload>
            </Space>
          </Form.Item>
        </Card>
      </Form>

      {previewCard}

      <div style={{ marginTop: 24 }}>
        <Button type="primary" onClick={handleSave} loading={saving} style={{ borderRadius: 8 }}>
          保存品牌设置
        </Button>
      </div>
    </div>
  );
}
