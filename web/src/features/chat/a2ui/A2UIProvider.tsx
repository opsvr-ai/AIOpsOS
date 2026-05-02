import React, { createContext, useContext, useMemo } from 'react';
import { getSharedProcessor } from './sharedProcessor';
import type { A2UIMessage, A2UIClientEvent, Surface } from './types';
import type { A2UIMessageProcessor } from './processor';

interface A2UIContextValue {
  processMessages: (messages: A2UIMessage[]) => void;
  getSurface: (surfaceId: string) => Surface | undefined;
  getSurfaces: () => Surface[];
  processor: A2UIMessageProcessor;
  onAction?: (event: A2UIClientEvent) => void;
}

const A2UIContext = createContext<A2UIContextValue | null>(null);

export function A2UIProvider({
  onAction,
  children,
}: {
  onAction?: (event: A2UIClientEvent) => void;
  children: React.ReactNode;
}) {
  const processor = useMemo(() => getSharedProcessor(), []);
  const onActionRef = React.useRef(onAction);
  onActionRef.current = onAction;

  const value: A2UIContextValue = useMemo(
    () => ({
      processor,
      processMessages: (msgs) => processor.processMessages(msgs),
      getSurface: (sid) => processor.getSurface(sid),
      getSurfaces: () => processor.getSurfaces(),
      onAction: (evt) => onActionRef.current?.(evt),
    }),
    [processor],
  );

  return React.createElement(A2UIContext.Provider, { value }, children);
}

export function useA2UIContext() {
  const ctx = useContext(A2UIContext);
  if (!ctx) throw new Error('useA2UIContext must be inside A2UIProvider');
  return ctx;
}
