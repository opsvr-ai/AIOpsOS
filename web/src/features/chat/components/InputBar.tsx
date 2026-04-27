import { useRef, useEffect, useCallback } from 'react';
import { Input, Button, theme } from 'antd';
import { SendOutlined, StopOutlined, LoadingOutlined } from '@ant-design/icons';
import { useThemeStore } from '@/stores/themeStore';

const { TextArea } = Input;

export default function InputBar({
  input,
  setInput,
  loading,
  onSend,
  onStop,
}: {
  input: string;
  setInput: (v: string) => void;
  loading: boolean;
  onSend: () => void;
  onStop: () => void;
}) {
  const { token } = theme.useToken();
  const mode = useThemeStore((s) => s.mode);
  const isDark = mode === 'dark';
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const canSend = input.trim().length > 0 && !loading;

  // Shift+Enter for newline, Enter to send
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (canSend) onSend();
      }
    },
    [canSend, onSend],
  );

  // Auto-focus after edit
  useEffect(() => {
    if (input && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, []);

  return (
    <div
      style={{
        padding: '0 16px 16px',
        flexShrink: 0,
        maxWidth: 900,
        margin: '0 auto',
        width: '100%',
      }}
    >
      {/* Status indicator */}
      {loading && (
        <div
          style={{
            textAlign: 'center',
            marginBottom: 8,
            fontSize: 12,
            color: token.colorPrimary,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
            fontWeight: 500,
          }}
        >
          <LoadingOutlined spin style={{ fontSize: 12 }} />
          AI 正在处理...
        </div>
      )}

      <div
        style={{
          background: token.colorBgContainer,
          border: `1px solid ${canSend ? token.colorPrimary : token.colorBorder}`,
          borderRadius: 16,
          padding: '8px 8px 8px 16px',
          display: 'flex',
          alignItems: 'flex-end',
          gap: 8,
          transition: 'border-color 0.2s, box-shadow 0.2s',
          boxShadow: isDark ? '0 4px 20px rgba(0,0,0,0.3)' : '0 4px 20px rgba(0,0,0,0.06)',
        }}
      >
        <TextArea
          ref={textareaRef}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
          variant="borderless"
          autoSize={{ minRows: 1, maxRows: 9 }}
          style={{
            flex: 1,
            fontSize: 14,
            padding: '6px 0',
            resize: 'none',
            lineHeight: 1.5,
          }}
        />
        {loading ? (
          <Button
            type="default"
            icon={<StopOutlined />}
            onClick={onStop}
            shape="circle"
            danger
            className="send-btn-running"
            style={{
              width: 38,
              height: 38,
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              alignSelf: 'flex-end',
            }}
          />
        ) : (
          <Button
            type={canSend ? 'primary' : 'default'}
            icon={<SendOutlined />}
            onClick={onSend}
            disabled={!canSend}
            shape="circle"
            style={{
              width: 38,
              height: 38,
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              alignSelf: 'flex-end',
            }}
          />
        )}
      </div>
      <div
        style={{
          textAlign: 'center',
          marginTop: 6,
          fontSize: 11,
          color: token.colorTextTertiary,
        }}
      >
        AI 生成内容仅供参考，请验证关键信息
      </div>
    </div>
  );
}
