import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Virtuoso, VirtuosoHandle } from 'react-virtuoso';
import { AnimatePresence, motion } from 'framer-motion';
import { useSpaceStore } from '@/stores/spaceStore';
import { useChatStore, type ChatMessage } from '@/stores/chatStore';
import { useChatStream } from './hooks/useChatStream';
import Bubble from './components/Bubble';
import EmptyState from './components/EmptyState';
import InputBar from './components/InputBar';
import ChatSidebar from './ChatSidebar';
import ModelSwitcher from './components/ModelSwitcher';
import ContextPanel from './components/ContextPanel';
import TaskPanel from './components/TaskPanel';
import { Button, Spin } from 'antd';
import api from '@/services/api';
import { A2UIProvider } from './a2ui';
import type { A2UIClientEvent } from './a2ui/types';
import { FolderOpenOutlined, UnorderedListOutlined } from '@ant-design/icons';

export default function ChatPage() {
  const { sessionId, messages, setSessionId, setMessages, setLoadingHistory, loadingHistory } =
    useChatStore();
  const currentSpace = useSpaceStore((s) => s.currentSpace);
  const [searchParams] = useSearchParams();

  const [modelProviderId, setModelProviderId] = useState<string | null>(null);
  const [contextOpen, setContextOpen] = useState(false);
  const [taskPanelOpen, setTaskPanelOpen] = useState(false);

  // Support /ops/chat?session=<id> to auto-select a session
  useEffect(() => {
    const targetSid = searchParams.get('session');
    if (targetSid && targetSid !== sessionId) {
      setSessionId(targetSid);
      setLoadingHistory(true);
      api
        .get(`/sessions/${targetSid}`)
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
              }) => ({
                id: m.id,
                role: m.role as ChatMessage['role'],
                content: m.content,
                type: (m.type as ChatMessage['type']) || 'text',
                timestamp: new Date(m.created_at).getTime(),
              }),
            );
          setMessages(msgs);
          if (res.data?.model_provider_id) {
            setModelProviderId(res.data.model_provider_id);
          }
        })
        .catch(() => {})
        .finally(() => setLoadingHistory(false));
    }
  }, [searchParams]);

  // Restore persisted session on mount
  useEffect(() => {
    const stored = localStorage.getItem('aiops_persisted_session_id');
    if (stored && !sessionId) {
      setLoadingHistory(true);
      api
        .get(`/sessions/${stored}`)
        .then((res) => {
          setSessionId(stored);
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
              }) => ({
                id: m.id,
                role: m.role as ChatMessage['role'],
                content: m.content,
                type: (m.type as ChatMessage['type']) || 'text',
                timestamp: new Date(m.created_at).getTime(),
              }),
            );
          setMessages(msgs);
        })
        .catch(() => {
          localStorage.removeItem('aiops_persisted_session_id');
        })
        .finally(() => setLoadingHistory(false));
    }
  }, []); // run once on mount

  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const atBottomRef = useRef(true);

  const {
    input,
    setInput,
    attachedFiles,
    setAttachedFiles,
    isRunning,
    sendMessage,
    stop,
    setModelProvider,
    handleEdit,
    handleRegenerate,
    handleInterruptRespond,
    handleFormSubmit,
    lastUserIdx,
    agentState,
    getUserState,
  } = useChatStream({ spaceId: currentSpace?.id });

  // Sync model provider ref in hook with local state
  useEffect(() => {
    setModelProvider(modelProviderId);
  }, [modelProviderId, setModelProvider]);

  // Auto-scroll to last message when entering a conversation
  useEffect(() => {
    if (messages.length > 0) {
      atBottomRef.current = true;
      const timer = setTimeout(() => {
        virtuosoRef.current?.scrollToIndex({ index: messages.length - 1, behavior: 'auto' });
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [sessionId]);

  useEffect(() => {
    if (messages.length > 0 && atBottomRef.current) {
      virtuosoRef.current?.scrollToIndex({ index: messages.length - 1, behavior: 'smooth' });
    }
  }, [messages]);

  const handleA2UIAction = (event: A2UIClientEvent) => {
    const payload = {
      type: 'a2ui_action',
      surfaceId: event.surfaceId,
      actionName: event.name,
      context: event.context,
    };
    sendMessage(`[A2UI_ACTION]\n${JSON.stringify(payload)}`);
  };

  return (
    <A2UIProvider onAction={handleA2UIAction}>
      <div style={{ height: '100%', display: 'flex', overflow: 'hidden' }}>
        <ChatSidebar />
        <ContextPanel
          open={contextOpen}
          onClose={() => setContextOpen(false)}
          sessionId={sessionId}
        />
        <TaskPanel
          open={taskPanelOpen}
          onClose={() => setTaskPanelOpen(false)}
          sessionId={sessionId}
        />
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
            position: 'relative',
          }}
        >
          {/* Chat header */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '8px 16px',
              borderBottom: messages.length > 0 ? '1px solid var(--color-border, #f0f0f0)' : 'none',
              flexShrink: 0,
            }}
          >
            <Button
              type="text"
              size="small"
              icon={<FolderOpenOutlined />}
              onClick={() => setContextOpen(true)}
            >
              上下文
            </Button>
            <Button
              type="text"
              size="small"
              icon={<UnorderedListOutlined />}
              onClick={() => setTaskPanelOpen(true)}
            >
              任务
            </Button>
            <ModelSwitcher value={modelProviderId} onChange={setModelProviderId} />
          </div>

          <AnimatePresence mode="wait">
            {loadingHistory ? (
              <div
                style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
              >
                <Spin size="large" />
              </div>
            ) : messages.length === 0 ? (
              <motion.div
                key="empty"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0, transition: { duration: 0.15 } }}
                style={{
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  paddingBottom: '8vh',
                  minWidth: 0,
                }}
              >
                <EmptyState onSuggestionClick={(label) => setInput(label)} />
                <div style={{ maxWidth: 900, width: '100%', padding: '0 16px', marginTop: 24 }}>
                  <InputBar
                    input={input}
                    setInput={setInput}
                    loading={isRunning}
                    onSend={() => sendMessage()}
                    onStop={stop}
                    onOpenContext={() => setContextOpen(true)}
                    attachedFiles={attachedFiles}
                    onAttachFile={(f) => setAttachedFiles((prev) => [...prev, f])}
                    onRemoveFile={(id) =>
                      setAttachedFiles((prev) => prev.filter((f) => f.id !== id))
                    }
                  />
                </div>
              </motion.div>
            ) : (
              <motion.div
                key="chat"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1, transition: { duration: 0.25, ease: 'easeOut' } }}
                style={{
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  minWidth: 0,
                  overflow: 'hidden',
                }}
              >
                <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
                  <Virtuoso
                    ref={virtuosoRef}
                    totalCount={messages.length}
                    atBottomStateChange={(atBottom) => {
                      atBottomRef.current = atBottom;
                    }}
                    itemContent={(index) => (
                      <Bubble
                        msg={messages[index]}
                        isLatestUser={index === lastUserIdx}
                        agentState={agentState}
                        userState={getUserState(index)}
                        onEdit={() => handleEdit(messages[index].content)}
                        onRegenerate={handleRegenerate}
                        onFormSubmit={handleFormSubmit}
                        onInterruptRespond={handleInterruptRespond}
                      />
                    )}
                    style={{ height: '100%', paddingBottom: 8 }}
                  />
                </div>
                <InputBar
                  input={input}
                  setInput={setInput}
                  loading={isRunning}
                  onSend={() => sendMessage()}
                  onStop={stop}
                  onOpenContext={() => setContextOpen(true)}
                  attachedFiles={attachedFiles}
                  onAttachFile={(f) => setAttachedFiles((prev) => [...prev, f])}
                  onRemoveFile={(id) => setAttachedFiles((prev) => prev.filter((f) => f.id !== id))}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </A2UIProvider>
  );
}
