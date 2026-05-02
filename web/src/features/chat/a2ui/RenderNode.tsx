import React from 'react';
import type { ResolvedComponent } from './types';
import { getA2UIComponent } from './registry';

export function RenderA2UINode({ node, surfaceId }: { node: ResolvedComponent; surfaceId: string }) {
  const Comp = getA2UIComponent(node.type);
  if (!Comp) {
    return React.createElement('div', {
      style: { color: '#999', fontSize: 12, padding: 8 },
    }, `[Unknown: ${node.type}]`);
  }
  return React.createElement(Comp, { node, surfaceId });
}
