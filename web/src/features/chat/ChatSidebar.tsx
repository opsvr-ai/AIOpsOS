import { useEffect, useState, useCallback } from 'react';
import { Button, Typography, theme, Spin, Empty, App } from 'antd';
import {
  PlusOutlined,
  DeleteOutlined,
  MessageOutlined,
  ThunderboltOutlined,
  AlertOutlined,
  QuestionCircleOutlined,
  SettingOutlined,
  WarningOutlined,
  InfoCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons';
import { useChatStore, type SessionInfo, type ChatMessage } from '@/stores/chatStore';
import api from '@/services/api';

function sessionIcon(title: string) {
  const t = (title ?? '').toLowerCase();
  const style = { fontSize: 14, flexShrink: 0 } as const;

  if (/告警|报警|alert|warning|故障|异常|error/i.test(t)) return <AlertOutlined style={style} />;
  if (/状态|健康|性能|status|health|负载|cpu|内存|disk/i.test(t))
    return <ThunderboltOutlined style={style} />;
  if (/怎么|如何|为什么|what|how|why|请问|帮助|功能|介绍|用法/i.test(t))
    return <QuestionCircleOutlined style={style} />;
  if (/配置|设置|部署|setup|deploy|install|config/i.test(t))
    return <SettingOutlined style={style} />;
  if (/风险|安全|漏洞|权限|security|vuln|risk/i.test(t)) return <WarningOutlined style={style} />;
  if (/查看|查询|show|list|get|fetch|find|search|统计|分析/i.test(t))
    return <InfoCircleOutlined style={style} />;
  return <MessageOutlined style={style} />;
}

export default function ChatSidebar() {
  const { sessionId, setSessionId, sessions, setSessions, setMessages, isRunning, _refreshTick } =
    useChatStore();
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const { message } = App.useApp();
  const { token } = theme.useToken();

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/sessions');
      setSessions(res.data ?? []);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [setSessions]);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions, _refreshTick]);

  const handleNew = () => {
    setSessionId(null);
    setMessages([]);
  };

  const handleSelect = (sid: string) => {
    if (sid === sessionId) return;
    setSessionId(sid);
    api
      .get(`/sessions/${sid}`)
      .then((res) => {
        const raw = res.data?.messages ?? [];
        const msgs = raw
          .filter(
            (m: { role: string; type?: string }) =>
              m.role !== 'system' || m.type === 'intent' || m.type === 'plan',
          )
          .map(
            (m: {
              id: string;
              role: string;
              content: string;
              type?: string;
              created_at: string;
              extra_metadata?: Record<string, unknown>;
            }) => {
              const base: ChatMessage = {
                id: m.id,
                role: m.role as ChatMessage['role'],
                content: m.content,
                type: (m.type as ChatMessage['type']) || 'text',
                timestamp: new Date(m.created_at).getTime(),
              };
              if (m.extra_metadata?.execution_steps) {
                base.executionSteps = m.extra_metadata
                  .execution_steps as ChatMessage['executionSteps'];
              }
              return base;
            },
          );
        setMessages(msgs);
      })
      .catch(() => setMessages([]));
  };

  const handleDelete = async (e: React.MouseEvent, sid: string) => {
    e.stopPropagation();
    try {
      await api.delete(`/sessions/${sid}`);
      if (sid === sessionId) {
        setSessionId(null);
        setMessages([]);
      }
      fetchSessions();
      message.success('已删除');
    } catch {
      message.error('删除失败');
    }
  };

  // Format: yyyy-mm-dd HH:mm:ss
  const formatDate = (s: string) => {
    const d = new Date(s);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };

  const width = collapsed ? 0 : 280;

  return (
    <>
      {/* Collapse toggle — floating tab when collapsed, inline when expanded */}
      {collapsed ? (
        <div
          style={{
            position: 'absolute',
            top: 12,
            left: 0,
            zIndex: 20,
          }}
        >
          <Button
            type="text"
            size="small"
            icon={<MenuUnfoldOutlined />}
            onClick={() => setCollapsed(false)}
            style={{
              color: token.colorTextTertiary,
              background: token.colorBgContainer,
              borderRadius: '0 8px 8px 0',
              border: `1px solid ${token.colorBorderSecondary}`,
              borderLeft: 'none',
              height: 32,
              width: 24,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
            }}
          />
        </div>
      ) : (
        <div
          style={{
            width,
            flexShrink: 0,
            borderRight: `1px solid ${token.colorBorderSecondary}`,
            background: token.colorBgLayout,
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
            overflow: 'hidden',
            transition: 'width 0.2s ease',
          }}
        >
          {/* Header */}
          <div
            style={{
              padding: '12px 16px',
              borderBottom: `1px solid ${token.colorBorderSecondary}`,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <Button
              type="dashed"
              icon={<PlusOutlined />}
              onClick={handleNew}
              style={{ borderRadius: 8, height: 36, fontSize: 13, flex: 1 }}
            >
              新建对话
            </Button>
            <Button
              type="text"
              size="small"
              icon={<MenuFoldOutlined />}
              onClick={() => setCollapsed(true)}
              style={{ color: token.colorTextTertiary, flexShrink: 0 }}
            />
          </div>

          {/* Session list */}
          <div style={{ flex: 1, overflow: 'auto', padding: '8px 0' }}>
            {loading ? (
              <div style={{ textAlign: 'center', paddingTop: 40 }}>
                <Spin size="small" />
              </div>
            ) : sessions.length === 0 ? (
              <Empty
                description="暂无对话"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                style={{ paddingTop: 40 }}
              />
            ) : (
              sessions.map((s: SessionInfo) => {
                const active = s.id === sessionId;
                return (
                  <div
                    key={s.id}
                    onClick={() => handleSelect(s.id)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: '10px 16px',
                      cursor: 'pointer',
                      transition: 'background 0.15s',
                      background: active ? token.colorFillSecondary : 'transparent',
                      borderRight: active
                        ? `3px solid ${token.colorPrimary}`
                        : '3px solid transparent',
                    }}
                    onMouseEnter={(e) => {
                      if (!active) e.currentTarget.style.background = token.colorFillQuaternary;
                    }}
                    onMouseLeave={(e) => {
                      if (!active) e.currentTarget.style.background = 'transparent';
                    }}
                  >
                    {sessionIcon(s.title || '')}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Typography.Text
                        style={{
                          fontSize: 13,
                          color: active ? token.colorPrimary : token.colorText,
                          display: 'block',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          fontWeight: active ? 600 : 400,
                        }}
                      >
                        {s.title || '新对话'}
                      </Typography.Text>
                      <Typography.Text style={{ fontSize: 11, color: token.colorTextTertiary }}>
                        {formatDate(s.updated_at || s.created_at)}
                      </Typography.Text>
                    </div>
                    {!isRunning && (
                      <DeleteOutlined
                        onClick={(e) => handleDelete(e, s.id)}
                        className="chat-sidebar-delete"
                        style={{ fontSize: 12, flexShrink: 0 }}
                      />
                    )}
                  </div>
                );
              })
            )}
          </div>

          <style>{`
            .chat-sidebar-delete { opacity: 0; transition: opacity 0.15s, color 0.15s; cursor: pointer; color: inherit; }
            div:hover > .chat-sidebar-delete { opacity: 1 !important; }
            .chat-sidebar-delete:hover { color: ${token.colorError} !important; }
          `}</style>
        </div>
      )}
    </>
  );
}
