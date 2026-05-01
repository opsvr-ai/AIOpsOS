import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface SpaceInfo {
  id: string;
  name: string;
  my_role: string;
}

interface SpaceState {
  currentSpace: SpaceInfo | null;
  setCurrentSpace: (space: SpaceInfo | null) => void;
  spaceVersion: number;
  bumpSpaceVersion: () => void;
}

export const useSpaceStore = create<SpaceState>()(
  persist(
    (set) => ({
      currentSpace: null,
      setCurrentSpace: (space) => set({ currentSpace: space }),
      spaceVersion: 0,
      bumpSpaceVersion: () => set((s) => ({ spaceVersion: s.spaceVersion + 1 })),
    }),
    { name: 'aiopsos-space' },
  ),
);
