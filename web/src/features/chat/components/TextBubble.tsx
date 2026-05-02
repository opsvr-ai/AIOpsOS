import { useState, useEffect, useRef, useMemo } from 'react';
import { theme } from 'antd';
import { DownOutlined, RightOutlined } from '@ant-design/icons';
import { useThemeStore } from '@/stores/themeStore';
import type { ChatMessage } from '@/stores/chatStore';
import { getSharedProcessor } from '../a2ui/sharedProcessor';
import AgentAvatar from './AgentAvatar';
import UserAvatar from './UserAvatar';
import MessageActions from './MessageActions';
import MarkdownContent from '../MarkdownContent';
import ExecutionTimeline from '../ExecutionTimeline';
import A2UISurface from './A2UISurface';

export default function TextBubble({
  msg,
  isLatestUser,
  agentState,
  userState,
  onEdit,
  onRegenerate,
}: {
  msg: ChatMessage;
  isLatestUser: boolean;
  agentState: 'idle' | 'thinking' | 'planning' | 'executing';
  userState: 'sleeping' | 'waiting' | 'reading';
  onEdit: () => void;
  onRegenerate: () => void;
}) {
  const { token } = theme.useToken();
  const isDark = useThemeStore((s) => s.mode) === 'dark';
  const [showExec, setShowExec] = useState(false);
  const userToggledRef = useRef(false);

  const isUser = msg.role === 'user';
  const displayContent = msg.streaming ? (msg.streamedContent ?? '') : msg.content;
  const isStreaming = !!msg.streaming;
  const hasExecSteps = msg.executionSteps && msg.executionSteps.length > 0;
  const isThinking = isStreaming && !isUser && displayContent.length === 0;
  const showStreamingCursor = isStreaming && !isUser && displayContent.length > 0;

  useEffect(() => {
    if ((isStreaming || agentState === 'executing') && hasExecSteps && !userToggledRef.current) {
      setShowExec(true);
    }
    if (!isStreaming && agentState === 'idle' && !userToggledRef.current) {
      setShowExec(false);
    }
  }, [isStreaming, hasExecSteps, agentState]);

  const bubbleBg = isUser
    ? `linear-gradient(135deg, ${token.colorPrimary}, ${token.colorPrimaryActive})`
    : isDark
      ? token.colorFillQuaternary
      : (token.colorBgElevated ?? token.colorBgContainer);
  const bubbleBorder = isUser ? 'none' : `1px solid ${token.colorBorderSecondary}`;
  const bubbleRadius = isUser ? '20px 20px 6px 20px' : '20px 20px 20px 6px';
  const bubbleShadow = isUser
    ? `0 2px 12px ${token.colorPrimary}30`
    : isDark
      ? 'none'
      : '0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)';
  const us = isLatestUser ? userState : 'sleeping';

  // Client-side A2UI fallback — extract JSON from content even if backend
  // marker detection failed (e.g. markers split across streaming tokens)
  const a2uiFallback = useMemo(() => {
    if (msg.role !== 'assistant' || msg.streaming) return null;
    const content = msg.content;
    const sidx = content.indexOf('[A2UI_START]');
    if (sidx === -1) return null;
    const eidx = content.indexOf('[A2UI_END]', sidx + 13);
    if (eidx === -1) return null;
    const raw = content.slice(sidx + 13, eidx).trim();
    try {
      // Strip markdown code fences if present
      let json = raw;
      if (json.startsWith('```')) {
        json = json
          .replace(/^```[a-z]*\n?/, '')
          .replace(/\n?```$/, '')
          .trim();
      }
      const msgs = JSON.parse(json);
      const proc = getSharedProcessor();
      proc.processMessages(msgs);
      const createMsg = msgs.find((m: any) => m.createSurface);
      return createMsg?.createSurface?.surfaceId || 'msg-fallback';
    } catch {
      return null;
    }
  }, [msg.content, msg.role, msg.streaming]);

  // A2UI interactive surface — render instead of markdown bubble
  if ((msg.type === 'a2ui' && msg.a2uiSurfaceId) || a2uiFallback) {
    const surfaceId = msg.a2uiSurfaceId || a2uiFallback || 'msg-fallback';
    return (
      <div className="msg-enter" style={{ padding: '4px 16px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto', width: '100%' }}>
          <A2UISurface surfaceId={surfaceId} />
        </div>
      </div>
    );
  }

  // Report card — clickable link to report viewer
  if (msg.type === 'report' && msg.reportUrl) {
    return (
      <div className="msg-enter" style={{ padding: '4px 16px' }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-start',
            maxWidth: 900,
            margin: '0 auto',
            width: '100%',
            paddingLeft: 46,
          }}
        >
          <a
            href={msg.reportUrl}
            onClick={(e) => {
              e.preventDefault();
              window.open(msg.reportUrl, '_blank');
            }}
            style={{ textDecoration: 'none', width: '100%', maxWidth: 420 }}
          >
            <div
              style={{
                padding: '14px 18px',
                borderRadius: 12,
                border: `1px solid ${token.colorBorderSecondary}`,
                background: isDark ? token.colorFillQuaternary : token.colorBgElevated,
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                cursor: 'pointer',
                transition: 'box-shadow 0.2s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.boxShadow = '0 2px 12px rgba(0,0,0,0.08)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.boxShadow = 'none';
              }}
            >
              <span style={{ fontSize: 24 }}>📊</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 14, color: token.colorText }}>
                  {msg.reportTitle || 'Report'}
                </div>
                <div style={{ fontSize: 12, color: token.colorTextSecondary, marginTop: 2 }}>
                  Click to view full report →
                </div>
              </div>
            </div>
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="msg-enter" style={{ padding: '4px 16px' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: isUser ? 'flex-end' : 'flex-start',
          alignItems: 'flex-start',
          gap: 10,
          maxWidth: 900,
          margin: '0 auto',
          width: '100%',
        }}
      >
        {!isUser && <AgentAvatar state={agentState} />}
        <div style={{ maxWidth: '85%', minWidth: 0 }}>
          {hasExecSteps && !isUser && (
            <div style={{ marginBottom: 10 }}>
              <div
                onClick={() => {
                  userToggledRef.current = true;
                  setShowExec(!showExec);
                }}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 14px',
                  borderRadius: 10,
                  cursor: 'pointer',
                  fontSize: 13,
                  color: isStreaming ? token.colorPrimary : token.colorTextSecondary,
                  background: isStreaming ? token.colorPrimaryBg : token.colorFillQuaternary,
                  border: isStreaming
                    ? `1px solid ${token.colorPrimaryBorder}`
                    : `1px solid ${token.colorBorderSecondary}`,
                  fontWeight: 500,
                  transition: 'all 0.2s ease',
                  userSelect: 'none',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = token.colorPrimary;
                  e.currentTarget.style.borderColor = token.colorPrimaryBorder;
                  e.currentTarget.style.background = token.colorPrimaryBg;
                }}
                onMouseLeave={(e) => {
                  if (!isStreaming) {
                    e.currentTarget.style.color = token.colorTextSecondary;
                    e.currentTarget.style.borderColor = token.colorBorderSecondary;
                    e.currentTarget.style.background = token.colorFillQuaternary;
                  }
                }}
              >
                {showExec ? (
                  <DownOutlined style={{ fontSize: 10 }} />
                ) : (
                  <RightOutlined style={{ fontSize: 10 }} />
                )}
                {isStreaming
                  ? `生成中 · ${msg.executionSteps!.filter((s) => s.status === 'done').length} 个工具已执行`
                  : `执行详情 · ${msg.executionSteps!.length} 个步骤`}
              </div>
              {showExec && (
                <div style={{ marginTop: 8 }}>
                  <ExecutionTimeline steps={msg.executionSteps!} />
                </div>
              )}
            </div>
          )}
          <div
            className={showStreamingCursor ? 'streaming-content' : ''}
            key={showStreamingCursor ? displayContent.slice(-20) : undefined}
            style={{
              padding: isThinking ? '10px 18px' : '12px 18px',
              borderRadius: bubbleRadius,
              background: bubbleBg,
              border: bubbleBorder,
              color: isUser ? '#fff' : token.colorText,
              fontSize: 14,
              lineHeight: 1.68,
              overflow: 'hidden',
              boxShadow: bubbleShadow,
              minHeight: isThinking ? 40 : undefined,
              display: isThinking ? 'flex' : undefined,
              alignItems: isThinking ? 'center' : undefined,
              gap: isThinking ? 4 : undefined,
            }}
          >
            {isThinking ? (
              <>
                <span className="typing-dot" />
                <span className="typing-dot" />
                <span className="typing-dot" />
              </>
            ) : isUser ? (
              <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {displayContent}
              </span>
            ) : (
              <MarkdownContent>{displayContent}</MarkdownContent>
            )}
            {showStreamingCursor && <span className="streaming-cursor" />}
          </div>
          {isUser && !isStreaming && (
            <MessageActions msg={msg} onEdit={onEdit} onRegenerate={onRegenerate} isUser />
          )}
        </div>
        {isUser && <UserAvatar state={us} />}
      </div>
      {!isUser && !isStreaming && msg.type !== 'exec' && (
        <div style={{ maxWidth: 900, margin: '0 auto', width: '100%', paddingLeft: 46 }}>
          <MessageActions msg={msg} onEdit={onEdit} onRegenerate={onRegenerate} isUser={false} />
        </div>
      )}
    </div>
  );
}
