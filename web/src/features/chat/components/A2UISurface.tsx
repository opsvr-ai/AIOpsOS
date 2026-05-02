import React, { useMemo } from 'react';
import { useA2UISurface } from '../a2ui/useA2UISurface';
import { useA2UIContext } from '../a2ui/A2UIProvider';
import { RenderA2UINode } from '../a2ui/RenderNode';

export default function A2UISurface({ surfaceId }: { surfaceId: string }) {
  const { surface } = useA2UISurface(surfaceId);
  const { processor } = useA2UIContext();

  const tree = useMemo(() => {
    if (!surface || !surface.rootId) return null;
    return processor.resolveTree(surfaceId);
  }, [surface, surfaceId, processor]);

  if (!tree) {
    return React.createElement('div', {
      style: { padding: 16, color: '#999', fontSize: 14, textAlign: 'center' },
    }, 'Loading interactive form...');
  }

  return React.createElement('div', {
    className: 'a2ui-surface',
    style: { padding: 12 },
  }, React.createElement(RenderA2UINode, { node: tree, surfaceId }));
}
