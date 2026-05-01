import { useEffect, useState } from 'react';
import { Card, Form, Input, Button, App, theme, Typography, Tabs } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useNavigate, Link } from 'react-router-dom';
import { authApi } from '@/services/auth';
import { useAuthStore } from '@/stores/authStore';

const FADE_IN_DURATION = 1000;
const FADE_OUT_DURATION = 800;

const fadeInKeyframes = `
@keyframes loginFadeIn {
  0% { opacity: 0; transform: translateY(12px); }
  100% { opacity: 1; transform: translateY(0); }
}
@keyframes loginCardFadeIn {
  0% { opacity: 0; transform: translateY(24px) scale(0.97); }
  100% { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes loginBgFadeIn {
  0% { opacity: 0; }
  100% { opacity: 1; }
}
`;

let styleInjected = false;
function injectKeyframes() {
  if (styleInjected) return;
  styleInjected = true;
  const style = document.createElement('style');
  style.textContent = fadeInKeyframes;
  document.head.appendChild(style);
}

export default function LoginPage() {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const setSetupRequired = useAuthStore((s) => s.setSetupRequired);
  const { message } = App.useApp();
  const { token } = theme.useToken();
  const [loginType, setLoginType] = useState<'local' | 'ldap'>('local');
  const [phase, setPhase] = useState<'entering' | 'active' | 'exiting'>('entering');

  useEffect(() => {
    injectKeyframes();
    const t = setTimeout(() => setPhase('active'), FADE_IN_DURATION + 200);
    return () => clearTimeout(t);
  }, []);

  const onFinish = async (values: { username: string; password: string }) => {
    try {
      const loginRes = await authApi.login({ ...values, login_type: loginType });
      const { access_token, refresh_token } = loginRes.data;
      setAuth(access_token, refresh_token, {
        id: '',
        username: values.username,
        email: '',
        roles: [],
      });
      const meRes = await authApi.getMe();
      const userData = meRes.data;
      setAuth(access_token, refresh_token, {
        id: userData.id,
        username: userData.username,
        email: userData.email,
        default_space_id: userData.default_space_id,
        roles: userData.roles.map((r) => r.name),
      });
      setSetupRequired(userData.setup_required || false);
      message.success('登录成功');
      setPhase('exiting');
      const target = userData.setup_required ? '/onboarding' : '/';
      setTimeout(() => navigate(target), FADE_OUT_DURATION);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || '未知错误';
      message.error(typeof detail === 'string' ? detail : '用户名或密码错误');
    }
  };

  const isLeaving = phase === 'exiting';

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
        opacity: phase === 'entering' ? 0 : isLeaving ? 0 : 1,
        animation:
          phase === 'entering'
            ? `loginBgFadeIn ${FADE_IN_DURATION}ms cubic-bezier(0.22, 0.61, 0.36, 1) forwards`
            : isLeaving
              ? `loginBgFadeIn ${FADE_OUT_DURATION}ms cubic-bezier(0.55, 0.06, 0.68, 0.19) reverse forwards`
              : 'none',
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
          ...(phase === 'entering'
            ? {
                opacity: 0,
                animation: `loginCardFadeIn ${FADE_IN_DURATION}ms cubic-bezier(0.22, 0.61, 0.36, 1) forwards`,
                animationDelay: '200ms',
              }
            : isLeaving
              ? {
                  opacity: 0,
                  transition: `opacity ${FADE_OUT_DURATION}ms cubic-bezier(0.55, 0.06, 0.68, 0.19), transform ${FADE_OUT_DURATION}ms cubic-bezier(0.55, 0.06, 0.68, 0.19)`,
                  transform: 'translateY(-12px) scale(0.97)',
                }
              : {}),
        }}
        styles={{ body: { padding: '40px 32px' } }}
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

        <Tabs
          activeKey={loginType}
          onChange={(k) => setLoginType(k as 'local' | 'ldap')}
          centered
          style={{ marginBottom: 8 }}
          items={[
            { key: 'local', label: '本地登录' },
            { key: 'ldap', label: '域账号登录' },
          ]}
        />

        <Form onFinish={onFinish} size="large" layout="vertical">
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
            style={{ marginBottom: 20 }}
          >
            <Input
              prefix={<UserOutlined style={{ color: token.colorTextTertiary }} />}
              placeholder={loginType === 'ldap' ? '域账号' : '用户名'}
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

        <div style={{ marginTop: 20 }}>
          <Link to="/register" style={{ color: token.colorPrimary, fontSize: 14 }}>
            还没有账号？立即注册
          </Link>
        </div>
      </Card>
    </div>
  );
}
