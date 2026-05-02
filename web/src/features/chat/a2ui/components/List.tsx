import React from 'react';
import { List as AntList } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { RenderA2UINode } from '../RenderNode';

export const A2UIList = React.memo(function A2UIList({ node, surfaceId }: A2UIComponentProps) {
  const items = node.children.map((child) => ({
    key: child.id,
    children: React.createElement(RenderA2UINode, { node: child, surfaceId }),
  }));

  const bordered = node.properties.bordered as boolean | undefined;
  const size = (node.properties.size as string) || 'small';

  return React.createElement(AntList, {
    size: size as 'small' | 'default' | 'large',
    bordered,
    dataSource: [items],
    renderItem: () => React.createElement(React.Fragment, null,
      ...items.map((item) => item.children)
    ),
  });
});

// Alternative simpler implementation for template-driven lists
export function A2UIListSimple({ node, surfaceId }: A2UIComponentProps) {
  return React.createElement('div', {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
    },
  }, node.children.map((child) =>
    React.createElement('div', { key: child.id },
      React.createElement(RenderA2UINode, { node: child, surfaceId })
    )
  ));
}
