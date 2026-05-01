import { useState } from 'react';
import { Typography, Button } from 'antd';
import { SafetyCertificateOutlined, ThunderboltOutlined, TeamOutlined } from '@ant-design/icons';
import LdapWizard from '@/features/system/LdapWizard';

const { Text } = Typography;

const benefits = [
  { icon: <SafetyCertificateOutlined />, text: '企业域账号登录，无需单独注册' },
  { icon: <ThunderboltOutlined />, text: '密码策略由 AD 域控统一管理' },
  { icon: <TeamOutlined />, text: '按 LDAP 群组自动分配平台角色' },
];

export default function Step2Ldap() {
  const [started, setStarted] = useState(false);

  if (!started) {
    return (
      <div style={{ textAlign: 'center', paddingTop: 24 }}>
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
          <SafetyCertificateOutlined />
        </div>
        <Typography.Title level={5} style={{ marginBottom: 8 }}>
          LDAP 企业域集成
        </Typography.Title>
        <Text type="secondary" style={{ display: 'block', marginBottom: 20, fontSize: 13 }}>
          对接企业 LDAP/AD 目录服务，员工可使用域账号直接登录 AIOpsOS
        </Text>

        <div style={{ display: 'flex', justifyContent: 'center', gap: 24, marginBottom: 24 }}>
          {benefits.map((b, i) => (
            <div key={i} style={{ textAlign: 'center', width: 140 }}>
              <div style={{ fontSize: 22, color: '#2563eb', marginBottom: 6 }}>{b.icon}</div>
              <Text style={{ fontSize: 12, color: '#555' }}>{b.text}</Text>
            </div>
          ))}
        </div>

        <Button
          type="primary"
          size="large"
          onClick={() => setStarted(true)}
          style={{ borderRadius: 8 }}
        >
          开始配置
        </Button>
      </div>
    );
  }

  return <LdapWizard />;
}
