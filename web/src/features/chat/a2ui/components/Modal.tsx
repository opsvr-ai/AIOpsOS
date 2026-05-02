import React from 'react';
import { Modal as AntModal } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { RenderA2UINode } from '../RenderNode';
import { useA2UISurface } from '../useA2UISurface';

export const A2UIModal = React.memo(function A2UIModal({ node, surfaceId }: A2UIComponentProps) {
  const title = (node.properties.title as string) || '';
  const { dispatch } = useA2UISurface(surfaceId);

  return React.createElement(AntModal, {
    title,
    open: true,
    onCancel: () => dispatch('modal_close', {}),
    footer: null,
    width: (node.properties.width as number) || 520,
  }, node.children.map((child) =>
    React.createElement(RenderA2UINode, { key: child.id, node: child, surfaceId })
  ));
});
