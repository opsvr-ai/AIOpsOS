import { create } from 'zustand';
import api from '@/services/api';

export interface MemoryEntry {
  id: string;
  title: string;
  content: string;
  scope: 'personal' | 'team';
  session_id: string;
  session_title?: string;
  tags: string[];
  created_at: string;
}

export interface GraphNode {
  id: string;
  type: 'memory' | 'tag';
  label: string;
  scope?: 'personal' | 'team';
  tags?: string[];
  count?: number;
  sessionId?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
}

export interface TagCount {
  name: string;
  count: number;
}

interface MemoryState {
  memories: MemoryEntry[];
  loading: boolean;
  scope: 'personal' | 'team';
  query: string;
  offset: number;
  limit: number;
  hasMore: boolean;
  tags: string;
  sortBy: string;
  graphData: { nodes: GraphNode[]; edges: GraphEdge[] } | null;
  graphLoading: boolean;
  allTags: TagCount[];
  selectedTag: string;
  selectedMemoryId: string;
  viewMode: 'graph' | 'list';
  fetchMemories: (opts?: {
    scope?: string;
    query?: string;
    sessionId?: string;
    offset?: number;
    tags?: string;
    sortBy?: string;
  }) => Promise<void>;
  loadMore: () => Promise<void>;
  deleteMemory: (id: string) => Promise<void>;
  summarizeSession: (sessionId: string) => Promise<{ ok: boolean; personal: number; team: number }>;
  fetchGraph: (opts?: { scope?: string; tag?: string; memoryId?: string }) => Promise<void>;
  fetchTags: (scope?: string) => Promise<void>;
  fetchRelatedMemories: (memoryId: string) => Promise<MemoryEntry[]>;
  focusTag: (tag: string) => void;
  clearFocus: () => void;
  setViewMode: (mode: 'graph' | 'list') => void;
}

const PAGE_SIZE = 20;

export const useMemoryStore = create<MemoryState>((set, get) => ({
  memories: [],
  loading: false,
  scope: 'personal',
  query: '',
  offset: 0,
  limit: PAGE_SIZE,
  hasMore: false,
  tags: '',
  sortBy: 'created_at',
  graphData: null,
  graphLoading: false,
  allTags: [],
  selectedTag: '',
  selectedMemoryId: '',
  viewMode: 'list',

  fetchMemories: async (opts) => {
    const s = opts?.scope ?? get().scope;
    const q = opts?.query ?? '';
    const offset = opts?.offset ?? 0;
    const tags = opts?.tags ?? '';
    const sortBy = opts?.sortBy ?? 'created_at';
    const sessionId = opts?.sessionId;

    set({ loading: true, scope: s as 'personal' | 'team', query: q, offset, tags, sortBy });
    try {
      const params: Record<string, string> = {
        scope: s,
        q,
        limit: String(PAGE_SIZE),
        offset: String(offset),
        sort_by: sortBy,
      };
      if (sessionId) params.session_id = sessionId;
      if (tags) params.tags = tags;
      const resp = await api.get('/memories', { params });
      const data: MemoryEntry[] = resp.data;
      set({
        memories: offset === 0 ? data : [...get().memories, ...data],
        hasMore: data.length === PAGE_SIZE,
        loading: false,
      });
    } catch {
      set({ loading: false });
    }
  },

  loadMore: async () => {
    const { offset, limit } = get();
    await get().fetchMemories({ offset: offset + limit });
  },

  deleteMemory: async (id: string) => {
    await api.delete(`/memories/${id}`);
    set((s) => ({ memories: s.memories.filter((m) => m.id !== id) }));
  },

  summarizeSession: async (sessionId: string) => {
    const resp = await api.post(`/sessions/${sessionId}/summarize`);
    return resp.data;
  },

  fetchGraph: async (opts) => {
    set({ graphLoading: true });
    try {
      const params: Record<string, string> = {
        scope: opts?.scope ?? get().scope,
        limit: '200',
      };
      if (opts?.tag) params.tag = opts.tag;
      if (opts?.memoryId) params.memory_id = opts.memoryId;
      const resp = await api.get('/memories/graph', { params });
      set({ graphData: resp.data, graphLoading: false });
    } catch {
      set({ graphLoading: false });
    }
  },

  fetchTags: async (scope) => {
    try {
      const params: Record<string, string> = { scope: scope ?? get().scope };
      const resp = await api.get('/memories/tags', { params });
      set({ allTags: resp.data ?? [] });
    } catch {
      /* ignore */
    }
  },

  fetchRelatedMemories: async (memoryId: string) => {
    try {
      const resp = await api.get(`/memories/${memoryId}/related`);
      return resp.data ?? [];
    } catch {
      return [];
    }
  },

  focusTag: (tag: string) => {
    set({ selectedTag: tag, selectedMemoryId: '' });
    get().fetchGraph({ tag });
  },

  clearFocus: () => {
    set({ selectedTag: '', selectedMemoryId: '' });
    get().fetchGraph({ scope: get().scope });
  },

  setViewMode: (mode: 'graph' | 'list') => {
    set({ viewMode: mode });
  },
}));
