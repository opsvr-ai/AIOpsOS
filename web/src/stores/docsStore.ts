import { create } from 'zustand';

export interface DocFile {
  name: string;
  title: string;
  path: string;
}

export interface DocCategory {
  key: string;
  label: string;
  files: DocFile[];
}

interface DocsState {
  categories: DocCategory[];
  loading: boolean;
  activeFile: string | null;
  content: string;
  contentLoading: boolean;
  fetchDocs: () => Promise<void>;
  fetchContent: (path: string) => Promise<void>;
  setActiveFile: (path: string) => void;
}

export const useDocsStore = create<DocsState>((set) => ({
  categories: [],
  loading: false,
  activeFile: null,
  content: '',
  contentLoading: false,

  fetchDocs: async () => {
    set({ loading: true });
    try {
      const token = localStorage.getItem('token');
      const res = await fetch('/api/v1/docs', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error('Failed to fetch docs');
      const data = await res.json();
      set({ categories: data.categories ?? [], loading: false });
    } catch {
      set({ loading: false });
    }
  },

  fetchContent: async (path: string) => {
    set({ contentLoading: true, activeFile: path });
    try {
      const token = localStorage.getItem('token');
      const res = await fetch(`/api/v1/docs/${path}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error('Failed to fetch content');
      const text = await res.text();
      set({ content: text, contentLoading: false });
    } catch {
      set({ content: '# 加载失败\n\n文档内容加载失败，请稍后重试。', contentLoading: false });
    }
  },

  setActiveFile: (path: string) => {
    set({ activeFile: path });
  },
}));
