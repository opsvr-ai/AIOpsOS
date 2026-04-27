import { useState } from 'react';
import { Typography, Tag, Button, Popconfirm, theme, App, Checkbox } from 'antd';
import {
  ArrowRightOutlined,
  DeleteOutlined,
  UserOutlined,
  TeamOutlined,
  DownOutlined,
  UpOutlined,
} from '@ant-design/icons';
import type { MemoryEntry } from '@/stores/memoryStore';

interface MemoryCardProps {
  memory: MemoryEntry;
  onDelete: (id: string) => Promise<void>;
  selected?: boolean;
  onSelect?: (id: string, checked: boolean) => void;
  onTagClick?: (tag: string) => void;
}

export default function MemoryCard({
  memory,
  onDelete,
  selected = false,
  onSelect,
  onTagClick,
}: MemoryCardProps) {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const [expanded, setExpanded] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const isPersonal = memory.scope === 'personal';

  const handleDelete = async (e?: React.MouseEvent) => {
    e?.stopPropagation();
    setDeleting(true);
    try {
      await onDelete(memory.id);
      message.success('已删除');
    } catch {
      message.error('删除失败');
    } finally {
      setDeleting(false);
    }
  };

  const formattedDate = memory.created_at
    ? new Date(memory.created_at).toLocaleString('zh-CN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';

  return (
    <div
      className="memory-card"
      style={{
        background: token.colorBgElevated ?? token.colorBgContainer,
        border: `1px solid ${selected ? token.colorPrimary : token.colorBorderSecondary}`,
        borderRadius: 10,
        overflow: 'hidden',
        transition: 'box-shadow 0.2s, border-color 0.2s, transform 0.15s',
        boxShadow: selected ? `0 0 0 2px ${token.colorPrimary}20` : '0 1px 3px rgba(0,0,0,0.04)',
        cursor: 'default',
        position: 'relative',
      }}
    >
      {/* Selection checkbox — top right */}
      {onSelect && (
        <div
          style={{ position: 'absolute', top: 8, right: 8, zIndex: 2 }}
          onClick={(e) => e.stopPropagation()}
        >
          <Checkbox checked={selected} onChange={() => onSelect(memory.id, !selected)} />
        </div>
      )}

      {/* Clickable body */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{ padding: '14px 16px', cursor: 'pointer' }}
      >
        {/* Header row: scope badge + expand icon */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 8,
            paddingRight: onSelect ? 22 : 0,
          }}
        >
          <Tag
            color={isPersonal ? 'blue' : 'green'}
            icon={isPersonal ? <UserOutlined /> : <TeamOutlined />}
            style={{ margin: 0, fontSize: 10, borderRadius: 4, lineHeight: '18px' }}
          >
            {isPersonal ? '个人' : '组织'}
          </Tag>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            {formattedDate && (
              <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                {formattedDate}
              </Typography.Text>
            )}
            {expanded ? (
              <UpOutlined style={{ fontSize: 10, color: token.colorTextTertiary }} />
            ) : (
              <DownOutlined style={{ fontSize: 10, color: token.colorTextTertiary }} />
            )}
          </span>
        </div>

        {/* Title */}
        <Typography.Text
          strong
          style={{
            fontSize: 13,
            color: token.colorText,
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            lineHeight: 1.5,
            marginBottom: 4,
          }}
        >
          {memory.title || '未命名记忆'}
        </Typography.Text>

        {/* Content preview */}
        <Typography.Paragraph
          type="secondary"
          style={{
            fontSize: 11,
            lineHeight: 1.5,
            margin: '6px 0 0',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {memory.content?.slice(0, 100) || '无内容'}
        </Typography.Paragraph>

        {/* Tags row */}
        {memory.tags && memory.tags.length > 0 && (
          <div
            style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 10 }}
            onClick={(e) => e.stopPropagation()}
          >
            {memory.tags.map((tag) => (
              <Tag
                key={tag}
                style={{
                  margin: 0,
                  fontSize: 10,
                  borderRadius: 4,
                  cursor: 'pointer',
                  background: token.colorFillSecondary,
                  border: 'none',
                  color: token.colorTextSecondary,
                  padding: '0 6px',
                  lineHeight: '20px',
                  transition: 'background 0.15s',
                }}
                onClick={() => onTagClick?.(tag)}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = token.colorFillTertiary;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = token.colorFillSecondary;
                }}
              >
                {tag}
              </Tag>
            ))}
          </div>
        )}

        {/* Session link */}
        {memory.session_title && (
          <Typography.Text
            type="secondary"
            style={{ fontSize: 10, display: 'block', marginTop: 8 }}
          >
            来自: {memory.session_title}
          </Typography.Text>
        )}
      </div>

      {/* Expandable full content */}
      {expanded && (
        <div
          style={{
            padding: '0 16px 14px',
            borderTop: `1px solid ${token.colorBorderSecondary}`,
            margin: '0 16px',
          }}
        >
          <div
            style={{
              paddingTop: 10,
              fontSize: 12,
              color: token.colorTextSecondary,
              lineHeight: 1.65,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: 200,
              overflow: 'auto',
            }}
          >
            {memory.content}
          </div>

          <div
            style={{
              marginTop: 10,
              display: 'flex',
              gap: 6,
              justifyContent: 'flex-end',
            }}
          >
            {memory.session_id && (
              <Button
                size="small"
                type="link"
                icon={<ArrowRightOutlined />}
                style={{ fontSize: 11 }}
                onClick={(e) => {
                  e.stopPropagation();
                  window.open(`/ops/chat?session=${memory.session_id}`, '_self');
                }}
              >
                查看会话
              </Button>
            )}
            <Popconfirm
              title="删除这条记忆？"
              onConfirm={() => handleDelete()}
              okText="删除"
              cancelText="取消"
            >
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={deleting}
                style={{ borderRadius: 6, fontSize: 11 }}
                onClick={(e) => e.stopPropagation()}
              />
            </Popconfirm>
          </div>
        </div>
      )}
    </div>
  );
}
