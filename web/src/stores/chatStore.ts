import { create } from 'zustand';

interface PlanStep {
  step: number;
  tool: string;
  args: Record<string, unknown>;
}

interface ToolResult {
  step: number;
  tool: string;
  output: string;
}

export interface ExecutionStep {
  id: string;
  type: 'tool' | 'sub_agent' | 'skill' | 'mcp' | 'builtin';
  name: string;
  input: string;
  output: string;
  status: 'running' | 'done' | 'error';
  timestamp: number;
  stepNumber?: number;
}

export interface FormField {
  key: string;
  label: string;
  type: 'text' | 'textarea' | 'radio' | 'checkbox';
  placeholder?: string;
  required?: boolean;
  options?: { value: string; label: string }[];
  value?: string | string[];
  show_when?: { key: string; equals: string };
}

export interface FormPage {
  title: string;
  description?: string;
  fields: FormField[];
}

export interface FormDefinition {
  type: 'form';
  form_id: string;
  title: string;
  description?: string;
  pages?: FormPage[];
  fields?: FormField[];
  step?: number;
  total_steps?: number;
  submit_label?: string;
}

export interface InterruptData {
  interrupt_id: string;
  type: 'approval' | 'form';
  data: {
    action?: string;
    details?: string;
    risk_level?: string;
    code_snippet?: string;
    impact_scope?: string;
    title?: string;
    description?: string;
    fields?: FormField[];
  };
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  type:
    | 'text'
    | 'intent'
    | 'plan'
    | 'exec'
    | 'final'
    | 'interactive_form'
    | 'interrupt'
    | 'a2ui'
    | 'report';
  timestamp: number;
  planSteps?: PlanStep[];
  execResults?: ToolResult[];
  executionSteps?: ExecutionStep[];
  /** Text progressively revealed for streaming effect */
  streamedContent?: string;
  streaming?: boolean;
  /** Interactive form data for form-type messages */
  formData?: FormDefinition;
  /** Whether the form has been submitted (read-only mode) */
  formSubmitted?: boolean;
  /** Interrupt data for human-in-the-loop approval/input requests */
  interruptData?: InterruptData;
  /** Whether the interrupt has been responded to */
  interruptResolved?: boolean;
  /** A2UI surface ID for rendering interactive surfaces */
  a2uiSurfaceId?: string;
  /** Whether the A2UI surface is ready for rendering */
  a2uiReady?: boolean;
  /** Report URL for report-type messages */
  reportUrl?: string;
  /** Report title for report-type messages */
  reportTitle?: string;
}

export interface SessionInfo {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  sleep_status?: 'awake' | 'sleeping';
  memory_status?: 'consolidated' | 'unconsolidated';
  auto_consolidate?: boolean;
  last_active_at?: string;
}

interface ChatState {
  sessionId: string | null;
  sessions: SessionInfo[];
  messages: ChatMessage[];
  isRunning: boolean;
  loadingHistory: boolean;
  refreshSessions: () => void;
  /** bumped after each session change, sidebar watches this to refetch */
  _refreshTick: number;
  /** bumped after each file upload/delete/rename, InputBar watches this to refresh @mention list */
  _fileRefreshTick: number;
  bumpFileRefresh: () => void;
  setSessionId: (id: string | null) => void;
  setSessions: (sessions: SessionInfo[]) => void;
  setMessages: (messages: ChatMessage[]) => void;
  setLoadingHistory: (loading: boolean) => void;
  addMessage: (msg: ChatMessage) => void;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
  addExecutionStep: (msgId: string, step: ExecutionStep) => void;
  updateExecutionStep: (msgId: string, stepId: string, updates: Partial<ExecutionStep>) => void;
  setIsRunning: (running: boolean) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  sessionId: null,
  sessions: [],
  messages: [],
  isRunning: false,
  loadingHistory: false,
  _refreshTick: 0,
  _fileRefreshTick: 0,
  refreshSessions: () => set((s) => ({ _refreshTick: s._refreshTick + 1 })),
  bumpFileRefresh: () => set((s) => ({ _fileRefreshTick: s._fileRefreshTick + 1 })),
  setSessionId: (id) => set({ sessionId: id }),
  setSessions: (sessions) => set({ sessions }),
  setMessages: (messages) => set({ messages }),
  setLoadingHistory: (loading) => set({ loadingHistory: loading }),
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  updateMessage: (id, updates) =>
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, ...updates } : m)),
    })),
  addExecutionStep: (msgId, step) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === msgId ? { ...m, executionSteps: [...(m.executionSteps || []), step] } : m,
      ),
    })),
  updateExecutionStep: (msgId, stepId, updates) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === msgId
          ? {
              ...m,
              executionSteps: (m.executionSteps || []).map((st) =>
                st.id === stepId ? { ...st, ...updates } : st,
              ),
            }
          : m,
      ),
    })),
  setIsRunning: (running) => set({ isRunning: running }),
}));
