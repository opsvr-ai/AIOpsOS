import { create } from 'zustand';
import api from '@/services/api';

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
      const { data } = await api.get('/docs');
      set({ categories: data.categories ?? [], loading: false });
    } catch {
      set({ loading: false });
    }
  },

  fetchContent: async (path: string) => {
    set({ contentLoading: true, activeFile: path });
    try {
      const { data } = await api.get(`/docs/${path}`, { responseType: 'text' });
      set({ content: data, contentLoading: false });
    } catch {
      set({ content: '# 加载失败\n\n文档内容加载失败，请稍后重试。', contentLoading: false });
    }
  },

  setActiveFile: (path: string) => {
    set({ activeFile: path });
  },
}));
