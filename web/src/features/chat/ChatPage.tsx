import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { Virtuoso, VirtuosoHandle } from 'react-virtuoso';
import { useAuthStore } from '@/stores/authStore';
import { useChatStore, type ExecutionStep, type FormDefinition } from '@/stores/chatStore';
import Bubble from './components/Bubble';
import EmptyState from './components/EmptyState';
import InputBar from './components/InputBar';
import ChatSidebar from './ChatSidebar';

function uuid() {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Detect and parse a form JSON block from message content. */
function parseFormFromContent(content: string): FormDefinition | null {
  const match = content.match(/```json\s*\n([\s\S]*?)\n\s*```/);
  if (!match) return null;
  try {
    const parsed = JSON.parse(match[1]);
    if (
      parsed &&
      parsed.type === 'form' &&
      parsed.form_id &&
      (Array.isArray(parsed.fields) || Array.isArray(parsed.pages))
    ) {
      return parsed as FormDefinition;
    }
  } catch {
    /* not valid JSON */
  }
  return null;
}

export default function ChatPage() {
  const { sessionId, messages, isRunning } = useChatStore();
  const [input, setInput] = useState('');
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const authToken = useAuthStore((s) => s.token);
  const abortRef = useRef<AbortController | null>(null);

  // Track the latest user message index for avatar states
  const lastUserIdx = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') return i;
    }
    return -1;
  }, [messages]);

  // Determine agent avatar state based on current activity
  const agentState = useMemo((): 'idle' | 'thinking' | 'planning' | 'executing' => {
    if (!isRunning) return 'idle';
    const hasRunning = messages.some((m) => m.executionSteps?.some((s) => s.status === 'running'));
    if (hasRunning) return 'executing';
    const hasPlan = messages.some((m) => m.type === 'plan');
    if (hasPlan) return 'planning';
    return 'thinking';
  }, [messages, isRunning]);

  // Determine each user message's avatar state
  const getUserState = useCallback(
    (idx: number): 'sleeping' | 'waiting' | 'reading' => {
      if (idx < lastUserIdx) return 'sleeping';
      if (idx === lastUserIdx && isRunning) return 'waiting';
      if (idx === lastUserIdx && !isRunning && messages.length > 0) return 'reading';
      return 'sleeping';
    },
    [lastUserIdx, isRunning, messages.length],
  );

  const sendMessage = useCallback(
    async (text?: string) => {
      const msgText = (text ?? input).trim();
      if (!msgText || isRunning) return;
      setInput('');
      abortRef.current?.abort();
      abortRef.current = new AbortController();

      const store = useChatStore.getState();
      store.addMessage({
        id: uuid(),
        role: 'user',
        content: msgText,
        type: 'text',
        timestamp: Date.now(),
      });
      store.setIsRunning(true);

      const sid = store.sessionId || uuid();
      if (!store.sessionId) store.setSessionId(sid);

      const msgId = uuid();
      let accumulated = '';
      let msgAdded = false;

      try {
        const resp = await fetch('/api/v1/chat/stream', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${authToken}`,
          },
          body: JSON.stringify({ message: msgText, session_id: sid }),
          signal: abortRef.current.signal,
        });

        if (!resp.ok) {
          const errText = await resp.text().catch(() => '');
          throw new Error(errText || `HTTP ${resp.status}`);
        }

        const reader = resp.body?.getReader();
        if (!reader) throw new Error('No response body');

        const decoder = new TextDecoder();
        let buffer = '';
        let currentEvent = '';
        const stepIndex = new Map<string, number>();

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = '';

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));

                if (currentEvent === 'intent') {
                  store.addMessage({
                    id: uuid(),
                    role: 'system',
                    content: data.intent || '',
                    type: 'intent',
                    timestamp: Date.now(),
                  });
                } else if (currentEvent === 'token' && data.content) {
                  accumulated += data.content;
                  if (!msgAdded) {
                    store.addMessage({
                      id: msgId,
                      role: 'assistant',
                      content: accumulated,
                      type: 'final',
                      timestamp: Date.now(),
                      streaming: true,
                      streamedContent: accumulated,
                    });
                    msgAdded = true;
                  } else {
                    store.updateMessage(msgId, {
                      content: accumulated,
                      streamedContent: accumulated,
                    });
                  }
                } else if (currentEvent === 'tool_start' || currentEvent === 'retrieve_start') {
                  // Pre-create the assistant message so execution steps have a home
                  if (!msgAdded) {
                    store.addMessage({
                      id: msgId,
                      role: 'assistant',
                      content: '',
                      type: 'final',
                      timestamp: Date.now(),
                      streaming: true,
                      streamedContent: '',
                    });
                    msgAdded = true;
                  }
                }

                if (currentEvent === 'retrieve_start') {
                  const stepId = `ret-${data.name || 'retrieve'}-${Date.now()}`;
                  const step: ExecutionStep = {
                    id: stepId,
                    type: 'tool',
                    name: `${data.type === 'knowledge' ? '知识检索' : '记忆检索'}: ${data.name || ''}`,
                    input: data.query || '',
                    output: '',
                    status: 'running',
                    timestamp: Date.now(),
                  };
                  store.addExecutionStep(msgId, step);
                } else if (currentEvent === 'retrieve_end') {
                  const curMsgs = useChatStore.getState().messages;
                  const execSteps = curMsgs.find((m) => m.id === msgId)?.executionSteps || [];
                  const runningStep = execSteps.find(
                    (s) => s.name.includes(data.name || '') && s.status === 'running',
                  );
                  if (runningStep) {
                    store.updateExecutionStep(msgId, runningStep.id, {
                      output: `找到 ${data.result_count || 0} 条相关内容`,
                      status: 'done',
                    });
                  }
                } else if (currentEvent === 'tool_start') {
                  const cnt = (stepIndex.get(data.name) || 0) + 1;
                  stepIndex.set(data.name, cnt);
                  const stepId = `${msgId}-${data.name}-${cnt}`;
                  const rawType = (data.tool_type as string) || 'tool';
                  const stepType: ExecutionStep['type'] =
                    rawType === 'sub_agent'
                      ? 'sub_agent'
                      : rawType === 'skill'
                        ? 'skill'
                        : rawType === 'mcp'
                          ? 'mcp'
                          : rawType === 'builtin'
                            ? 'builtin'
                            : 'tool';
                  const step: ExecutionStep = {
                    id: stepId,
                    type: stepType,
                    name: data.name || '',
                    input: data.input || '',
                    output: '',
                    status: 'running',
                    timestamp: Date.now(),
                    stepNumber: data.step ? Number(data.step) : undefined,
                  };
                  store.addExecutionStep(msgId, step);
                } else if (currentEvent === 'tool_end') {
                  const cnt = stepIndex.get(data.name) || 1;
                  const stepId = `${msgId}-${data.name}-${cnt}`;
                  const isError = data.output && String(data.output).startsWith('Error');
                  store.updateExecutionStep(msgId, stepId, {
                    output: data.output || '',
                    status: isError ? 'error' : 'done',
                  });
                }
              } catch {
                /* skip malformed JSON */
              }
              currentEvent = '';
            } else if (line === '') {
              currentEvent = '';
            } else {
              buffer += line + '\n';
            }
          }
        }

        if (accumulated) {
          const formDef = parseFormFromContent(accumulated);
          store.updateMessage(msgId, {
            content: accumulated,
            streamedContent: accumulated,
            streaming: false,
            ...(formDef ? { type: 'interactive_form' as const, formData: formDef } : {}),
          });
        } else if (!msgAdded) {
          store.addMessage({
            id: msgId,
            role: 'assistant',
            content: 'Agent produced no output.',
            type: 'final',
            timestamp: Date.now(),
          });
        }
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        store.addMessage({
          id: uuid(),
          role: 'system',
          content: `请求失败: ${err instanceof Error ? err.message : '未知错误'}`,
          type: 'text',
          timestamp: Date.now(),
        });
      } finally {
        store.setIsRunning(false);
        store.refreshSessions();
      }
    },
    [input, sessionId, isRunning, authToken],
  );

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const handleEdit = useCallback((oldContent: string) => {
    setInput(oldContent);
    // Focus handled by InputBar ref
  }, []);

  const handleRegenerate = useCallback(() => {
    const store = useChatStore.getState();
    const lastUser = [...store.messages].reverse().find((m) => m.role === 'user');
    if (lastUser) {
      sendMessage(lastUser.content);
    }
  }, [sendMessage]);

  const handleFormSubmit = useCallback(
    (formId: string, values: Record<string, unknown>) => {
      // Mark the form message as submitted
      const store = useChatStore.getState();
      const msgs = store.messages;
      for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].type === 'interactive_form' && msgs[i].formData?.form_id === formId) {
          useChatStore.setState({
            messages: msgs.map((m, idx) => (idx === i ? { ...m, formSubmitted: true } : m)),
          });
          break;
        }
      }
      const payload = `[FORM_SUBMISSION: ${formId}]\n${JSON.stringify(values, null, 2)}`;
      sendMessage(payload);
    },
    [sendMessage],
  );

  const atBottomRef = useRef(true);

  useEffect(() => {
    if (messages.length > 0 && atBottomRef.current) {
      virtuosoRef.current?.scrollToIndex({ index: messages.length - 1, behavior: 'smooth' });
    }
  }, [messages]);

  return (
    <div style={{ height: '100%', display: 'flex', overflow: 'hidden' }}>
      <ChatSidebar />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
          {messages.length === 0 ? (
            <EmptyState onSuggestionClick={(label) => setInput(label)} />
          ) : (
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
                />
              )}
              style={{ height: '100%', paddingBottom: 8 }}
            />
          )}
        </div>
        <InputBar
          input={input}
          setInput={setInput}
          loading={isRunning}
          onSend={() => sendMessage()}
          onStop={handleStop}
        />
      </div>
    </div>
  );
}
