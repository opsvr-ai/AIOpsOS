import { useEffect, useState } from 'react';
import { Typography, theme, Skeleton } from 'antd';
import {
  ThunderboltOutlined,
  AlertOutlined,
  SwapRightOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';
import api from '@/services/api';

interface Suggestion {
  label: string;
  prompt: string;
}

const FALLBACK_CARDS: (Suggestion & { icon: React.ReactNode; color: string })[] = [
  {
    icon: <ThunderboltOutlined />,
    label: '检查系统状态',
    prompt: '查看系统当前运行状态',
    color: '#2563EB',
  },
  { icon: <AlertOutlined />, label: '查看告警信息', prompt: '查看最近告警列表', color: '#DC2626' },
  {
    icon: <SwapRightOutlined />,
    label: '执行自动化任务',
    prompt: '帮我执行一个自动化运维任务',
    color: '#059669',
  },
  {
    icon: <QuestionCircleOutlined />,
    label: '平台功能介绍',
    prompt: '平台有哪些功能和使用方式',
    color: '#7C3AED',
  },
];

const CARD_COLORS = ['#2563EB', '#DC2626', '#059669', '#7C3AED'];

export default function EmptyState({
  onSuggestionClick,
}: {
  onSuggestionClick: (prompt: string) => void;
}) {
  const { token } = theme.useToken();
  const [cards, setCards] = useState<Suggestion[] | null>(null);

  useEffect(() => {
    api
      .get('/sessions/recommendations')
      .then((res) => {
        if (Array.isArray(res.data) && res.data.length > 0) {
          setCards(res.data);
        } else {
          setCards(FALLBACK_CARDS);
        }
      })
      .catch(() => setCards(FALLBACK_CARDS));
  }, []);

  const displayCards = cards || FALLBACK_CARDS;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '0 24px',
        textAlign: 'center',
        width: '100%',
      }}
    >
      <div
        style={{
          width: 64,
          height: 64,
          borderRadius: 18,
          background: token.colorPrimaryBg,
          border: `2px solid ${token.colorPrimaryBorder}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: token.colorPrimary,
          fontSize: 28,
          fontWeight: 700,
          marginBottom: 24,
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
          letterSpacing: -0.3,
        }}
      >
        有什么可以帮你？
      </Typography.Title>
      <Typography.Text
        style={{
          color: token.colorTextSecondary,
          fontSize: 14,
          marginTop: 8,
          marginBottom: 36,
          lineHeight: 1.6,
        }}
      >
        AIOpsOS 智能运维助手，输入问题开始对话
      </Typography.Text>

      {!cards ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: 12,
            width: '100%',
            maxWidth: 500,
          }}
        >
          {[1, 2, 3, 4].map((i) => (
            <div key={i} style={{ borderRadius: 14, overflow: 'hidden' }}>
              <Skeleton.Input active block style={{ height: 64 }} />
            </div>
          ))}
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: 12,
            width: '100%',
            maxWidth: 500,
          }}
        >
          {displayCards.map((card, idx) => (
            <button
              key={card.label}
              onClick={() => onSuggestionClick(card.prompt)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '16px 18px',
                border: `1px solid ${token.colorBorderSecondary}`,
                borderRadius: 14,
                background: token.colorBgContainer,
                cursor: 'pointer',
                transition: 'all 0.2s ease',
                fontSize: 14,
                color: token.colorText,
                textAlign: 'left',
                fontFamily: 'inherit',
              }}
              onMouseEnter={(e) => {
                const color = CARD_COLORS[idx % CARD_COLORS.length];
                e.currentTarget.style.borderColor = color;
                e.currentTarget.style.background = token.colorPrimaryBg;
                e.currentTarget.style.boxShadow = `0 4px 16px ${color}20`;
                e.currentTarget.style.transform = 'translateY(-1px)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = token.colorBorderSecondary;
                e.currentTarget.style.background = token.colorBgContainer;
                e.currentTarget.style.boxShadow = 'none';
                e.currentTarget.style.transform = 'translateY(0)';
              }}
            >
              <span
                style={{
                  color: CARD_COLORS[idx % CARD_COLORS.length],
                  fontSize: 20,
                  width: 36,
                  height: 36,
                  borderRadius: 10,
                  background: `${CARD_COLORS[idx % CARD_COLORS.length]}14`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                {FALLBACK_CARDS[idx % FALLBACK_CARDS.length].icon}
              </span>
              <span style={{ fontWeight: 500 }}>{card.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
