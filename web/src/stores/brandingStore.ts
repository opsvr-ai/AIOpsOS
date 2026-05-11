import { create } from 'zustand';
import api from '@/services/api';

interface BrandingData {
  logo_url: string;
  favicon_url: string;
  company_name: string;
  primary_color: string;
}

interface BrandingState {
  branding: BrandingData;
  loading: boolean;
  loaded: boolean;
  fetchBranding: () => Promise<void>;
  updateBranding: (data: Partial<BrandingData>) => void;
}

const DEFAULT_BRANDING: BrandingData = {
  logo_url: '',
  favicon_url: '',
  company_name: 'AIOpsOS',
  primary_color: '#1677ff',
};

export const useBrandingStore = create<BrandingState>()((set, get) => ({
  branding: DEFAULT_BRANDING,
  loading: false,
  loaded: false,

  fetchBranding: async () => {
    if (get().loaded) return;
    set({ loading: true });
    try {
      const res = await api.get('/system/branding');
      set({
        branding: { ...DEFAULT_BRANDING, ...res.data },
        loaded: true,
      });
    } catch {
      // Use defaults on error
      set({ loaded: true });
    } finally {
      set({ loading: false });
    }
  },

  updateBranding: (data: Partial<BrandingData>) => {
    set((state) => ({
      branding: { ...state.branding, ...data },
    }));
  },
}));
