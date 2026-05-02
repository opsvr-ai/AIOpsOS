import { useSyncExternalStore, useCallback } from 'react';
import { useA2UIContext } from './A2UIProvider';
import { resolveDataPath } from './processor';
import type { Surface } from './types';

export function useA2UISurface(surfaceId: string) {
  const { processor, onAction } = useA2UIContext();

  const surface = useSyncExternalStore(
    useCallback(
      (cb: () => void) => {
        return processor.subscribe(() => {
          cb();
        });
      },
      [processor, surfaceId],
    ),
    () => processor.getSurface(surfaceId),
  );

  const dispatch = useCallback(
    (name: string, context: Record<string, unknown> = {}) => {
      onAction?.({
        surfaceId,
        name,
        sourceComponentId: 'root',
        context,
        timestamp: Date.now(),
      });
    },
    [surfaceId, onAction],
  );

  const getValue = useCallback(
    (path: string): unknown => {
      if (!surface || !path) return undefined;
      return resolveDataPath(surface.dataModel, path);
    },
    [surface],
  );

  return { surface, dispatch, getValue };
}

export type { Surface };
