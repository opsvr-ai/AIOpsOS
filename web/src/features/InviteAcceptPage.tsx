import { useEffect, useState } from 'react';
import { Card, Form, Input, Button, App, Typography, theme, Spin, Result } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { authApi } from '@/services/auth';
import { useAuthStore } from '@/stores/authStore';
import PageFadeIn from '@/components/ui/PageFadeIn';

export default function InviteAcceptPage() {
  const { token: inviteToken } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const { message } = App.useApp();
  const { token: themeToken } = theme.useToken();
  const [invitation, setInvitation] = useState<{
    email: string;
    space_name?: string;
    expires_at: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!inviteToken) {
      setError('无效的邀请链接');
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const res = await authApi.getInvitation(inviteToken);
        setInvitation(res.data as any);
      } catch (e: any) {
        const detail = e?.response?.data?.detail || '邀请链接无效或已过期';
        setError(typeof detail === 'string' ? detail : '邀请链接无效或已过期');
      } finally {
        setLoading(false);
      }
    })();
  }, [inviteToken]);

  const onFinish = async (values: {
    username: string;
    password: string;
    password_confirm: string;
  }) => {
    if (values.password !== values.password_confirm) {
      message.error('两次密码不一致');
      return;
    }
    if (!inviteToken) return;
    setSubmitting(true);
    try {
      const res = await authApi.acceptInvitation(inviteToken, {
        username: values.username,
        email: invitation!.email,
        password: values.password,
      });
      const { access_token, refresh_token } = res.data;
      setAuth(access_token, refresh_token, {
        id: '',
        username: values.username,
        email: invitation!.email,
        roles: [],
      });
      const meRes = await authApi.getMe();
      const userData = meRes.data;
      setAuth(access_token, refresh_token, {
        id: userData.id,
        username: userData.username,
        email: userData.email,
        default_space_id: userData.default_space_id,
        roles: userData.roles.map((r: any) => r.name),
      });
      message.success('注册成功');
      navigate('/');
    } catch (e: any) {
      const detail = e?.response?.data?.detail || '接受邀请失败';
      message.error(typeof detail === 'string' ? detail : '接受邀请失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div
        style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      >
        <Spin size="large" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      >
        <Result status="error" title="邀请无效" subTitle={error} />
      </div>
    );
  }

  return (
    <PageFadeIn>
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
          styles={{ body: { padding: '40px 32px' } }}
        >
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: 14,
              background: themeToken.colorPrimary,
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
          <Typography.Title level={3} style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>
            接受邀请
          </Typography.Title>
          <Typography.Text
            style={{
              color: themeToken.colorTextSecondary,
              fontSize: 14,
              display: 'block',
              marginTop: 4,
              marginBottom: 8,
            }}
          >
            {invitation?.email}
          </Typography.Text>
          {invitation?.space_name && (
            <Typography.Text
              style={{
                color: themeToken.colorPrimary,
                fontSize: 13,
                display: 'block',
                marginBottom: 24,
              }}
            >
              加入空间：{invitation.space_name}
            </Typography.Text>
          )}

          <Form onFinish={onFinish} size="large" layout="vertical">
            <Form.Item
              name="username"
              rules={[{ required: true, message: '请输入用户名' }]}
              style={{ marginBottom: 16 }}
            >
              <Input
                prefix={<UserOutlined style={{ color: themeToken.colorTextTertiary }} />}
                placeholder="用户名"
                style={{ height: 44, borderRadius: 10 }}
              />
            </Form.Item>
            <Form.Item
              name="password"
              rules={[
                { required: true, message: '请输入密码' },
                { min: 6, message: '密码至少6位' },
              ]}
              style={{ marginBottom: 16 }}
            >
              <Input.Password
                prefix={<LockOutlined style={{ color: themeToken.colorTextTertiary }} />}
                placeholder="密码"
                style={{ height: 44, borderRadius: 10 }}
              />
            </Form.Item>
            <Form.Item
              name="password_confirm"
              rules={[{ required: true, message: '请确认密码' }]}
              style={{ marginBottom: 24 }}
            >
              <Input.Password
                prefix={<LockOutlined style={{ color: themeToken.colorTextTertiary }} />}
                placeholder="确认密码"
                style={{ height: 44, borderRadius: 10 }}
              />
            </Form.Item>
            <Form.Item style={{ marginBottom: 0 }}>
              <Button
                type="primary"
                htmlType="submit"
                block
                size="large"
                loading={submitting}
                style={{ height: 44, borderRadius: 10, fontWeight: 600 }}
              >
                注册并加入
              </Button>
            </Form.Item>
          </Form>
        </Card>
      </div>
    </PageFadeIn>
  );
}
