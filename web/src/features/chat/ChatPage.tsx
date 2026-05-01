import { useEffect, useRef, useState } from 'react';
import { Virtuoso, VirtuosoHandle } from 'react-virtuoso';
import { AnimatePresence, motion } from 'framer-motion';
import { useAuthStore } from '@/stores/authStore';
import { useSpaceStore } from '@/stores/spaceStore';
import { useChatStore } from '@/stores/chatStore';
import { useChatStream } from './hooks/useChatStream';
import Bubble from './components/Bubble';
import EmptyState from './components/EmptyState';
import InputBar from './components/InputBar';
import ChatSidebar from './ChatSidebar';
import ModelSwitcher from './components/ModelSwitcher';
import ContextPanel from './components/ContextPanel';
import TaskPanel from './components/TaskPanel';
import { Button } from 'antd';
import { FolderOpenOutlined, UnorderedListOutlined } from '@ant-design/icons';

export default function ChatPage() {
  const { sessionId, messages } = useChatStore();
  const authToken = useAuthStore((s) => s.token);
  const currentSpace = useSpaceStore((s) => s.currentSpace);

  const [modelProviderId, setModelProviderId] = useState<string | null>(null);
  const [contextOpen, setContextOpen] = useState(false);
  const [taskPanelOpen, setTaskPanelOpen] = useState(false);
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const atBottomRef = useRef(true);

  const {
    input,
    setInput,
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
  } = useChatStream({ authToken: authToken || '', spaceId: currentSpace?.id });

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

  return (
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
          {messages.length === 0 ? (
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
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
