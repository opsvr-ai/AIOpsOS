import React from 'react';
import { Progress } from 'antd';
import type { A2UIComponentProps } from '../registry';
import { useA2UISurface } from '../useA2UISurface';
import type { PathValue } from '../types';

export const A2UIProgressBar = React.memo(function A2UIProgressBar({ node, surfaceId }: A2UIComponentProps) {
  const label = (node.properties.label as string) || '';
  const valuePath = node.properties.value as PathValue | undefined;
  const { getValue } = useA2UISurface(surfaceId);

  const percent = valuePath ? Number(getValue(valuePath.path) ?? 0) : 0;

  return React.createElement('div', { style: { marginBottom: 12 } },
    label && React.createElement('label', {
      style: { display: 'block', marginBottom: 4, fontSize: 13, fontWeight: 500 },
    }, `${label} (${percent}%)`),
    React.createElement(Progress, { percent, strokeColor: '#1677ff' })
  );
});
