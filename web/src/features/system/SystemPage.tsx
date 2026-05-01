import { useEffect, useState } from 'react';
import { Card, Descriptions, Tag, Typography, Spin, Space, theme, Tabs } from 'antd';
import {
  SettingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  BgColorsOutlined,
  CloudServerOutlined,
} from '@ant-design/icons';
import BrandSettings from './BrandSettings';
import LdapWizard from './LdapWizard';

function SystemInfo() {
  const { token } = theme.useToken();
  const [health, setHealth] = useState<{ status: string } | null>(null);

  useEffect(() => {
    fetch('/health')
      .then((res) => res.json())
      .then((data) => setHealth(data))
      .catch(() => setHealth({ status: 'unreachable' }));
  }, []);

  const info = [
    { label: '系统版本', value: '0.1.0' },
    {
      label: 'API 状态',
      value: health ? (
        <Tag
          icon={health.status === 'ok' ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
          color={health.status === 'ok' ? 'success' : 'error'}
          style={{ borderRadius: 4 }}
        >
          {health.status === 'ok' ? '正常' : '异常'}
        </Tag>
      ) : (
        <Spin size="small" />
      ),
    },
    { label: '数据库', value: 'PostgreSQL + pgvector' },
    { label: '缓存', value: 'Redis' },
    { label: 'AI 模型', value: 'DeepSeek / OpenAI 兼容' },
    { label: '嵌入模型', value: 'text-embedding-3-small (1536维)' },
  ];

  const components = [
    { name: 'API 服务', status: health?.status === 'ok', desc: 'FastAPI 异步服务' },
    { name: '智能体引擎', status: true, desc: 'LangGraph 智能体引擎' },
    { name: '知识库', status: true, desc: 'LLM-WIKI 检索管道' },
    { name: '记忆服务', status: true, desc: 'pgvector 长期记忆' },
    { name: '工具注册', status: true, desc: 'Skill/MCP 工具注册' },
  ];

  return (
    <div>
      <Card title="系统信息" style={{ borderRadius: 12, marginBottom: 16 }}>
        <Descriptions column={2} size="middle">
          {info.map((i) => (
            <Descriptions.Item
              key={i.label}
              label={<span style={{ color: token.colorTextSecondary }}>{i.label}</span>}
            >
              <span style={{ color: token.colorText }}>{i.value}</span>
            </Descriptions.Item>
          ))}
        </Descriptions>
      </Card>
      <Card title="服务组件" style={{ borderRadius: 12 }}>
        {components.map((svc) => (
          <div
            key={svc.name}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '12px 0',
              borderBottom: `1px solid ${token.colorBorderSecondary}`,
            }}
          >
            <Space>
              {svc.status ? (
                <CheckCircleOutlined style={{ color: token.colorSuccess }} />
              ) : (
                <CloseCircleOutlined style={{ color: token.colorError }} />
              )}
              <span style={{ fontWeight: 500, color: token.colorText }}>{svc.name}</span>
              <span style={{ color: token.colorTextTertiary, fontSize: 13 }}>{svc.desc}</span>
            </Space>
            <Tag color={svc.status ? 'success' : 'error'} style={{ borderRadius: 4 }}>
              {svc.status ? '运行中' : '异常'}
            </Tag>
          </div>
        ))}
      </Card>
    </div>
  );
}

export default function SystemPage() {
  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 24, fontWeight: 600 }}>
        <SettingOutlined style={{ marginRight: 8 }} />
        系统管理
      </Typography.Title>
      <Tabs
        type="card"
        items={[
          {
            key: 'info',
            label: (
              <span>
                <SettingOutlined /> 系统信息
              </span>
            ),
            children: <SystemInfo />,
          },
          {
            key: 'branding',
            label: (
              <span>
                <BgColorsOutlined /> 品牌设置
              </span>
            ),
            children: <BrandSettings />,
          },
          {
            key: 'ldap',
            label: (
              <span>
                <CloudServerOutlined /> LDAP 集成
              </span>
            ),
            children: <LdapWizard />,
          },
        ]}
      />
    </div>
  );
}
