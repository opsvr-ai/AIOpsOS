import { useState, useRef, useCallback, useMemo } from 'react';
import { useChatStore, type ExecutionStep, type FormDefinition } from '@/stores/chatStore';
import { getSharedProcessor } from '../a2ui/sharedProcessor';
import { useAuthStore } from '@/stores/authStore';

function uuid() {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

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

/** SSE event handling logic extracted from ChatPage — keeps the page component lean. */
async function streamChat(opts: {
  msgText: string;
  spaceId?: string;
  modelProviderId?: string | null;
  signal: AbortSignal;
}) {
  const { msgText, spaceId, modelProviderId, signal } = opts;
  const store = useChatStore.getState();

  // Always read the freshest token from the auth store (axios may have refreshed it)
  const authToken = useAuthStore.getState().token || '';

  const sid = store.sessionId || uuid();
  if (!store.sessionId) store.setSessionId(sid);

  const resp = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${authToken}`,
    },
    body: JSON.stringify({
      message: msgText,
      session_id: sid,
      space_id: spaceId || undefined,
      model_provider_id: modelProviderId || undefined,
    }),
    signal,
  });

  if (!resp.ok) {
    const errText = await resp.text().catch(() => '');
    throw new Error(errText || `HTTP ${resp.status}`);
  }

  const reader = resp.body?.getReader();
  if (!reader) throw new Error('No response body');

  const msgId = uuid();
  let accumulated = '';
  let msgAdded = false;

  const decoder = new TextDecoder();
  let buffer = '';
  let currentEvent = '';
  const stepIndex = new Map<string, number>();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6));

          if (currentEvent === 'report_progress') {
            if (data.status === 'completed' && data.url) {
              store.addMessage({
                id: uuid(),
                role: 'assistant',
                type: 'report',
                content: `Report ready: ${data.title || 'Untitled'}`,
                timestamp: Date.now(),
                reportUrl: data.url,
                reportTitle: data.title,
              });
            }
          } else if (currentEvent === 'a2ui_batch') {
            const proc = getSharedProcessor();
            proc.processMessages(data.messages);

            const createMsg = data.messages.find((m: any) => m.createSurface);
            const surfaceId = createMsg?.createSurface?.surfaceId || `msg-${msgId}`;

            store.updateMessage(msgId, {
              type: 'a2ui' as any,
              a2uiSurfaceId: surfaceId,
              a2uiReady: true,
            });
          } else if (currentEvent === 'interrupt') {
            useChatStore.getState().addMessage({
              id: uuid(),
              role: 'assistant',
              content: data.data?.action || data.data?.title || '请求人工介入',
              type: 'interrupt',
              timestamp: Date.now(),
              interruptData: {
                interrupt_id: data.interrupt_id,
                type: data.type,
                data: data.data,
              },
            });
          } else if (currentEvent === 'intent') {
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
            store.addExecutionStep(msgId, {
              id: stepId,
              type: 'tool',
              name: `${data.type === 'knowledge' ? '知识检索' : '记忆检索'}: ${data.name || ''}`,
              input: data.query || '',
              output: '',
              status: 'running',
              timestamp: Date.now(),
            });
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
            store.addExecutionStep(msgId, {
              id: `${msgId}-${data.name}-${cnt}`,
              type: stepType,
              name: data.name || '',
              input: data.input || '',
              output: '',
              status: 'running',
              timestamp: Date.now(),
              stepNumber: data.step ? Number(data.step) : undefined,
            });
          } else if (currentEvent === 'tool_end') {
            const cnt = stepIndex.get(data.name) || 1;
            const isError = data.output && String(data.output).startsWith('Error');
            store.updateExecutionStep(msgId, `${msgId}-${data.name}-${cnt}`, {
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
}

export interface AttachedFile {
  id: string;
  filename: string;
}

export function useChatStream(opts: { spaceId?: string }) {
  const { spaceId } = opts;
  const modelProviderRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [input, setInput] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const isRunning = useChatStore((s) => s.isRunning);
  const messages = useChatStore((s) => s.messages);

  const lastUserIdx = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') return i;
    }
    return -1;
  }, [messages]);

  const agentState = useMemo((): 'idle' | 'thinking' | 'planning' | 'executing' => {
    if (!isRunning) return 'idle';
    const hasRunning = messages.some((m) => m.executionSteps?.some((s) => s.status === 'running'));
    if (hasRunning) return 'executing';
    const hasPlan = messages.some((m) => m.type === 'plan');
    if (hasPlan) return 'planning';
    return 'thinking';
  }, [messages, isRunning]);

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

      // Build file refs from attached files
      const fileRefs = attachedFiles.map((f) => `@[${f.filename}](ref:${f.id})`).join('\n');
      const fullText = fileRefs ? `${fileRefs}\n${msgText}` : msgText;

      setAttachedFiles([]);
      abortRef.current?.abort();
      abortRef.current = new AbortController();

      const store = useChatStore.getState();
      store.addMessage({
        id: uuid(),
        role: 'user',
        content: fullText,
        type: 'text',
        timestamp: Date.now(),
      });
      store.setIsRunning(true);

      try {
        await streamChat({
          msgText: fullText,
          spaceId,
          modelProviderId: modelProviderRef.current,
          signal: abortRef.current.signal,
        });
        setInput('');
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          setInput('');
          return;
        }
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
    [input, isRunning, spaceId, attachedFiles],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const setModelProvider = useCallback((id: string | null) => {
    modelProviderRef.current = id;
  }, []);

  const handleEdit = useCallback((oldContent: string) => {
    setInput(oldContent);
  }, []);

  const handleRegenerate = useCallback(() => {
    const store = useChatStore.getState();
    const lastUser = [...store.messages].reverse().find((m) => m.role === 'user');
    if (lastUser) sendMessage(lastUser.content);
  }, [sendMessage]);

  const handleInterruptRespond = useCallback(
    (interruptId: string, approved: boolean) => {
      const store = useChatStore.getState();
      const msgs = store.messages;
      for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].type === 'interrupt' && msgs[i].interruptData?.interrupt_id === interruptId) {
          useChatStore.setState({
            messages: msgs.map((m, idx) => (idx === i ? { ...m, interruptResolved: true } : m)),
          });
          break;
        }
      }
      sendMessage(approved ? 'yes' : 'no');
    },
    [sendMessage],
  );

  const handleFormSubmit = useCallback(
    (formId: string, values: Record<string, unknown>) => {
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
      sendMessage(`[FORM_SUBMISSION: ${formId}]\n${JSON.stringify(values, null, 2)}`);
    },
    [sendMessage],
  );

  return {
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
  };
}
