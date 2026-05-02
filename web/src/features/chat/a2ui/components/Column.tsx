import React from 'react';
import type { A2UIComponentProps } from '../registry';
import { RenderA2UINode } from '../RenderNode';

export const A2UIColumn = React.memo(function A2UIColumn({ node, surfaceId }: A2UIComponentProps) {
  const align = (node.properties.align as string) || 'stretch';
  const gap = (node.properties.gap as number) ?? 12;

  return React.createElement('div', {
    style: {
      display: 'flex',
      flexDirection: 'column',
      alignItems: align === 'center' ? 'center' : align === 'end' ? 'flex-end' : 'stretch',
      gap,
    },
  }, node.children.map((child) =>
    React.createElement(RenderA2UINode, { key: child.id, node: child, surfaceId })
  ));
});
