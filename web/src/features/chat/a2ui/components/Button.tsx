import React from 'react';
import { Button as AntButton } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import { RenderA2UINode } from '../RenderNode';

export const A2UIButton = React.memo(function A2UIButton({ node, surfaceId }: A2UIComponentProps) {
  const action = node.properties.action as
    { event?: { name: string; context?: Record<string, unknown> } } | undefined;
  const primary = node.properties.primary as boolean | undefined;
  const { dispatch } = useA2UISurface(surfaceId);

  const handleClick = () => {
    if (action?.event) {
      dispatch(action.event.name, action.event.context || {});
    }
  };

  const child = node.children[0];
  return React.createElement(AntButton, {
    type: primary ? 'primary' : 'default',
    onClick: handleClick,
    style: { borderRadius: 6 },
  }, child
    ? React.createElement(RenderA2UINode, { node: child, surfaceId })
    : 'Submit'
  );
});
