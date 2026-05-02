import React from 'react';
import type { A2UIComponentProps } from '../registry';
import { RenderA2UINode } from '../RenderNode';

export const A2UIRow = React.memo(function A2UIRow({ node, surfaceId }: A2UIComponentProps) {
  const align = (node.properties.align as string) || 'start';
  const distribution = (node.properties.distribution as string) || 'start';
  const gap = (node.properties.gap as number) ?? 12;

  const justifyMap: Record<string, string> = {
    start: 'flex-start', center: 'center', end: 'flex-end',
    spaceBetween: 'space-between', spaceAround: 'space-around', spaceEvenly: 'space-evenly',
  };

  return React.createElement('div', {
    style: {
      display: 'flex',
      flexDirection: 'row',
      alignItems: align,
      justifyContent: justifyMap[distribution] || 'flex-start',
      gap,
      flexWrap: 'wrap',
    },
  }, node.children.map((child) =>
    React.createElement(RenderA2UINode, { key: child.id, node: child, surfaceId })
  ));
});
