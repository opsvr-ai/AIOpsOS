import { useState, useCallback, useEffect, useRef } from 'react';
import { theme, Avatar, Button, App } from 'antd';
import {
  UserOutlined,
  RobotOutlined,
  CaretDownOutlined,
  CaretRightOutlined,
  CopyOutlined,
  EditOutlined,
  CheckOutlined,
  LikeOutlined,
  DislikeOutlined,
  ReloadOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { useThemeStore } from '@/stores/themeStore';
import type { ChatMessage } from '@/stores/chatStore';
import { IntentCard, PlanCard } from '../ChatCards';
import MarkdownContent from '../MarkdownContent';
import ExecutionTimeline from '../ExecutionTimeline';
import InteractiveFormCard from './InteractiveFormCard';

/* ── agent avatar: state-driven icon ─────────────────────────────── */

function AgentAvatar({ state }: { state: 'idle' | 'thinking' | 'planning' | 'executing' }) {
  const { token } = theme.useToken();
  const cls = state === 'idle' ? 'agent-avatar-wave' : `agent-avatar-${state}`;

  if (state === 'executing') {
    return (
      <Avatar
        size={36}
        icon={<ToolOutlined />}
        className={cls}
        style={{
          backgroundColor: token.colorWarningBg,
          color: token.colorWarning,
          flexShrink: 0,
          marginTop: 2,
          border: `2px solid ${token.colorWarningBorder}`,
        }}
      />
    );
  }
  return (
    <Avatar
      size={36}
      icon={<RobotOutlined />}
      className={cls}
      style={{
        background: `linear-gradient(135deg, ${token.colorPrimary}, ${token.colorPrimaryActive})`,
        flexShrink: 0,
        marginTop: 2,
        boxShadow: `0 2px 8px ${token.colorPrimary}40`,
      }}
    />
  );
}

/* ── user avatar: state-driven ───────────────────────────────────── */

function UserAvatar({ state }: { state: 'sleeping' | 'waiting' | 'reading' }) {
  const { token } = theme.useToken();
  const cls = `user-avatar-${state}`;
  return (
    <Avatar
      size={32}
      icon={<UserOutlined />}
      className={cls}
      style={{
        backgroundColor: state === 'sleeping' ? token.colorFill : token.colorPrimaryBg,
        color: state === 'sleeping' ? token.colorTextQuaternary : token.colorTextSecondary,
        flexShrink: 0,
        marginTop: 2,
      }}
    />
  );
}

/* ── message action buttons ──────────────────────────────────────── */

function UserActions({ msg, onEdit }: { msg: ChatMessage; onEdit: () => void }) {
  const { token } = theme.useToken();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(msg.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  }, [msg.content]);

  return (
    <div
      style={{
        display: 'flex',
        gap: 2,
        justifyContent: 'flex-end',
        marginTop: 4,
        opacity: 0.7,
        transition: 'opacity 0.15s',
      }}
      className="msg-actions"
      onMouseEnter={(e) => {
        e.currentTarget.style.opacity = '1';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.opacity = '0.7';
      }}
    >
      <Button
        type="text"
        size="small"
        icon={copied ? <CheckOutlined /> : <CopyOutlined />}
        onClick={handleCopy}
        style={{ color: token.colorTextTertiary, fontSize: 12 }}
      />
      <Button
        type="text"
        size="small"
        icon={<EditOutlined />}
        onClick={onEdit}
        style={{ color: token.colorTextTertiary, fontSize: 12 }}
      />
    </div>
  );
}

function AssistActions({ msg, onRegenerate }: { msg: ChatMessage; onRegenerate: () => void }) {
  const { token } = theme.useToken();
  const { message } = App.useApp();
  const [copied, setCopied] = useState(false);
  const [liked, setLiked] = useState<'up' | 'down' | null>(null);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(msg.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      message.success('已复制');
    } catch {
      /* ignore */
    }
  }, [msg.content, message]);

  const handleThumbs = (t: 'up' | 'down') => {
    setLiked((prev) => (prev === t ? null : t));
  };

  return (
    <div
      style={{
        display: 'flex',
        gap: 2,
        marginTop: 4,
        opacity: 0.6,
        transition: 'opacity 0.15s',
      }}
      className="msg-actions"
      onMouseEnter={(e) => {
        e.currentTarget.style.opacity = '1';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.opacity = '0.6';
      }}
    >
      <Button
        type="text"
        size="small"
        icon={copied ? <CheckOutlined style={{ color: token.colorSuccess }} /> : <CopyOutlined />}
        onClick={handleCopy}
        style={{ color: token.colorTextTertiary, fontSize: 12 }}
      />
      <Button
        type="text"
        size="small"
        icon={<LikeOutlined style={{ color: liked === 'up' ? token.colorPrimary : undefined }} />}
        onClick={() => handleThumbs('up')}
        style={{ color: token.colorTextTertiary, fontSize: 12 }}
      />
      <Button
        type="text"
        size="small"
        icon={
          <DislikeOutlined style={{ color: liked === 'down' ? token.colorError : undefined }} />
        }
        onClick={() => handleThumbs('down')}
        style={{ color: token.colorTextTertiary, fontSize: 12 }}
      />
      <Button
        type="text"
        size="small"
        icon={<ReloadOutlined />}
        onClick={onRegenerate}
        style={{ color: token.colorTextTertiary, fontSize: 12 }}
      />
    </div>
  );
}

/* ── Bubble ───────────────────────────────────────────────────────── */

export default function Bubble({
  msg,
  isLatestUser,
  agentState,
  userState,
  onEdit,
  onRegenerate,
  onFormSubmit,
}: {
  msg: ChatMessage;
  isLatestUser: boolean;
  agentState: 'idle' | 'thinking' | 'planning' | 'executing';
  userState: 'sleeping' | 'waiting' | 'reading';
  onEdit: () => void;
  onRegenerate: () => void;
  onFormSubmit?: (formId: string, values: Record<string, unknown>) => void;
}) {
  const { token } = theme.useToken();
  const mode = useThemeStore((s) => s.mode);
  const isDark = mode === 'dark';
  const [showExec, setShowExec] = useState(false);
  const userToggledRef = useRef(false);

  const isUser = msg.role === 'user';
  const displayContent = msg.streaming ? (msg.streamedContent ?? '') : msg.content;
  const isStreaming = !!msg.streaming;
  const hasExecSteps = msg.executionSteps && msg.executionSteps.length > 0;

  // Auto-expand execution steps during streaming or when agent is running
  useEffect(() => {
    if ((isStreaming || agentState === 'executing') && hasExecSteps && !userToggledRef.current) {
      setShowExec(true);
    }
    if (!isStreaming && agentState === 'idle' && !userToggledRef.current) {
      setShowExec(false);
    }
  }, [isStreaming, hasExecSteps, agentState]);

  /* ── early-return types ──────────────────────────────────────── */
  if (msg.type === 'intent') {
    return (
      <div className="msg-enter" style={{ padding: '4px 16px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 10,
            maxWidth: 900,
            margin: '0 auto',
            width: '100%',
          }}
        >
          <AgentAvatar state="thinking" />
          <IntentCard message={msg.content} />
        </div>
      </div>
    );
  }

  if (msg.type === 'interactive_form' && msg.formData) {
    return (
      <div className="msg-enter" style={{ padding: '4px 16px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 10,
            maxWidth: 900,
            margin: '0 auto',
            width: '100%',
          }}
        >
          <AgentAvatar state="thinking" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <InteractiveFormCard
              formData={msg.formData}
              onSubmit={(formId, values) => onFormSubmit?.(formId, values)}
              submitted={!!msg.formSubmitted}
            />
          </div>
        </div>
      </div>
    );
  }

  if (msg.type === 'plan' && msg.planSteps) {
    return (
      <div className="msg-enter" style={{ padding: '4px 16px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 10,
            maxWidth: 900,
            margin: '0 auto',
            width: '100%',
          }}
        >
          <AgentAvatar state="planning" />
          <PlanCard steps={msg.planSteps} execResults={msg.execResults ?? []} />
        </div>
      </div>
    );
  }

  const bubbleBg = isUser
    ? token.colorPrimary
    : isDark
      ? token.colorFillQuaternary
      : token.colorFillSecondary;
  const bubbleRadius = isUser ? '18px 18px 4px 18px' : '18px 18px 18px 4px';

  const us = isLatestUser ? userState : 'sleeping';

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
          {/* Execution steps — render BEFORE text output, auto-expand during exec */}
          {hasExecSteps && !isUser && (
            <div style={{ marginBottom: 8 }}>
              <div
                onClick={() => {
                  userToggledRef.current = true;
                  setShowExec(!showExec);
                }}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '3px 10px',
                  borderRadius: 6,
                  cursor: 'pointer',
                  fontSize: 12,
                  color: isStreaming ? token.colorPrimary : token.colorTextTertiary,
                  background: isStreaming ? token.colorPrimaryBg : token.colorFillQuaternary,
                  border: isStreaming
                    ? `1px solid ${token.colorPrimaryBorder}`
                    : `1px solid ${token.colorBorderSecondary}`,
                  fontFamily: 'inherit',
                  fontWeight: isStreaming ? 500 : 400,
                  transition: 'all 0.2s',
                  userSelect: 'none',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = token.colorPrimary;
                  e.currentTarget.style.borderColor = token.colorPrimaryBorder;
                }}
                onMouseLeave={(e) => {
                  if (!isStreaming) {
                    e.currentTarget.style.color = token.colorTextTertiary;
                    e.currentTarget.style.borderColor = token.colorBorderSecondary;
                  }
                }}
              >
                {showExec ? (
                  <CaretDownOutlined style={{ fontSize: 10 }} />
                ) : (
                  <CaretRightOutlined style={{ fontSize: 10 }} />
                )}
                {isStreaming
                  ? `执行中 (${msg.executionSteps!.filter((s) => s.status === 'done').length}/${msg.executionSteps!.length})`
                  : `查看执行过程 · ${msg.executionSteps!.length} 步`}
              </div>
              {showExec && (
                <div style={{ marginTop: 4 }}>
                  <ExecutionTimeline steps={msg.executionSteps!} />
                </div>
              )}
            </div>
          )}

          {/* Message bubble with text content */}
          <div
            className={isStreaming ? 'streaming-content' : ''}
            key={isStreaming ? displayContent.slice(-20) : undefined}
            style={{
              padding: '12px 18px',
              borderRadius: bubbleRadius,
              background: bubbleBg,
              color: isUser ? '#fff' : token.colorText,
              fontSize: 14,
              lineHeight: 1.65,
              overflow: 'hidden',
              boxShadow: isDark ? 'none' : '0 1px 2px rgba(0,0,0,0.04)',
            }}
          >
            {isUser || msg.type === 'interactive_form' ? (
              displayContent
            ) : (
              <MarkdownContent>{displayContent}</MarkdownContent>
            )}
            {isStreaming && <span className="streaming-cursor" />}
          </div>

          {/* User message actions */}
          {isUser && !isStreaming && <UserActions msg={msg} onEdit={onEdit} />}
        </div>

        {isUser && <UserAvatar state={us} />}
      </div>

      {/* Assistant actions — below message */}
      {!isUser && !isStreaming && msg.type !== 'exec' && (
        <div style={{ maxWidth: 900, margin: '0 auto', width: '100%', paddingLeft: 46 }}>
          <AssistActions msg={msg} onRegenerate={onRegenerate} />
        </div>
      )}
    </div>
  );
}
