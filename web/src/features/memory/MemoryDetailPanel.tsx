import { useState, useEffect, useCallback } from 'react';
import { Typography, Tag, Button, Spin, Empty, theme, App } from 'antd';
import {
  CloseOutlined,
  UserOutlined,
  TeamOutlined,
  LinkOutlined,
  LoadingOutlined,
  TagsOutlined,
} from '@ant-design/icons';
import { useMemoryStore, type MemoryEntry } from '@/stores/memoryStore';
import api from '@/services/api';

interface MemoryDetailPanelProps {
  memoryId: string;
  onClose: () => void;
  onFocusTag: (tag: string) => void;
  onSelectMemory: (id: string) => void;
}

export default function MemoryDetailPanel({
  memoryId,
  onClose,
  onFocusTag,
  onSelectMemory,
}: MemoryDetailPanelProps) {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const { deleteMemory } = useMemoryStore();
  const [memory, setMemory] = useState<MemoryEntry | null>(null);
  const [related, setRelated] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!memoryId) return;
    setLoading(true);
    Promise.all([
      api.get(`/memories/${memoryId}`).then((r) => r.data),
      api.get(`/memories/${memoryId}/related`).then((r) => r.data ?? []),
    ])
      .then(([mem, rel]) => {
        setMemory(mem);
        setRelated(rel);
      })
      .catch(() => setMemory(null))
      .finally(() => setLoading(false));
  }, [memoryId]);

  const handleDelete = useCallback(async () => {
    try {
      await deleteMemory(memoryId);
      message.success('已删除');
      onClose();
    } catch {
      message.error('删除失败');
    }
  }, [memoryId, deleteMemory, message, onClose]);

  if (loading) {
    return (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: token.colorBgContainer,
        }}
      >
        <Spin indicator={<LoadingOutlined style={{ fontSize: 20 }} spin />} />
      </div>
    );
  }

  if (!memory) {
    return (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: token.colorBgContainer,
        }}
      >
        <Empty description="记忆未找到" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </div>
    );
  }

  const scopeColor = memory.scope === 'team' ? '#52c41a' : '#4a90d9';
  const scopeLabel = memory.scope === 'team' ? '组织记忆' : '个人记忆';
  const scopeIcon = memory.scope === 'team' ? <TeamOutlined /> : <UserOutlined />;

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        background: token.colorBgContainer,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '12px 16px',
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <Typography.Text strong style={{ fontSize: 14 }}>
          记忆详情
        </Typography.Text>
        <Button type="text" size="small" icon={<CloseOutlined />} onClick={onClose} />
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'auto', padding: '16px' }}>
        {/* Scope badge + title */}
        <div style={{ marginBottom: 12 }}>
          <Tag
            color={scopeColor}
            icon={scopeIcon}
            style={{ borderRadius: 4, marginBottom: 8, fontSize: 11 }}
          >
            {scopeLabel}
          </Tag>
          <Typography.Title level={5} style={{ margin: '8px 0 0' }}>
            {memory.title || '未命名记忆'}
          </Typography.Title>
        </div>

        {/* Content */}
        <div
          style={{
            background: token.colorFillTertiary,
            borderRadius: 8,
            padding: '12px',
            marginBottom: 16,
            fontSize: 13,
            lineHeight: 1.7,
            color: token.colorText,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: 280,
            overflow: 'auto',
          }}
        >
          {memory.content || '无内容'}
        </div>

        {/* Tags */}
        {memory.tags && memory.tags.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <Typography.Text
              type="secondary"
              style={{ fontSize: 11, marginBottom: 6, display: 'block' }}
            >
              <TagsOutlined style={{ marginRight: 4 }} />
              标签
            </Typography.Text>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {memory.tags.map((t) => (
                <Tag
                  key={t}
                  style={{
                    cursor: 'pointer',
                    borderRadius: 4,
                    fontSize: 11,
                    border: '1px solid #f0a500',
                    color: '#d4a017',
                    background: '#fffbe6',
                  }}
                  onClick={() => onFocusTag(t)}
                >
                  {t}
                </Tag>
              ))}
            </div>
          </div>
        )}

        {/* Session link */}
        {memory.session_id && (
          <div style={{ marginBottom: 16 }}>
            <Typography.Text
              type="secondary"
              style={{ fontSize: 11, marginBottom: 4, display: 'block' }}
            >
              来源会话
            </Typography.Text>
            <Typography.Link
              style={{ fontSize: 12 }}
              onClick={() => {
                window.open(`/chat?session=${memory.session_id}`, '_self');
              }}
            >
              <LinkOutlined style={{ marginRight: 4 }} />
              {memory.session_title || memory.session_id.slice(0, 8)}
            </Typography.Link>
          </div>
        )}

        {/* Related memories */}
        {related.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <Typography.Text
              type="secondary"
              style={{ fontSize: 11, marginBottom: 6, display: 'block' }}
            >
              相关记忆 ({related.length})
            </Typography.Text>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {related.map((r) => (
                <div
                  key={r.id}
                  onClick={() => onSelectMemory(r.id)}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 6,
                    cursor: 'pointer',
                    background: token.colorFillQuaternary,
                    border: '1px solid transparent',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = token.colorPrimaryBorder;
                    e.currentTarget.style.background = token.colorPrimaryBg;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = 'transparent';
                    e.currentTarget.style.background = token.colorFillQuaternary;
                  }}
                >
                  <Typography.Text
                    style={{
                      fontSize: 12,
                      display: 'block',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      fontWeight: 500,
                    }}
                  >
                    {r.title || '未命名记忆'}
                  </Typography.Text>
                  <Typography.Text
                    type="secondary"
                    style={{
                      fontSize: 11,
                      display: 'block',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      marginTop: 2,
                    }}
                  >
                    {r.content?.slice(0, 60) ?? ''}
                    {(r.content?.length ?? 0) > 60 ? '...' : ''}
                  </Typography.Text>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div
        style={{
          padding: '10px 16px',
          borderTop: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <Button
          danger
          size="small"
          block
          onClick={handleDelete}
          style={{ borderRadius: 6, fontSize: 12 }}
        >
          删除此记忆
        </Button>
      </div>
    </div>
  );
}
