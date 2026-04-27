import { Typography, theme } from 'antd';
import {
  ThunderboltOutlined,
  AlertOutlined,
  SwapRightOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';

const suggestionCards = [
  { icon: <ThunderboltOutlined />, label: '检查系统状态', color: '#2563EB' },
  { icon: <AlertOutlined />, label: '查看告警信息', color: '#DC2626' },
  { icon: <SwapRightOutlined />, label: '执行自动化任务', color: '#059669' },
  { icon: <QuestionCircleOutlined />, label: '平台功能介绍', color: '#7C3AED' },
];

export default function EmptyState({
  onSuggestionClick,
}: {
  onSuggestionClick: (label: string) => void;
}) {
  const { token } = theme.useToken();

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        padding: '0 24px',
        textAlign: 'center',
      }}
    >
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: 16,
          background: `linear-gradient(135deg, ${token.colorPrimary}, ${token.colorPrimaryActive})`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fff',
          fontSize: 26,
          fontWeight: 700,
          marginBottom: 20,
        }}
      >
        A
      </div>
      <Typography.Title
        level={3}
        style={{ margin: 0, fontSize: 24, fontWeight: 600, color: token.colorText }}
      >
        有什么可以帮你的？
      </Typography.Title>
      <Typography.Text
        style={{ color: token.colorTextSecondary, fontSize: 14, marginTop: 8, marginBottom: 32 }}
      >
        AIOpsOS 智能运维助手，输入问题开始对话
      </Typography.Text>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: 10,
          width: '100%',
          maxWidth: 480,
        }}
      >
        {suggestionCards.map((card) => (
          <button
            key={card.label}
            onClick={() => onSuggestionClick(card.label)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '14px 16px',
              border: `1px solid ${token.colorBorder}`,
              borderRadius: 12,
              background: token.colorBgContainer,
              cursor: 'pointer',
              transition: 'all 0.2s ease',
              fontSize: 14,
              color: token.colorText,
              textAlign: 'left',
              fontFamily: 'inherit',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = card.color;
              e.currentTarget.style.boxShadow = token.boxShadowTertiary;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = token.colorBorder;
              e.currentTarget.style.boxShadow = 'none';
            }}
          >
            <span style={{ color: card.color, fontSize: 18 }}>{card.icon}</span>
            <span>{card.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
