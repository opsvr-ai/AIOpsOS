import { useState, useCallback } from 'react';
import { Button, theme, App } from 'antd';
import {
  CopyOutlined,
  EditOutlined,
  CheckOutlined,
  LikeOutlined,
  DislikeOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { ChatMessage } from '@/stores/chatStore';

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
        onClick={() => setLiked((p) => (p === 'up' ? null : 'up'))}
        style={{ color: token.colorTextTertiary, fontSize: 12 }}
      />
      <Button
        type="text"
        size="small"
        icon={
          <DislikeOutlined style={{ color: liked === 'down' ? token.colorError : undefined }} />
        }
        onClick={() => setLiked((p) => (p === 'down' ? null : 'down'))}
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

export default function MessageActions({
  msg,
  onEdit,
  onRegenerate,
  isUser,
}: {
  msg: ChatMessage;
  onEdit: () => void;
  onRegenerate: () => void;
  isUser: boolean;
}) {
  if (isUser) return <UserActions msg={msg} onEdit={onEdit} />;
  return <AssistActions msg={msg} onRegenerate={onRegenerate} />;
}
