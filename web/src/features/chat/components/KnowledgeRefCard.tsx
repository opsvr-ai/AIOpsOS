import { Card, Typography, Space, Tag, theme } from 'antd';
import {
  BookOutlined,
  FileTextOutlined,
  SearchOutlined,
} from '@ant-design/icons';

const { Text, Paragraph } = Typography;

interface KnowledgeSnippet {
  id: string;
  content: string;
  source: string;
  score?: number;
  type?: 'wiki' | 'memory' | 'search';
}

interface Props {
  snippets: KnowledgeSnippet[];
  collapsed?: boolean;
}

const TYPE_CONFIG: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  wiki: { icon: <BookOutlined />, color: '#1677ff', label: 'Wiki' },
  memory: { icon: <SearchOutlined />, color: '#52c41a', label: '记忆' },
  search: { icon: <FileTextOutlined />, color: '#722ed1', label: '检索' },
};

export default function KnowledgeRefCard({ snippets, collapsed }: Props) {
  const { token } = theme.useToken();

  if (!snippets || snippets.length === 0) return null;

  return (
    <Card
      size="small"
      style={{
        maxWidth: 500,
        borderRadius: 12,
        border: `1px solid ${token.colorInfoBorder}`,
        boxShadow: `0 1px 8px ${token.colorInfoBg}`,
      }}
      title={
        <Space>
          <BookOutlined style={{ color: token.colorInfo }} />
          <Text strong style={{ fontSize: 13, color: token.colorInfoText }}>
            知识引用 ({snippets.length})
          </Text>
        </Space>
      }
      headStyle={{
        background: token.colorInfoBg,
        borderBottom: `1px solid ${token.colorInfoBorder}`,
        padding: '8px 14px',
        minHeight: 'auto',
      }}
    >
      {snippets.map((s, i) => {
        const cfg = TYPE_CONFIG[s.type || 'search'] || TYPE_CONFIG.search;
        return (
          <div
            key={s.id || i}
            style={{
              padding: '8px 0',
              borderBottom: i < snippets.length - 1
                ? `1px solid ${token.colorBorderSecondary}` : 'none',
            }}
          >
            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 4,
            }}>
              <Space size={4}>
                <span style={{ color: cfg.color, fontSize: 12 }}>{cfg.icon}</span>
                <Tag color={cfg.color} style={{ borderRadius: 4, fontSize: 10, margin: 0 }}>
                  {cfg.label}
                </Tag>
              </Space>
              {s.score !== undefined && (
                <Text type="secondary" style={{ fontSize: 10 }}>
                  相似度: {(s.score * 100).toFixed(0)}%
                </Text>
              )}
            </div>
            <Paragraph
              type="secondary"
              ellipsis={collapsed ? { rows: 2 } : { rows: 3 }}
              style={{
                fontSize: 12,
                marginBottom: 2,
                lineHeight: 1.5,
                fontFamily: "'Inter', sans-serif",
              }}
            >
              {s.content}
            </Paragraph>
            {s.source && (
              <Text style={{ fontSize: 10, color: token.colorTextQuaternary }}>
                来源: {s.source}
              </Text>
            )}
          </div>
        );
      })}
    </Card>
  );
}
