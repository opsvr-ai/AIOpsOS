import React from 'react';
import { Tag as AntTag } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

const TAG_COLORS: Record<string, string> = {
  success: 'green', error: 'red', warning: 'orange', info: 'blue',
  default: 'default', processing: 'processing',
};

export const A2UITag = React.memo(function A2UITag({ node, surfaceId }: A2UIComponentProps) {
  const label = node.properties.label as string | undefined;
  const textPath = node.properties.text as PathValue | undefined;
  const colorKey = (node.properties.color as string) || 'default';
  const { getValue } = useA2UISurface(surfaceId);

  const text = label || (textPath ? String(getValue(textPath.path) ?? '') : '');
  const color = TAG_COLORS[colorKey] || 'default';

  return React.createElement(AntTag, { color }, text);
});
