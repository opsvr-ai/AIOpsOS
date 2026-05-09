import { useRef, useEffect, useCallback, useState } from 'react';
import { Input, Button, Tag, theme, Popover } from 'antd';
import {
  SendOutlined,
  StopOutlined,
  LoadingOutlined,
  PaperClipOutlined,
  ThunderboltOutlined,
  SearchOutlined,
  BugOutlined,
  SettingOutlined,
  FileTextOutlined,
  CloseOutlined,
} from '@ant-design/icons';
import { useThemeStore } from '@/stores/themeStore';
import { useChatStore } from '@/stores/chatStore';
import api from '@/services/api';
import type { AttachedFile } from '../hooks/useChatStream';

const { TextArea } = Input;

interface SlashCommand {
  key: string;
  label: string;
  description: string;
  icon: React.ReactNode;
}

const SLASH_COMMANDS: SlashCommand[] = [
  { key: '/analyze', label: '分析告警', description: '分析最近的告警事件', icon: <BugOutlined /> },
  {
    key: '/search',
    label: '检索知识',
    description: '搜索知识库中的相关内容',
    icon: <SearchOutlined />,
  },
  {
    key: '/check',
    label: '系统检查',
    description: '检查系统运行状态',
    icon: <ThunderboltOutlined />,
  },
  {
    key: '/config',
    label: '系统配置',
    description: '查看或修改系统配置',
    icon: <SettingOutlined />,
  },
  { key: '/docs', label: '查看文档', description: '查看运维相关文档', icon: <FileTextOutlined /> },
];

const SUGGESTED_PILLS = [
  { label: '系统状态', prompt: '查看系统当前运行状态' },
  { label: '最近告警', prompt: '查看最近告警列表' },
  { label: '知识搜索', prompt: '搜索运维相关知识' },
  { label: '故障排查', prompt: '帮我排查一下' },
];

export default function InputBar({
  input,
  setInput,
  loading,
  onSend,
  onStop,
  onOpenContext,
  attachedFiles = [],
  onAttachFile,
  onRemoveFile,
}: {
  input: string;
  setInput: (v: string) => void;
  loading: boolean;
  onSend: () => void;
  onStop: () => void;
  onOpenContext?: () => void;
  attachedFiles?: AttachedFile[];
  onAttachFile?: (file: AttachedFile) => void;
  onRemoveFile?: (id: string) => void;
}) {
  const { token } = theme.useToken();
  const mode = useThemeStore((s) => s.mode);
  const isDark = mode === 'dark';
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mentionDropdownRef = useRef<HTMLDivElement>(null);
  const [slashOpen, setSlashOpen] = useState(false);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionFiles, setMentionFiles] = useState<Array<{ id: string; filename: string }>>([]);
  const [mentionQuery, setMentionQuery] = useState('');

  const canSend = input.trim().length > 0 && !loading;

  const fileRefreshTick = useChatStore((s) => s._fileRefreshTick);

  // Fetch context files when @ is typed
  const fetchMentionFiles = useCallback(async () => {
    const sid = useChatStore.getState().sessionId;
    if (!sid) return;
    try {
      const res = await api.get(`/sessions/${sid}/files?scope=space`);
      const files = (res.data || []).map((f: { id: string; filename: string }) => ({
        id: f.id,
        filename: f.filename,
      }));
      setMentionFiles(files);
    } catch {
      setMentionFiles([]);
    }
  }, []);

  // Refetch @mention file list when files are mutated in ContextPanel
  useEffect(() => {
    if (fileRefreshTick > 0) fetchMentionFiles();
  }, [fileRefreshTick, fetchMentionFiles]);

  // Detect @ trigger in input
  const checkMentionTrigger = useCallback(
    (val: string, cursorPos?: number) => {
      // Find the last @ before cursor that's not preceded by a word char
      const pos = cursorPos ?? val.length;
      const before = val.slice(0, pos);
      const atMatch = before.match(/@([^\s@]*)$/);
      if (atMatch) {
        setMentionQuery(atMatch[1]);
        setMentionOpen(true);
        fetchMentionFiles();
      } else {
        setMentionOpen(false);
        setMentionQuery('');
      }
    },
    [fetchMentionFiles],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey && !mentionOpen) {
        e.preventDefault();
        if (canSend) onSend();
      }
      if (e.key === '/' && !input) {
        setSlashOpen(true);
      }
      if (e.key === 'Escape') {
        setSlashOpen(false);
        setMentionOpen(false);
      }
    },
    [canSend, onSend, input, mentionOpen],
  );

  const handleMentionSelect = (file: { id: string; filename: string }) => {
    // Remove the @query text from the input and add file as a locked chip
    const lastAt = input.lastIndexOf('@', textareaRef.current?.selectionStart ?? input.length);
    if (lastAt >= 0) {
      const before = input.slice(0, lastAt);
      const after = input.slice(textareaRef.current?.selectionStart ?? input.length);
      setInput((before + after).trimEnd() + '\n');
    }
    onAttachFile?.(file);
    setMentionOpen(false);
    setMentionQuery('');
    setTimeout(() => textareaRef.current?.focus(), 50);
  };

  const handleSlashSelect = (cmd: SlashCommand) => {
    setInput(cmd.key + ' ');
    setSlashOpen(false);
    setTimeout(() => textareaRef.current?.focus(), 50);
  };

  const handlePillClick = (prompt: string) => {
    setInput(prompt);
    setTimeout(() => textareaRef.current?.focus(), 50);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInput(val);
    if (val === '/') {
      setSlashOpen(true);
    } else if (slashOpen && !val.startsWith('/')) {
      setSlashOpen(false);
    }
    checkMentionTrigger(val, e.target.selectionStart ?? undefined);
  };

  useEffect(() => {
    if (input && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, []);

  // Close mention dropdown on outside click
  useEffect(() => {
    if (!mentionOpen) return;
    const handler = (e: MouseEvent) => {
      if (mentionDropdownRef.current && !mentionDropdownRef.current.contains(e.target as Node)) {
        setMentionOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [mentionOpen]);

  return (
    <div
      style={{
        padding: '0 16px 20px',
        flexShrink: 0,
        maxWidth: 900,
        margin: '0 auto',
        width: '100%',
      }}
    >
      {/* Slash command popover */}
      <Popover
        open={slashOpen && input === '/'}
        onOpenChange={(v) => {
          if (!v) setSlashOpen(false);
        }}
        placement="topLeft"
        trigger="click"
        content={
          <div style={{ width: 260 }}>
            <div
              style={{
                fontSize: 11,
                color: token.colorTextTertiary,
                marginBottom: 6,
                fontWeight: 500,
              }}
            >
              快捷指令
            </div>
            {SLASH_COMMANDS.map((cmd) => (
              <div
                key={cmd.key}
                onClick={() => handleSlashSelect(cmd)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '7px 8px',
                  borderRadius: 6,
                  cursor: 'pointer',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = token.colorFillSecondary;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent';
                }}
              >
                <span style={{ color: token.colorPrimary, fontSize: 14 }}>{cmd.icon}</span>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{cmd.key}</div>
                  <div style={{ fontSize: 11, color: token.colorTextTertiary }}>
                    {cmd.description}
                  </div>
                </div>
              </div>
            ))}
          </div>
        }
      >
        <div />
      </Popover>

      {loading && (
        <div
          style={{
            textAlign: 'center',
            marginBottom: 10,
            fontSize: 12,
            color: token.colorPrimary,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            fontWeight: 500,
            padding: '4px 0',
          }}
        >
          <LoadingOutlined spin style={{ fontSize: 13 }} />
          AI 正在处理中
        </div>
      )}

      {/* Input card */}
      <div
        style={{
          position: 'relative',
          background: token.colorBgContainer,
          border: `1px solid ${canSend ? token.colorPrimary : token.colorBorderSecondary}`,
          borderRadius: 18,
          padding: '10px 14px 10px 20px',
          display: 'flex',
          flexDirection: 'column',
          transition: 'border-color 0.2s, box-shadow 0.2s',
          boxShadow: isDark
            ? '0 4px 24px rgba(0,0,0,0.35)'
            : '0 2px 12px rgba(0,0,0,0.06), 0 1px 3px rgba(0,0,0,0.04)',
        }}
      >
        {/* @mention dropdown — positioned above the input card */}
        {mentionOpen && (
          <div
            ref={mentionDropdownRef}
            style={{
              position: 'absolute',
              bottom: '100%',
              left: 0,
              right: 0,
              marginBottom: 8,
              background: token.colorBgElevated,
              borderRadius: 10,
              boxShadow: token.boxShadowSecondary,
              border: `1px solid ${token.colorBorderSecondary}`,
              maxHeight: 320,
              overflowY: 'auto',
              padding: 8,
              zIndex: 1050,
            }}
          >
            <div
              style={{
                fontSize: 11,
                color: token.colorTextTertiary,
                marginBottom: 6,
                fontWeight: 500,
              }}
            >
              引用上下文文件
            </div>
            {mentionFiles.length === 0 ? (
              <div
                style={{
                  fontSize: 12,
                  color: token.colorTextTertiary,
                  padding: '12px 0',
                  textAlign: 'center',
                }}
              >
                暂无上下文文件
              </div>
            ) : (
              mentionFiles
                .filter(
                  (f) =>
                    !mentionQuery || f.filename.toLowerCase().includes(mentionQuery.toLowerCase()),
                )
                .slice(0, 8)
                .map((file) => (
                  <div
                    key={file.id}
                    onClick={() => handleMentionSelect(file)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      padding: '7px 8px',
                      borderRadius: 6,
                      cursor: 'pointer',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = token.colorFillSecondary;
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'transparent';
                    }}
                  >
                    <FileTextOutlined style={{ color: token.colorPrimary, fontSize: 14 }} />
                    <div>
                      <div style={{ fontSize: 13 }}>{file.filename}</div>
                      <div style={{ fontSize: 11, color: token.colorTextTertiary }}>
                        @{file.filename}
                      </div>
                    </div>
                  </div>
                ))
            )}
          </div>
        )}

        {!loading && !input && (
          <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
            {SUGGESTED_PILLS.map((pill) => (
              <Button
                key={pill.label}
                size="small"
                type="default"
                onClick={() => handlePillClick(pill.prompt)}
                style={{
                  borderRadius: 16,
                  fontSize: 12,
                  color: token.colorTextSecondary,
                  borderColor: token.colorBorderSecondary,
                  background: token.colorFillQuaternary,
                  padding: '0 12px',
                  height: 28,
                }}
              >
                {pill.label}
              </Button>
            ))}
          </div>
        )}

        {/* Attached file chips — locked, non-editable */}
        {attachedFiles.length > 0 && (
          <div
            style={{
              display: 'flex',
              gap: 6,
              marginBottom: 8,
              flexWrap: 'wrap',
              alignItems: 'center',
            }}
          >
            {attachedFiles.map((f) => (
              <Tag
                key={f.id}
                closable
                onClose={(e) => {
                  e.preventDefault();
                  onRemoveFile?.(f.id);
                }}
                closeIcon={<CloseOutlined style={{ fontSize: 10 }} />}
                icon={<FileTextOutlined style={{ marginRight: 2 }} />}
                color="blue"
                style={{
                  margin: 0,
                  padding: '2px 8px',
                  fontSize: 12,
                  borderRadius: 6,
                  cursor: 'default',
                  userSelect: 'none',
                }}
              >
                {f.filename}
              </Tag>
            ))}
            <span
              style={{
                fontSize: 11,
                color: token.colorTextTertiary,
                marginLeft: 4,
              }}
            >
              已引用，请输入需求
            </span>
          </div>
        )}

        <TextArea
          ref={textareaRef}
          placeholder={loading ? 'AI 正在回复中...' : '输入消息，Enter 发送，Shift+Enter 换行'}
          value={input}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          disabled={loading}
          variant="borderless"
          autoSize={{ minRows: 1, maxRows: 8 }}
          style={{ flex: 1, fontSize: 14, padding: '2px 0', resize: 'none', lineHeight: 1.55 }}
        />

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginTop: 8,
            paddingTop: 8,
            borderTop: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Button
              type="text"
              size="small"
              onClick={() => {
                setInput('/');
                setTimeout(() => textareaRef.current?.focus(), 50);
              }}
              style={{
                fontSize: 11,
                color: token.colorTextTertiary,
                padding: '0 8px',
                height: 28,
                borderRadius: 6,
              }}
            >
              / 指令
            </Button>
            <Button
              type="text"
              size="small"
              onClick={() => {
                setInput(input + '@');
                setTimeout(() => textareaRef.current?.focus(), 50);
              }}
              style={{
                fontSize: 11,
                color: token.colorTextTertiary,
                padding: '0 8px',
                height: 28,
                borderRadius: 6,
              }}
            >
              @ 引用
            </Button>
            <Button
              type="text"
              size="small"
              icon={<PaperClipOutlined style={{ fontSize: 12 }} />}
              disabled={loading}
              onClick={onOpenContext}
              style={{
                fontSize: 11,
                color: token.colorTextTertiary,
                padding: '0 8px',
                height: 28,
                borderRadius: 6,
              }}
            >
              附件
            </Button>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 10, color: token.colorTextQuaternary }}>
              Enter 发送 · Shift+Enter 换行
              {input.length > 0 && (
                <span
                  style={{
                    marginLeft: 6,
                    color: input.length > 3800 ? token.colorError : token.colorTextQuaternary,
                  }}
                >
                  {input.length}/4000
                </span>
              )}
            </span>
            {loading ? (
              <Button
                type="default"
                icon={<StopOutlined />}
                onClick={onStop}
                shape="circle"
                danger
                size="small"
                style={{ width: 36, height: 36, flexShrink: 0 }}
              />
            ) : (
              <Button
                type={canSend ? 'primary' : 'default'}
                icon={<SendOutlined />}
                onClick={onSend}
                disabled={!canSend}
                shape="circle"
                size="small"
                style={{
                  width: 36,
                  height: 36,
                  flexShrink: 0,
                  ...(canSend && { boxShadow: `0 2px 8px ${token.colorPrimary}40` }),
                }}
              />
            )}
          </div>
        </div>
      </div>

      <div
        style={{
          textAlign: 'center',
          marginTop: 8,
          fontSize: 11,
          color: token.colorTextQuaternary,
        }}
      >
        AI 生成内容仅供参考，请验证关键信息
      </div>
    </div>
  );
}
