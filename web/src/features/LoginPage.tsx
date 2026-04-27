import { Card, Form, Input, Button, App, theme, Typography } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { authApi } from '@/services/auth';
import { useAuthStore } from '@/stores/authStore';

export default function LoginPage() {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const { message } = App.useApp();
  const { token } = theme.useToken();

  const onFinish = async (values: { username: string; password: string }) => {
    try {
      const loginRes = await authApi.login(values);
      const { access_token, refresh_token } = loginRes.data;
      setAuth(access_token, refresh_token, { id: '', username: values.username, email: '' });
      try {
        const meRes = await authApi.getMe();
        setAuth(access_token, refresh_token, meRes.data);
      } catch {
        setAuth(access_token, refresh_token, { id: '', username: values.username, email: '' });
      }
      message.success('登录成功');
      navigate('/');
    } catch {
      message.error('用户名或密码错误');
    }
  };

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #EEF2FF 0%, #F9FAFB 50%, #ECFDF5 100%)',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Subtle decorative dots */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: 'radial-gradient(circle, rgba(37,99,235,0.06) 1px, transparent 1px)',
          backgroundSize: '32px 32px',
        }}
      />

      <Card
        style={{
          width: 400,
          textAlign: 'center',
          borderRadius: 16,
          boxShadow: '0 4px 24px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.04)',
          position: 'relative',
        }}
        bodyStyle={{ padding: '40px 32px' }}
      >
        {/* Logo */}
        <div
          style={{
            width: 48,
            height: 48,
            borderRadius: 14,
            background: token.colorPrimary,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontSize: 22,
            fontWeight: 700,
            margin: '0 auto 16px',
          }}
        >
          A
        </div>

        <Typography.Title
          level={3}
          style={{
            margin: 0,
            fontSize: 22,
            fontWeight: 600,
            color: token.colorText,
          }}
        >
          AIOpsOS
        </Typography.Title>
        <Typography.Text
          style={{
            color: token.colorTextSecondary,
            fontSize: 14,
            display: 'block',
            marginTop: 4,
            marginBottom: 32,
          }}
        >
          智能运维平台
        </Typography.Text>

        <Form onFinish={onFinish} size="large" layout="vertical">
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
            style={{ marginBottom: 20 }}
          >
            <Input
              prefix={<UserOutlined style={{ color: token.colorTextTertiary }} />}
              placeholder="用户名"
              style={{ height: 44, borderRadius: 10 }}
            />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
            style={{ marginBottom: 28 }}
          >
            <Input.Password
              prefix={<LockOutlined style={{ color: token.colorTextTertiary }} />}
              placeholder="密码"
              style={{ height: 44, borderRadius: 10 }}
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button
              type="primary"
              htmlType="submit"
              block
              size="large"
              style={{ height: 44, borderRadius: 10, fontWeight: 600 }}
            >
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
