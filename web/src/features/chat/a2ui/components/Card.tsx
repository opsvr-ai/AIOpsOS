import React from 'react';
import { Card as AntCard } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { RenderA2UINode } from '../RenderNode';

export const A2UICard = React.memo(function A2UICard({ node, surfaceId }: A2UIComponentProps) {
  return React.createElement(AntCard, {
    size: 'small',
    style: { marginBottom: 12 },
  }, node.children.map((child) =>
    React.createElement(RenderA2UINode, { key: child.id, node: child, surfaceId })
  ));
});
